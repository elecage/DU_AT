#include "wifi_manager.h"
#include "wifi_portal.h"

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

ESP_EVENT_DEFINE_BASE(CARECALL_WIFI_INTERNAL);

namespace {
using Phase = CarecallWifiPhase;
constexpr char TAG[] = "WIFI_SETUP";
constexpr char NVS_NAMESPACE[] = "carecall_wifi";
constexpr char NVS_KEY[] = "profile_v1";
constexpr std::uint32_t PROFILE_VERSION = 0xcc570001;
constexpr EventBits_t DRAINED = BIT0;
constexpr std::int64_t SAVED_TIMEOUT_MS = 90000;
constexpr std::int64_t TEST_TIMEOUT_MS = 75000;

struct StoredProfile {
    std::uint32_t version;
    CarecallWifiCredentials credentials;
    unsigned char reserved[3];
};
static_assert(sizeof(StoredProfile) == 104, "Wi-Fi storage format changed");
enum class Action { Test, Save, Resume };
struct Request { Action action; CarecallWifiCredentials credentials; };

SemaphoreHandle_t state_mutex = nullptr;
QueueHandle_t requests = nullptr;
EventGroupHandle_t barriers = nullptr;
esp_netif_t* sta_netif = nullptr;
esp_netif_t* ap_netif = nullptr;
std::atomic<bool> associated{false}, has_ip{false}, connected{false};
std::atomic<unsigned> disconnect_reason{0};
CarecallWifiSetupStatus status{Phase::Starting, false, false, false, true, 0, "STARTING"};
bool initialized = false, radio_started = false, has_saved = false;
std::atomic<bool> storage_usable{true};
bool active_valid = false, boot_setup = false;
CarecallWifiCredentials saved{}, fallback{}, active{};
char ap_ssid[33]{};
std::int64_t last_connect = 0;

std::int64_t now_ms() { return esp_timer_get_time() / 1000; }

void set_phase(Phase phase, const char* notice)
{
    xSemaphoreTake(state_mutex, portMAX_DELAY);
    status.phase = phase;
    status.notice = notice;
    status.profile_saved = has_saved;
    xSemaphoreGive(state_mutex);
    ESP_LOGI(TAG, "STATE=%d NOTICE=%s PROFILE_SAVED=%s", static_cast<int>(phase), notice, has_saved ? "YES" : "NO");
}

void event_handler(void*, esp_event_base_t base, std::int32_t id, void* data)
{
    if (base == CARECALL_WIFI_INTERNAL) { xEventGroupSetBits(barriers, DRAINED); return; }
    if (base == WIFI_EVENT) {
        if (id == WIFI_EVENT_STA_CONNECTED) associated.store(true);
        if (id == WIFI_EVENT_STA_DISCONNECTED || id == WIFI_EVENT_STA_STOP) {
            associated.store(false); has_ip.store(false); connected.store(false);
            if (id == WIFI_EVENT_STA_DISCONNECTED && data)
                disconnect_reason.store(static_cast<wifi_event_sta_disconnected_t*>(data)->reason);
        }
    }
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) has_ip.store(true);
    if (base == IP_EVENT && id == IP_EVENT_STA_LOST_IP) { has_ip.store(false); connected.store(false); }
}

esp_err_t read_saved()
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (result != ESP_OK) return result;
    StoredProfile profile{};
    std::size_t size = sizeof(profile);
    result = nvs_get_blob(handle, NVS_KEY, &profile, &size);
    nvs_close(handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) result = ESP_OK;
    else if (result == ESP_OK) {
        if (size != sizeof(profile) || profile.version != PROFILE_VERSION ||
            !carecall_wifi_credentials_valid(profile.credentials)) result = ESP_ERR_INVALID_STATE;
        else { saved = profile.credentials; has_saved = true; }
    }
    carecall_wifi_clear(&profile, sizeof(profile));
    return result;
}

esp_err_t write_saved(const CarecallWifiCredentials& credentials)
{
    if (!storage_usable || !carecall_wifi_credentials_valid(credentials)) return ESP_ERR_INVALID_STATE;
    StoredProfile profile{};
    profile.version = PROFILE_VERSION;
    profile.credentials = credentials;
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (result == ESP_OK) {
        result = nvs_set_blob(handle, NVS_KEY, &profile, sizeof(profile));
        if (result == ESP_OK) result = nvs_commit(handle);
        nvs_close(handle);
    }
    carecall_wifi_clear(&profile, sizeof(profile));
    // A failed write may have taken effect. Do not keep writing a stale snapshot.
    if (result != ESP_OK) storage_usable = false;
    return result;
}

const CarecallWifiCredentials* previous_profile()
{
    if (has_saved) return &saved;
    return carecall_wifi_credentials_valid(fallback) ? &fallback : nullptr;
}

esp_err_t restart_radio(const CarecallWifiCredentials* profile, bool with_ap)
{
    carecall_wifi_portal_stop();
    xSemaphoreTake(state_mutex, portMAX_DELAY); status.ap_active = false; xSemaphoreGive(state_mutex);
    connected.store(false);
    if (radio_started) {
        esp_err_t result = esp_wifi_stop();
        if (result != ESP_OK) return result;
        radio_started = false;
        // Drain stop/IP events before changing the identity of the active profile.
        xEventGroupClearBits(barriers, DRAINED);
        result = esp_event_post(CARECALL_WIFI_INTERNAL, 1, nullptr, 0, pdMS_TO_TICKS(1000));
        if (result != ESP_OK) return result;
        if (!(xEventGroupWaitBits(barriers, DRAINED, pdTRUE, pdTRUE, pdMS_TO_TICKS(5000)) & DRAINED))
            return ESP_ERR_TIMEOUT;
    }
    associated.store(false); has_ip.store(false); disconnect_reason.store(0);
    active_valid = profile != nullptr;
    CarecallWifiCredentials next{};
    if (profile) next = *profile;  // profile may point at active.
    carecall_wifi_clear(&active, sizeof(active));
    active = next;
    carecall_wifi_clear(&next, sizeof(next));
    if (!with_ap && !active_valid) with_ap = true;
    esp_err_t result = esp_wifi_set_mode(with_ap ? (active_valid ? WIFI_MODE_APSTA : WIFI_MODE_AP) : WIFI_MODE_STA);
    if (result != ESP_OK) return result;
    if (active_valid) {
        wifi_config_t cfg{};
        std::memcpy(cfg.sta.ssid, active.ssid, std::strlen(active.ssid));
        std::memcpy(cfg.sta.password, active.password, std::strlen(active.password));
        cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
        cfg.sta.pmf_cfg.capable = true;
        cfg.sta.pmf_cfg.required = false;
        result = esp_wifi_set_config(WIFI_IF_STA, &cfg);
        carecall_wifi_clear(&cfg, sizeof(cfg));
        if (result != ESP_OK) return result;
    }
    if (with_ap) {
        wifi_config_t cfg{};
        std::memcpy(cfg.ap.ssid, ap_ssid, std::strlen(ap_ssid));
        cfg.ap.ssid_len = std::strlen(ap_ssid);
        std::memcpy(cfg.ap.password, CONFIG_CARECALL_SETUP_AP_PASSWORD, std::strlen(CONFIG_CARECALL_SETUP_AP_PASSWORD));
        cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
        cfg.ap.channel = 1;
        cfg.ap.max_connection = 2;
        cfg.ap.beacon_interval = 200;
        result = esp_wifi_set_config(WIFI_IF_AP, &cfg);
        carecall_wifi_clear(&cfg, sizeof(cfg));
        if (result != ESP_OK) return result;
    }
    result = esp_wifi_start();
    if (result != ESP_OK) return result;
    radio_started = true;
    result = esp_wifi_set_max_tx_power(with_ap ? 28 : 34);  // Setup AP 7 dBm; STA-only 8.5 dBm.
    if (result != ESP_OK) return result;
    result = esp_wifi_set_ps(with_ap ? WIFI_PS_NONE : WIFI_PS_MIN_MODEM);
    if (result != ESP_OK) return result;
    if (with_ap) {
        xSemaphoreTake(state_mutex, portMAX_DELAY); status.ap_active = true; xSemaphoreGive(state_mutex);
        result = carecall_wifi_portal_start();
        if (result != ESP_OK) {
            xSemaphoreTake(state_mutex, portMAX_DELAY); status.ap_active = false; xSemaphoreGive(state_mutex);
            return result;
        }
        ESP_LOGI(TAG, "SETUP_AP=%s URL=http://%s/", ap_ssid, CARECALL_WIFI_SETUP_HOST);
    }
    last_connect = now_ms();
    if (active_valid) {
        result = esp_wifi_connect();
        if (result != ESP_OK) ESP_LOGW(TAG, "CONNECT_REQUEST=%s", esp_err_to_name(result));
    }
    return ESP_OK;
}

bool station_ready(bool& subnet_conflict)
{
    subnet_conflict = false;
    if (!active_valid || !associated.load() || !has_ip.load()) return false;
    wifi_ap_record_t record{};
    esp_netif_ip_info_t info{};
    if (esp_wifi_sta_get_ap_info(&record) != ESP_OK || esp_netif_get_ip_info(sta_netif, &info) != ESP_OK) return false;
    const std::size_t length = std::strlen(active.ssid);
    if (std::memcmp(record.ssid, active.ssid, length) != 0 || (length < 32 && record.ssid[length] != 0)) return false;
    if (!info.ip.addr || !info.gw.addr || !info.netmask.addr) return false;
    if ((info.ip.addr & info.netmask.addr) != (info.gw.addr & info.netmask.addr)) return false;
    const std::uint32_t setup_ip = inet_addr(CARECALL_WIFI_SETUP_IP);
    const std::uint32_t setup_mask = inet_addr("255.255.255.0");
    subnet_conflict = ((info.ip.addr ^ setup_ip) & info.netmask.addr & setup_mask) == 0;
    return !subnet_conflict;
}

void reconnect_if_needed()
{
    if (active_valid && !associated.load() && now_ms() - last_connect >= 5000) {
        last_connect = now_ms();
        esp_wifi_connect();  // Driver can already be connecting; bounded retries only here.
    }
}

void worker(void*)
{
    Phase phase = (boot_setup || !has_saved) ? Phase::Setup : Phase::ConnectingSaved;
    set_phase(phase, storage_usable ? (has_saved ? "CONNECTING" : "FIRST_SETUP") : "STORAGE_ERROR");
    esp_err_t result = restart_radio(previous_profile(), phase == Phase::Setup);
    if (result != ESP_OK) { phase = Phase::Error; set_phase(phase, "RADIO_ERROR"); }
    std::int64_t phase_started = now_ms(), offline_since = now_ms(), stable_since = 0;
    bool pending = false;
    for (;;) {
        bool conflict = false;
        const bool ready = station_ready(conflict);
        connected.store(ready);
        reconnect_if_needed();
        Request request{};
        if (xQueueReceive(requests, &request, 0) == pdTRUE) {
            vTaskDelay(pdMS_TO_TICKS(800));  // Let the HTTP response leave before radio changes.
            stable_since = 0;
            if (request.action == Action::Test) {
                pending = true;
                phase = Phase::Testing; set_phase(phase, "TESTING");
                result = restart_radio(&request.credentials, true);
            } else if (request.action == Action::Save) {
                bool current_conflict = false;
                if (!pending || !station_ready(current_conflict)) {
                    pending = false; phase = Phase::Setup; set_phase(phase, "TEST_CONNECTION_LOST");
                    result = restart_radio(previous_profile(), true);
                } else {
                    phase = Phase::Saving; set_phase(phase, "SAVING");
                    result = write_saved(active);
                    if (result == ESP_OK) {
                        saved = active; has_saved = true; pending = false;
                        phase = Phase::Saved; set_phase(phase, "SAVED");
                        ESP_LOGI(TAG, "WIFI_PROFILE_COMMITTED=YES");
                    } else {
                        phase = Phase::SaveUncertain; set_phase(phase, "SAVE_UNCERTAIN_REBOOT");
                        ESP_LOGE(TAG, "PROFILE_SAVE=%s", esp_err_to_name(result));
                        result = ESP_OK;  // Keep portal accessible; prohibit more writes until reboot.
                    }
                }
            } else {
                pending = false;
                phase = Phase::ConnectingSaved; set_phase(phase, "CONNECTING");
                result = restart_radio(previous_profile(), false);
            }
            carecall_wifi_clear(&request, sizeof(request));
            phase_started = offline_since = now_ms();
            if (result != ESP_OK) { phase = Phase::Error; set_phase(phase, "RADIO_ERROR"); }
            continue;
        }
        const auto time = now_ms();
        if (phase == Phase::Testing) {
            if (ready) {
                if (!stable_since) stable_since = time;
                if (time - stable_since >= 2000) { phase = Phase::TestOk; set_phase(phase, "TEST_OK"); }
            } else stable_since = 0;
            if (conflict || (phase == Phase::Testing && time - phase_started >= TEST_TIMEOUT_MS)) {
                pending = false; phase = Phase::Setup;
                set_phase(phase, conflict ? "SUBNET_CONFLICT" : "TEST_TIMEOUT");
                result = restart_radio(previous_profile(), true);
            }
        } else if (phase == Phase::TestOk && !ready) {
            phase = Phase::Testing; phase_started = time; stable_since = 0;
            set_phase(phase, "TESTING");
        } else if (phase == Phase::Saved && time - phase_started >= 4000) {
            phase = Phase::ConnectingSaved; set_phase(phase, "CONNECTING");
            result = restart_radio(previous_profile(), false); phase_started = offline_since = now_ms();
        } else if (phase == Phase::ConnectingSaved) {
            if (ready) { phase = Phase::Online; offline_since = 0; set_phase(phase, "ONLINE"); }
            else if (conflict || time - phase_started >= SAVED_TIMEOUT_MS) {
                phase = Phase::Setup; set_phase(phase, conflict ? "SUBNET_CONFLICT" : "SAVED_UNAVAILABLE");
                result = restart_radio(previous_profile(), true);
            }
        } else if (phase == Phase::Online) {
            if (ready) offline_since = 0;
            else {
                if (!offline_since) offline_since = time;
                if (time - offline_since >= SAVED_TIMEOUT_MS) {
                    phase = Phase::Setup; set_phase(phase, "SAVED_UNAVAILABLE");
                    result = restart_radio(previous_profile(), true);
                }
            }
        } else if (phase == Phase::Error && time - phase_started >= 10000) {
            phase = Phase::Setup; set_phase(phase, "RECOVERED");
            result = restart_radio(previous_profile(), true); phase_started = now_ms();
        }
        if (result != ESP_OK) { phase = Phase::Error; phase_started = now_ms(); set_phase(phase, "RADIO_ERROR"); result = ESP_OK; }
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

esp_err_t enqueue(Action action, const CarecallWifiCredentials* candidate)
{
    if (!initialized) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(state_mutex, portMAX_DELAY);
    const bool idle = status.phase == Phase::Setup || status.phase == Phase::TestOk;
    const bool allowed = status.ap_active && idle &&
        (action != Action::Save || (status.phase == Phase::TestOk && connected.load() && storage_usable));
    if (!allowed) { xSemaphoreGive(state_mutex); return ESP_ERR_INVALID_STATE; }
    Request request{}; request.action = action;
    if (candidate) request.credentials = *candidate;
    const bool queued = xQueueSend(requests, &request, 0) == pdTRUE;
    carecall_wifi_clear(&request, sizeof(request));
    if (queued) { status.phase = Phase::Queued; status.notice = "REQUEST_ACCEPTED"; }
    xSemaphoreGive(state_mutex);
    return queued ? ESP_OK : ESP_ERR_NO_MEM;
}
}

esp_err_t wifi_manager_init()
{
    if (initialized) return ESP_OK;
    const auto ap_password_size = std::strlen(CONFIG_CARECALL_SETUP_AP_PASSWORD);
    if (ap_password_size < 16 || ap_password_size > 63) return ESP_ERR_INVALID_ARG;
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(CONFIG_CARECALL_SETUP_AP_PASSWORD); *p; ++p)
        if (!((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9'))) return ESP_ERR_INVALID_ARG;
    esp_err_t result = nvs_flash_init();  // Never erase the existing calls namespace.
    if (result != ESP_OK) return result;
    result = read_saved();
    if (result != ESP_OK) { storage_usable = false; ESP_LOGE(TAG, "PROFILE_READ=%s", esp_err_to_name(result)); }
    if (std::strlen(CONFIG_CARECALL_WIFI_SSID) < sizeof(fallback.ssid) &&
        std::strlen(CONFIG_CARECALL_WIFI_PASSWORD) < sizeof(fallback.password)) {
        std::strcpy(fallback.ssid, CONFIG_CARECALL_WIFI_SSID);
        std::strcpy(fallback.password, CONFIG_CARECALL_WIFI_PASSWORD);
    }
    // GPIO4 is already configured by the unchanged button driver. Hold it from
    // power-on for 3 seconds to request setup. Normal runtime presses keep their role.
    if (gpio_get_level(GPIO_NUM_4) == 0) {
        boot_setup = true;
        for (int i = 0; i < 60; ++i) {
            vTaskDelay(pdMS_TO_TICKS(50));
            if (gpio_get_level(GPIO_NUM_4) != 0) { boot_setup = false; break; }
        }
    }
    state_mutex = xSemaphoreCreateMutex();
    barriers = xEventGroupCreate();
    requests = xQueueCreate(1, sizeof(Request));
    if (!state_mutex || !barriers || !requests) return ESP_ERR_NO_MEM;
    result = esp_netif_init(); if (result != ESP_OK) return result;
    result = esp_event_loop_create_default();
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) return result;
    sta_netif = esp_netif_create_default_wifi_sta();
    ap_netif = esp_netif_create_default_wifi_ap();
    if (!sta_netif || !ap_netif) return ESP_ERR_NO_MEM;
    esp_netif_ip_info_t ip{};
    ip.ip.addr = ip.gw.addr = inet_addr(CARECALL_WIFI_SETUP_IP);
    ip.netmask.addr = inet_addr("255.255.255.0");
    result = esp_netif_dhcps_stop(ap_netif);
    if (result != ESP_OK && result != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) return result;
    result = esp_netif_set_ip_info(ap_netif, &ip); if (result != ESP_OK) return result;
    result = esp_netif_dhcps_start(ap_netif);
    if (result != ESP_OK && result != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STARTED) return result;
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    result = esp_wifi_init(&cfg); if (result != ESP_OK) return result;
    result = esp_wifi_set_storage(WIFI_STORAGE_RAM); if (result != ESP_OK) return result;
    std::uint8_t mac[6]{};
    result = esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP); if (result != ESP_OK) return result;
    std::snprintf(ap_ssid, sizeof(ap_ssid), "CareCall-ESP32-%02X%02X%02X", mac[3], mac[4], mac[5]);
    result = esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, event_handler, nullptr);
    if (result != ESP_OK) return result;
    result = esp_event_handler_register(IP_EVENT, ESP_EVENT_ANY_ID, event_handler, nullptr);
    if (result != ESP_OK) return result;
    result = esp_event_handler_register(CARECALL_WIFI_INTERNAL, ESP_EVENT_ANY_ID, event_handler, nullptr);
    if (result != ESP_OK) return result;
    initialized = true;
    if (xTaskCreate(worker, "wifi_setup_mgr", 6144, nullptr, 4, nullptr) != pdPASS) {
        initialized = false; return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "VERSION=20260930-esp32-wifi-1 CALL_STORAGE_UNCHANGED=YES");
    return ESP_OK;
}

bool wifi_manager_is_connected() { return connected.load(); }

void carecall_wifi_setup_status(CarecallWifiSetupStatus& result)
{
    xSemaphoreTake(state_mutex, portMAX_DELAY); result = status; xSemaphoreGive(state_mutex);
    result.station_ready = connected.load();
    result.storage_ready = storage_usable.load();
    result.disconnect_reason = disconnect_reason.load();
}

esp_err_t carecall_wifi_request_test(const CarecallWifiCredentials& candidate)
{
    if (!carecall_wifi_credentials_valid(candidate)) return ESP_ERR_INVALID_ARG;
    return enqueue(Action::Test, &candidate);
}
esp_err_t carecall_wifi_request_save() { return enqueue(Action::Save, nullptr); }
esp_err_t carecall_wifi_request_resume() { return enqueue(Action::Resume, nullptr); }
