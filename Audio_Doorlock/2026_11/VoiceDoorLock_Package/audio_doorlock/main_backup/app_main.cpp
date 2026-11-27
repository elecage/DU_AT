#include "battery_monitor.h"
#include "servo_lock.h"

#include "esp_check.h"
#include "esp_err.h"
#include "esp_event.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_matter.h"
#include "esp_matter_core.h"
#include "esp_matter_attribute.h"
#include "esp_matter_cluster.h"
#include "esp_matter_endpoint.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_wifi_types.h"
#include "esp_netif.h"
#include "esp_netif_ip_addr.h"
#include "esp_pm.h"
#include "nvs_flash.h"
#include "sdkconfig.h"


#include <app-common/zap-generated/ids/Attributes.h>
#include <app-common/zap-generated/ids/Clusters.h>
#include <app/clusters/door-lock-server/door-lock-server.h>
#include <app-common/zap-generated/cluster-enums.h>
#include <app/server/CommissioningWindowManager.h>
#include <app/server/Server.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/ConfigurationManager.h>

#include <algorithm>
#include <cstring>

using namespace esp_matter;
using namespace esp_matter::attribute;
using namespace esp_matter::endpoint;
using namespace chip::app::Clusters;

namespace {
// ESP-IDF 로그에서 이 파일의 메시지를 구분하기 위한 태그입니다.
constexpr char TAG[] = "matter_lock";

// 전원 상태 표시 LED
// GPIO5 -> LED 직렬저항(권장 330Ω, 필요 시 220~470Ω 범위) -> GND 기준의 Active-High 배선을 가정합니다.
// 펌웨어가 정상적으로 app_main()까지 진입해 초기화되면 LED를 계속 켜 둡니다.
// 기기 전원이 실제로 차단되면 GPIO 출력도 사라지므로 LED는 자동으로 꺼집니다.
constexpr gpio_num_t kPowerStatusLedGpio = GPIO_NUM_5;

esp_err_t init_power_status_led()
{
    gpio_config_t io_config = {};
    io_config.pin_bit_mask = (1ULL << kPowerStatusLedGpio);
    io_config.mode = GPIO_MODE_OUTPUT;
    io_config.pull_up_en = GPIO_PULLUP_DISABLE;
    io_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_config.intr_type = GPIO_INTR_DISABLE;

    ESP_RETURN_ON_ERROR(gpio_config(&io_config), TAG, "Failed to configure power status LED GPIO");

    // 전원 ON 상태 표시: GPIO5를 HIGH로 유지합니다.
    ESP_RETURN_ON_ERROR(gpio_set_level(kPowerStatusLedGpio, 1), TAG,
                        "Failed to turn on power status LED");

    ESP_LOGI(TAG, "Power status LED ON: GPIO%d", static_cast<int>(kPowerStatusLedGpio));
    return ESP_OK;
}

// -----------------------------------------------------------------------------
// GPIO6 Matter factory-reset button + GPIO5 commissioning LED
// -----------------------------------------------------------------------------
// Wiring:
//   GPIO6 ---- push button ---- GND
//
// GPIO6 uses the ESP32-C3 internal pull-up, so:
//   released = HIGH
//   pressed  = LOW
//
// Button behavior:
//   1) After boot, the button must be observed RELEASED once.
//   2) Input changes are debounced for 100 ms.
//   3) A continuous 5-second press requests esp_matter::factory_reset().
//   4) esp-matter erases its non-volatile Matter state and schedules a reboot.
//
// Commissioning LED behavior:
//   - no Matter fabric: GPIO5 blinks every 0.5 seconds
//   - commissioning complete / fabric exists: GPIO5 stays ON
//   - if a 5-minute commissioning window closes before pairing completes,
//     the firmware safely re-opens it after a short delay while no fabric exists.
constexpr gpio_num_t kFactoryResetButtonGpio = GPIO_NUM_6;
constexpr uint64_t kFactoryResetHoldUs = 5ULL * 1000ULL * 1000ULL;
constexpr uint64_t kFactoryResetPollIntervalUs = 50ULL * 1000ULL;
constexpr uint64_t kFactoryResetDebounceUs = 100ULL * 1000ULL;
constexpr uint64_t kCommissioningLedBlinkIntervalUs = 500ULL * 1000ULL;
constexpr uint32_t kCommissioningWindowTimeoutSeconds = 300;
constexpr uint64_t kCommissioningRetryDelayUs = 2ULL * 1000ULL * 1000ULL;

esp_timer_handle_t g_factory_reset_button_timer = nullptr;
esp_timer_handle_t g_commissioning_led_timer = nullptr;
esp_timer_handle_t g_commissioning_retry_timer = nullptr;

bool g_factory_reset_button_armed = false;
bool g_factory_reset_triggered = false;
bool g_factory_reset_raw_pressed = false;
bool g_factory_reset_stable_pressed = false;
int64_t g_factory_reset_raw_changed_us = 0;
int64_t g_factory_reset_press_started_us = 0;

bool g_commissioning_led_blinking = false;
bool g_commissioning_led_level = true;
bool g_commissioning_session_active = false;
// If the last fabric is removed at runtime, ESP-Matter may already have shut
// down BLE (CONFIG_USE_BLE_ONLY_FOR_COMMISSIONING). In that case, follow the
// Espressif example pattern and reopen via DNS-SD only. A physical factory reset
// reboots the device, clears Wi-Fi provisioning, and starts with this flag false,
// so fresh onboarding still advertises over all supported transports including BLE.
bool g_commissioning_dnssd_only = false;

// Forward declarations. Matter server objects are touched only from the Matter
// platform work queue; the ESP timer callback merely schedules that work.
void open_commissioning_window_work(intptr_t);
void schedule_commissioning_retry(uint64_t delay_us);
void cancel_commissioning_retry();

void set_power_status_led(bool on)
{
    g_commissioning_led_level = on;
    const esp_err_t err = gpio_set_level(kPowerStatusLedGpio, on ? 1 : 0);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to set GPIO5 LED=%d: %s",
                 on ? 1 : 0, esp_err_to_name(err));
    }
}

void commissioning_led_timer_cb(void *)
{
    g_commissioning_led_level = !g_commissioning_led_level;
    gpio_set_level(kPowerStatusLedGpio, g_commissioning_led_level ? 1 : 0);
}

esp_err_t start_commissioning_led_blink()
{
    if (g_commissioning_led_timer == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    if (g_commissioning_led_blinking) {
        return ESP_OK;
    }

    const esp_err_t stop_err = esp_timer_stop(g_commissioning_led_timer);
    if (stop_err != ESP_OK && stop_err != ESP_ERR_INVALID_STATE) {
        return stop_err;
    }

    g_commissioning_led_blinking = true;
    set_power_status_led(true);

    const esp_err_t err =
        esp_timer_start_periodic(g_commissioning_led_timer,
                                 kCommissioningLedBlinkIntervalUs);
    if (err == ESP_OK) {
        ESP_LOGI(TAG,
                 "Commissioning mode LED: GPIO5 blinking every 0.5 seconds");
    } else {
        g_commissioning_led_blinking = false;
    }
    return err;
}

void stop_commissioning_led_blink(bool leave_led_on)
{
    if (g_commissioning_led_timer != nullptr) {
        const esp_err_t stop_err = esp_timer_stop(g_commissioning_led_timer);
        if (stop_err != ESP_OK && stop_err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "Failed to stop commissioning LED timer: %s",
                     esp_err_to_name(stop_err));
        }
    }

    g_commissioning_led_blinking = false;
    set_power_status_led(leave_led_on);

    ESP_LOGI(TAG, "Commissioning mode LED stopped; GPIO5=%s",
             leave_led_on ? "ON" : "OFF");
}

esp_err_t init_factory_reset_button()
{
    gpio_config_t io_config = {};
    io_config.pin_bit_mask = (1ULL << kFactoryResetButtonGpio);
    io_config.mode = GPIO_MODE_INPUT;
    io_config.pull_up_en = GPIO_PULLUP_ENABLE;
    io_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_config.intr_type = GPIO_INTR_DISABLE;

    ESP_RETURN_ON_ERROR(
        gpio_config(&io_config),
        TAG,
        "Failed to configure GPIO6 factory-reset button");

    // Capture the initial electrical level. The button still remains disarmed
    // until a stable RELEASE is observed, preventing a held-button reboot loop.
    g_factory_reset_raw_pressed =
        (gpio_get_level(kFactoryResetButtonGpio) == 0);
    g_factory_reset_stable_pressed = g_factory_reset_raw_pressed;
    g_factory_reset_raw_changed_us = esp_timer_get_time();
    g_factory_reset_button_armed = false;
    g_factory_reset_triggered = false;
    g_factory_reset_press_started_us = 0;

    ESP_LOGI(TAG,
             "Factory-reset button ready: GPIO%d active-low, hold for 5 seconds; initial_level=%s",
             static_cast<int>(kFactoryResetButtonGpio),
             g_factory_reset_raw_pressed ? "LOW(pressed)" : "HIGH(released)");
    return ESP_OK;
}

void factory_reset_work(intptr_t)
{
    ESP_LOGW(TAG,
             "GPIO6 factory reset confirmed: erasing esp-matter/Matter provisioning and scheduling reboot");

    // Give immediate visual feedback before the reset/reboot is scheduled.
    const esp_err_t led_err = start_commissioning_led_blink();
    if (led_err != ESP_OK) {
        ESP_LOGW(TAG, "Unable to start reset/commissioning LED blink: %s",
                 esp_err_to_name(led_err));
    }

    // esp-matter release/v1.4.2 provides this API. It erases esp-matter NVS
    // state and then schedules the Matter server factory reset/restart.
    const esp_err_t reset_err = esp_matter::factory_reset();
    if (reset_err != ESP_OK) {
        // The SDK still schedules its submodule reset; retain the triggered
        // state so a noisy/repeated button press cannot queue another reset.
        ESP_LOGE(TAG, "esp_matter::factory_reset returned: %s",
                 esp_err_to_name(reset_err));
    } else {
        ESP_LOGW(TAG, "Factory reset scheduled successfully; device will reboot");
    }
}

void factory_reset_button_timer_cb(void *)
{
    if (g_factory_reset_triggered) {
        return;
    }

    const int64_t now_us = esp_timer_get_time();
    const bool raw_pressed =
        (gpio_get_level(kFactoryResetButtonGpio) == 0);

    // Debounce: every electrical edge must remain unchanged for at least
    // kFactoryResetDebounceUs before it becomes a stable button state.
    if (raw_pressed != g_factory_reset_raw_pressed) {
        g_factory_reset_raw_pressed = raw_pressed;
        g_factory_reset_raw_changed_us = now_us;
        return;
    }

    if ((now_us - g_factory_reset_raw_changed_us) <
        static_cast<int64_t>(kFactoryResetDebounceUs)) {
        return;
    }

    if (g_factory_reset_stable_pressed != g_factory_reset_raw_pressed) {
        g_factory_reset_stable_pressed = g_factory_reset_raw_pressed;

        if (!g_factory_reset_button_armed) {
            if (!g_factory_reset_stable_pressed) {
                g_factory_reset_button_armed = true;
                ESP_LOGI(TAG, "GPIO6 factory-reset button armed");
            }
            return;
        }

        if (g_factory_reset_stable_pressed) {
            g_factory_reset_press_started_us = now_us;
            ESP_LOGI(TAG,
                     "GPIO6 pressed: keep holding for 5 seconds to factory reset");
        } else {
            if (g_factory_reset_press_started_us != 0) {
                const int64_t held_ms =
                    (now_us - g_factory_reset_press_started_us) / 1000;
                ESP_LOGI(TAG,
                         "GPIO6 released before reset threshold: held=%lldms",
                         static_cast<long long>(held_ms));
            }
            g_factory_reset_press_started_us = 0;
        }
        return;
    }

    // At boot, if the button was already released, arm only after the release
    // has remained stable for the debounce period.
    if (!g_factory_reset_button_armed && !g_factory_reset_stable_pressed) {
        g_factory_reset_button_armed = true;
        ESP_LOGI(TAG, "GPIO6 factory-reset button armed");
        return;
    }

    if (!g_factory_reset_button_armed ||
        !g_factory_reset_stable_pressed ||
        g_factory_reset_press_started_us == 0) {
        return;
    }

    if ((now_us - g_factory_reset_press_started_us) >=
        static_cast<int64_t>(kFactoryResetHoldUs)) {
        g_factory_reset_triggered = true;
        g_factory_reset_press_started_us = 0;

        ESP_LOGW(TAG,
                 "GPIO6 held for >=5 seconds: scheduling esp-matter factory reset");
        chip::DeviceLayer::PlatformMgr().ScheduleWork(factory_reset_work, 0);
    }
}

// Wi-Fi 최대 송신전력 API의 단위는 0.25 dBm입니다.
// 34는 8.5 dBm 제한 요청입니다. Wi-Fi가 시작될 때마다 다시 적용합니다.
constexpr int8_t kWiFiMaxTxPowerQuarterDbm = 34;

// CPU가 할 일이 없고 드라이버의 PM lock이 해제되면 40 MHz 또는 자동 Light-sleep으로 대기합니다.
// 네트워크 수신을 유지해야 하므로 수동 Light-sleep/Deep-sleep은 호출하지 않습니다.
constexpr int kIdleCpuFrequencyMhz = 40;

// Google Assistant는 Door Lock endpoint의 음성 잠금해제를 막습니다.
// 그래서 별도 On/Off 스위치 endpoint를 "열림 트리거"로 사용합니다.
// 스위치가 켜지면 즉시 열고, 3초 뒤 자동으로 다시 잠급니다.
constexpr uint64_t kVoiceTriggerRelockDelayUs = 3ULL * 1000ULL * 1000ULL;

// 현관문 버튼 장치의 배터리 상태는 너무 자주 바뀌는 값이 아니므로 60초마다 측정합니다.
// 부팅 직후에는 Matter stack 시작 후 한 번 즉시 측정하도록 별도 ScheduleWork를 사용합니다.
constexpr uint64_t kBatteryMonitorIntervalUs = 60ULL * 1000ULL * 1000ULL;

// Google Home에서 배터리 상태를 장치 전체의 전원 상태로 인식할 수 있도록
// Power Source cluster를 Root endpoint(0)에 유지합니다.
// Google Home은 Battery feature가 활성화된 Matter Power Source cluster를
// EnergyStorage 상태로 매핑하여 배터리 잔량 질의에 사용할 수 있습니다.
constexpr uint16_t kPowerSourceEndpointId = 0;

// Matter Door Lock cluster의 LockState 값을 애플리케이션에서 다루기 쉽게 표현한 enum입니다.
// 숫자 값은 Matter Door Lock specification의 LockState enum 값과 맞춰 둡니다.
enum class LockState : uint8_t {
    NotFullyLocked = 0,
    Locked = 1,
    Unlocked = 2,
    Unlatched = 3,
    Unknown = 4,
};

// 실제 서보 제어 객체입니다.
// 이 객체는 PWM만 담당하고, Matter 네트워크나 endpoint 상태는 아래 전역 값들이 담당합니다.
ServoLock g_servo_lock;

// ADC로 배터리 전압을 읽고 퍼센트/저전압 상태로 변환하는 객체입니다.
BatteryMonitor g_battery_monitor;

// Door Lock endpoint id입니다.
// esp-matter가 endpoint를 생성한 뒤 실제 id를 할당하므로, 생성 후 저장해 둡니다.
uint16_t g_lock_endpoint_id = 0;

// Google Assistant 음성 트리거용 On/Off Plug-in Unit endpoint id입니다.
// Google Home 앱에서는 별도 스위치처럼 보이며, 켜짐 명령을 "잠깐 열기"로 해석합니다.
uint16_t g_voice_trigger_endpoint_id = 0;

// 음성 트리거로 문을 열었을 때 3초 뒤 다시 잠그기 위한 one-shot 타이머입니다.
esp_timer_handle_t g_auto_relock_timer = nullptr;

// 주기적으로 배터리 상태를 읽기 위한 타이머입니다.
esp_timer_handle_t g_battery_monitor_timer = nullptr;

// 내부에서 OnOff 상태를 되돌릴 때 PRE_UPDATE 콜백이 다시 서보를 구동하지 않도록 막습니다.
bool g_internal_voice_trigger_attribute_update = false;

uint8_t percent_to_matter_half_percent(uint8_t percent)
{
    // Matter Power Source의 BatPercentRemaining은 0.5% 단위라서 100%가 200입니다.
    return static_cast<uint8_t>(std::min<unsigned>(percent * 2U, 200U));
}

uint8_t charge_level_for_sample(const BatterySample &sample)
{
    if (sample.critical) {
        return static_cast<uint8_t>(PowerSource::BatChargeLevelEnum::kCritical);
    }
    if (sample.low) {
        return static_cast<uint8_t>(PowerSource::BatChargeLevelEnum::kWarning);
    }
    return static_cast<uint8_t>(PowerSource::BatChargeLevelEnum::kOk);
}

esp_err_t update_battery_power_source_attributes(const BatterySample &sample)
{
    // BatVoltage and BatPercentRemaining are nullable Matter attributes.
    // When the ADC reading is electrically implausible, publish NULL instead
    // of falsely converting a wiring error (for example GPIO3 ~= 0.5 V) to 0%.
    nullable<uint32_t> voltage_value;
    nullable<uint8_t> percent_value;

    if (sample.valid) {
        voltage_value = sample.battery_mv;
        percent_value =
            percent_to_matter_half_percent(sample.percent_remaining);
    }

    esp_matter_attr_val_t voltage =
        esp_matter_nullable_uint32(voltage_value);
    esp_err_t err = attribute::update(
        kPowerSourceEndpointId,
        PowerSource::Id,
        PowerSource::Attributes::BatVoltage::Id,
        &voltage);
    if (err != ESP_OK) {
        return err;
    }

    esp_matter_attr_val_t percent =
        esp_matter_nullable_uint8(percent_value);
    err = attribute::update(
        kPowerSourceEndpointId,
        PowerSource::Id,
        PowerSource::Attributes::BatPercentRemaining::Id,
        &percent);
    if (err != ESP_OK) {
        return err;
    }

    // The battery is physically part of this design. "valid" describes the ADC
    // measurement, not whether the cells are physically installed, so keep
    // BatPresent=true. On an invalid measurement, leave charge-level semantics
    // neutral and rely on NULL voltage/percentage plus the diagnostic log.
    esp_matter_attr_val_t present = esp_matter_bool(true);
    err = attribute::update(
        kPowerSourceEndpointId,
        PowerSource::Id,
        PowerSource::Attributes::BatPresent::Id,
        &present);
    if (err != ESP_OK) {
        return err;
    }

    if (!sample.valid) {
        ESP_LOGE(TAG,
                 "Matter battery measurement unavailable: GPIO3=%lumV reconstructed_pack=%lumV; voltage/percentage published as NULL",
                 static_cast<unsigned long>(sample.gpio_mv),
                 static_cast<unsigned long>(sample.battery_mv));
        return ESP_OK;
    }

    esp_matter_attr_val_t charge_level =
        esp_matter_enum8(charge_level_for_sample(sample));
    err = attribute::update(
        kPowerSourceEndpointId,
        PowerSource::Id,
        PowerSource::Attributes::BatChargeLevel::Id,
        &charge_level);
    if (err != ESP_OK) {
        return err;
    }

    esp_matter_attr_val_t replacement_needed =
        esp_matter_bool(sample.critical);
    err = attribute::update(
        kPowerSourceEndpointId,
        PowerSource::Id,
        PowerSource::Attributes::BatReplacementNeeded::Id,
        &replacement_needed);
    if (err != ESP_OK) {
        return err;
    }

    ESP_LOGI(TAG,
             "Matter/Google Home battery state updated: GPIO3=%lumV pack=%lumV %u%% charge_level=%u",
             static_cast<unsigned long>(sample.gpio_mv),
             static_cast<unsigned long>(sample.battery_mv),
             sample.percent_remaining,
             static_cast<unsigned>(charge_level_for_sample(sample)));
    return ESP_OK;
}

void battery_monitor_work(intptr_t)
{
    BatterySample sample = {};
    esp_err_t err = g_battery_monitor.read(sample);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to read battery state: %s", esp_err_to_name(err));
        return;
    }

    err = update_battery_power_source_attributes(sample);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to update Matter battery state: %s", esp_err_to_name(err));
        return;
    }

    if (!sample.valid) {
        ESP_LOGE(TAG,
                 "Battery ADC diagnostic: invalid measurement; verify B+ -> 100k -> GPIO3 -> 100k -> GND and common ground");
        return;
    }

    if (sample.critical) {
        ESP_LOGW(TAG, "Battery critical: %lumV (%u%%)",
                 static_cast<unsigned long>(sample.battery_mv),
                 sample.percent_remaining);
    } else if (sample.low) {
        ESP_LOGW(TAG, "Battery low: %lumV (%u%%)",
                 static_cast<unsigned long>(sample.battery_mv),
                 sample.percent_remaining);
    }
}

void battery_monitor_timer_cb(void *)
{
    // Matter attribute update는 CHIP platform work queue에서 수행합니다.
    chip::DeviceLayer::PlatformMgr().ScheduleWork(battery_monitor_work, 0);
}

esp_err_t add_battery_power_source_cluster(endpoint_t *root_endpoint,
                                           const BatterySample &initial_sample)
{
    cluster::power_source::config_t power_source_config;
    power_source_config.status =
        static_cast<uint8_t>(PowerSource::PowerSourceStatusEnum::kActive);
    power_source_config.order = 0;

    strncpy(power_source_config.description,
            "Entrance button battery",
            sizeof(power_source_config.description) - 1);
    power_source_config.description[
        sizeof(power_source_config.description) - 1] = '\0';

    power_source_config.feature_flags =
        cluster::power_source::feature::battery::get_id();
    power_source_config.features.battery.bat_charge_level =
        initial_sample.valid
            ? charge_level_for_sample(initial_sample)
            : static_cast<uint8_t>(PowerSource::BatChargeLevelEnum::kOk);
    power_source_config.features.battery.bat_replacement_needed =
        initial_sample.valid && initial_sample.critical;
    power_source_config.features.battery.bat_replaceability =
        static_cast<uint8_t>(PowerSource::BatReplaceabilityEnum::kUserReplaceable);

    cluster_t *power_source_cluster = cluster::power_source::create(
        root_endpoint,
        &power_source_config,
        CLUSTER_FLAG_SERVER);
    ESP_RETURN_ON_FALSE(power_source_cluster != nullptr,
                        ESP_FAIL,
                        TAG,
                        "Failed to create Power Source cluster");

    // Nullable attributes let the firmware distinguish a real 0% battery from
    // an invalid ADC circuit. Do not seed Google Home with a false 0% when the
    // input voltage is outside the plausible 1S2P/divider range.
    nullable<uint32_t> initial_voltage;
    nullable<uint8_t> initial_percent;
    if (initial_sample.valid) {
        initial_voltage = initial_sample.battery_mv;
        initial_percent = percent_to_matter_half_percent(
            initial_sample.percent_remaining);
    }

    cluster::power_source::attribute::create_bat_voltage(
        power_source_cluster,
        initial_voltage,
        0,
        0xFFFF);
    cluster::power_source::attribute::create_bat_percent_remaining(
        power_source_cluster,
        initial_percent,
        0,
        200);
    cluster::power_source::attribute::create_bat_present(
        power_source_cluster,
        true);

    if (initial_sample.valid) {
        ESP_LOGI(TAG,
                 "Matter Power Source ready: endpoint=%u GPIO3=%lumV pack=%lumV percent=%u%% raw_half_percent=%u",
                 kPowerSourceEndpointId,
                 static_cast<unsigned long>(initial_sample.gpio_mv),
                 static_cast<unsigned long>(initial_sample.battery_mv),
                 initial_sample.percent_remaining,
                 percent_to_matter_half_percent(initial_sample.percent_remaining));
    } else {
        ESP_LOGE(TAG,
                 "Matter Power Source ready but ADC measurement is invalid: endpoint=%u GPIO3=%lumV reconstructed_pack=%lumV; initial voltage/percentage=NULL",
                 kPowerSourceEndpointId,
                 static_cast<unsigned long>(initial_sample.gpio_mv),
                 static_cast<unsigned long>(initial_sample.battery_mv));
    }

    return ESP_OK;
}

void cancel_commissioning_retry()
{
    if (g_commissioning_retry_timer == nullptr) {
        return;
    }

    const esp_err_t err = esp_timer_stop(g_commissioning_retry_timer);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "Failed to stop commissioning retry timer: %s",
                 esp_err_to_name(err));
    }
}

void commissioning_retry_timer_cb(void *)
{
    // Never manipulate CommissioningWindowManager from the esp_timer task.
    // Move the work to the Matter platform queue instead.
    chip::DeviceLayer::PlatformMgr().ScheduleWork(open_commissioning_window_work, 0);
}

void schedule_commissioning_retry(uint64_t delay_us)
{
    if (g_commissioning_retry_timer == nullptr) {
        ESP_LOGW(TAG, "Commissioning retry timer is not initialized");
        return;
    }

    const esp_err_t stop_err = esp_timer_stop(g_commissioning_retry_timer);
    if (stop_err != ESP_OK && stop_err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "Failed to stop previous commissioning retry: %s",
                 esp_err_to_name(stop_err));
    }

    const esp_err_t start_err =
        esp_timer_start_once(g_commissioning_retry_timer, delay_us);
    if (start_err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to schedule commissioning retry: %s",
                 esp_err_to_name(start_err));
    } else {
        ESP_LOGI(TAG, "Commissioning retry scheduled in %llums",
                 static_cast<unsigned long long>(delay_us / 1000ULL));
    }
}

void open_commissioning_window_if_needed()
{
    auto &server = chip::Server::GetInstance();
    auto &commissioning = server.GetCommissioningWindowManager();
    const size_t fabric_count = server.GetFabricTable().FabricCount();

    // A committed fabric means pairing succeeded. Stop all retry work and keep
    // the status LED steadily ON.
    if (fabric_count != 0) {
        cancel_commissioning_retry();
        stop_commissioning_led_blink(true);
        return;
    }

    // No fabric means the device must remain visibly ready for onboarding.
    const esp_err_t led_err = start_commissioning_led_blink();
    if (led_err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to start commissioning LED blink: %s",
                 esp_err_to_name(led_err));
    }

    // Do not open a second window during an active PASE/commissioning session.
    // A closed window can be normal once a commissioning session has started.
    if (g_commissioning_session_active) {
        ESP_LOGI(TAG, "Commissioning session active; not opening another window");
        return;
    }

    if (commissioning.IsCommissioningWindowOpen()) {
        cancel_commissioning_retry();
        return;
    }

    // 300 seconds is above the Matter-defined 3-minute minimum. If it expires
    // before the user finishes pairing, kCommissioningWindowClosed schedules a
    // safe re-open, so an uncommissioned device remains pairable.
    const auto timeout =
        chip::System::Clock::Seconds32(kCommissioningWindowTimeoutSeconds);
    const auto advertisement =
        g_commissioning_dnssd_only
            ? chip::CommissioningWindowAdvertisement::kDnssdOnly
            : chip::CommissioningWindowAdvertisement::kAllSupported;
    const CHIP_ERROR error = commissioning.OpenBasicCommissioningWindow(
        timeout, advertisement);

    if (error != CHIP_NO_ERROR) {
        ESP_LOGE(TAG, "Failed to open commissioning window: %" CHIP_ERROR_FORMAT,
                 error.Format());
        schedule_commissioning_retry(kCommissioningRetryDelayUs);
        return;
    }

    cancel_commissioning_retry();
    ESP_LOGI(TAG,
             "Matter commissioning window opened for %lu seconds mode=%s; GPIO5 keeps blinking until a fabric is committed",
             static_cast<unsigned long>(kCommissioningWindowTimeoutSeconds),
             g_commissioning_dnssd_only ? "DNS-SD-only" : "all-supported/BLE");
}

void open_commissioning_window_work(intptr_t)
{
    // This function runs on the Matter platform work queue. The ESP-Matter FAQ
    // explicitly requires server operations such as opening/closing a
    // commissioning window to be performed while holding/scheduling onto the
    // Matter thread.
    open_commissioning_window_if_needed();
}

esp_err_t update_lock_state_attribute(LockState state)
{
    // Google Home에 "현재 잠금 상태"를 알려주기 위해 DoorLock cluster의 LockState attribute를 갱신합니다.
    // 실제 서보를 움직이는 것과 Matter attribute를 갱신하는 것은 별도 작업입니다.
    // 서보가 움직였는데 attribute 갱신이 실패하면 앱에는 상태가 늦게 반영될 수 있습니다.
    esp_matter_attr_val_t value = esp_matter_enum8(static_cast<uint8_t>(state));
    return attribute::update(g_lock_endpoint_id, DoorLock::Id, DoorLock::Attributes::LockState::Id, &value);
}

esp_err_t update_voice_trigger_attribute(bool on)
{
    // 보조 스위치 endpoint가 아직 생성되지 않은 상태에서 호출되면 잘못된 상태입니다.
    if (g_voice_trigger_endpoint_id == 0) {
        return ESP_ERR_INVALID_STATE;
    }

    // 내부 상태 동기화 때문에 attribute::update()를 호출할 때 PRE_UPDATE가 다시
    // handle_voice_trigger()로 들어가 서보를 중복 구동하지 않도록 guard를 사용합니다.
    esp_matter_attr_val_t value = esp_matter_bool(on);
    g_internal_voice_trigger_attribute_update = true;
    const esp_err_t err =
        attribute::update(g_voice_trigger_endpoint_id, OnOff::Id,
                          OnOff::Attributes::OnOff::Id, &value);
    g_internal_voice_trigger_attribute_update = false;
    return err;
}

esp_err_t move_servo_for_lock_state(LockState state)
{
    // Matter LockState를 실제 서보 동작으로 변환하는 중앙 함수입니다.
    // DoorLock 명령, DoorLock attribute 변경, 자동 재잠금 등 여러 경로에서 이 함수 또는
    // g_servo_lock.lock()/unlock()을 통해 동일한 물리 동작을 수행합니다.
    switch (state) {
    case LockState::Locked:
        return g_servo_lock.lock();
    case LockState::Unlocked:
    case LockState::Unlatched:
        return g_servo_lock.unlock();
    default:
        // NotFullyLocked, Unknown 같은 상태는 실제 서보 위치를 새로 정하기 애매하므로 무시합니다.
        ESP_LOGW(TAG, "Ignoring unsupported lock state %u", static_cast<unsigned>(state));
        return ESP_OK;
    }
}

void auto_relock_work(intptr_t)
{
    // esp_timer callback은 타이머 task 문맥에서 호출됩니다.
    // 실제 Matter attribute update는 CHIP platform work queue에서 처리하는 것이 안전하므로
    // auto_relock_timer_cb()에서 이 함수를 ScheduleWork로 예약합니다.
    ESP_LOGI(TAG, "Voice trigger auto-relock after 3 seconds");

    // 3초가 지나면 물리 서보를 먼저 잠김 위치로 보냅니다.
    esp_err_t lock_err = g_servo_lock.lock();
    if (lock_err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to auto-relock servo: %s", esp_err_to_name(lock_err));
        return;
    }

    // 서보가 잠겼다는 사실을 DoorLock endpoint의 상태에도 반영합니다.
    // 이 값이 Google Home의 도어락 타일 상태 표시와 연결됩니다.
    esp_err_t lock_state_err = update_lock_state_attribute(LockState::Locked);
    if (lock_state_err != ESP_OK) {
        ESP_LOGW(TAG, "Auto-relocked servo, but DoorLock state update failed: %s",
                 esp_err_to_name(lock_state_err));
    }

    // 음성 트리거 스위치도 다시 꺼짐으로 돌려놓습니다.
    // 이렇게 해야 다음에 "스위치 켜줘" 명령을 다시 보낼 수 있고,
    // Google Home UI에서도 순간 버튼처럼 동작합니다.
    esp_err_t trigger_state_err = update_voice_trigger_attribute(false);
    if (trigger_state_err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to reset voice trigger switch state: %s", esp_err_to_name(trigger_state_err));
    }
}

void auto_relock_timer_cb(void *)
{
    // esp_timer callback 안에서 직접 Matter attribute를 만지지 않고,
    // CHIP platform work queue로 넘깁니다.
    chip::DeviceLayer::PlatformMgr().ScheduleWork(auto_relock_work, 0);
}

esp_err_t schedule_auto_relock()
{
    // 타이머는 app_main()에서 한 번 생성해 둡니다.
    // 생성 전 호출되면 코드 흐름상 오류이므로 ESP_ERR_INVALID_STATE를 반환합니다.
    if (g_auto_relock_timer == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    // 이미 자동 재잠금 타이머가 돌고 있다면 먼저 멈춥니다.
    // 예를 들어 사용자가 3초 안에 스위치를 다시 켰을 때 타이머를 새로 3초로 연장하는 효과가 있습니다.
    esp_err_t stop_err = esp_timer_stop(g_auto_relock_timer);
    if (stop_err != ESP_OK && stop_err != ESP_ERR_INVALID_STATE) {
        return stop_err;
    }

    // one-shot 타이머로 3초 뒤 auto_relock_timer_cb()가 한 번 호출됩니다.
    return esp_timer_start_once(g_auto_relock_timer, kVoiceTriggerRelockDelayUs);
}

esp_err_t handle_voice_trigger(bool on)
{
    // On/Off Plug-in Unit endpoint의 OnOff attribute가 바뀔 때 호출됩니다.
    //
    // on == true:
    //   Google Home 또는 Nest Mini에서 보조 스위치를 켠 상황입니다.
    //   서보를 열림 위치로 보내고 3초 자동 재잠금을 예약합니다.
    //
    // on == false:
    //   사용자가 스위치를 끄거나, 앱에서 꺼짐 상태가 들어온 상황입니다.
    //   자동 재잠금을 취소하고 즉시 잠김 위치로 보냅니다.
    ESP_LOGI(TAG, "Voice trigger switch command: %s", on ? "open" : "close");

    if (!on) {
        // 사용자가 명시적으로 스위치를 끈 경우에는 예정된 자동 재잠금 타이머가 있다면 취소합니다.
        esp_err_t stop_err = esp_timer_stop(g_auto_relock_timer);
        if (stop_err != ESP_OK && stop_err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "Failed to stop auto-relock timer: %s", esp_err_to_name(stop_err));
        }

        // 꺼짐 명령은 "닫기/잠그기"로 해석합니다.
        ESP_RETURN_ON_ERROR(g_servo_lock.lock(), TAG, "Failed to lock from voice trigger off command");
        return update_lock_state_attribute(LockState::Locked);
    }

    // 켜짐 명령은 "잠깐 열기"로 해석합니다.
    ESP_RETURN_ON_ERROR(g_servo_lock.unlock(), TAG, "Failed to unlock from voice trigger on command");

    // DoorLock endpoint도 Unlocked 상태로 갱신합니다.
    // Google Home 앱에서 도어락 타일을 함께 보고 있을 때 상태가 맞도록 하기 위한 처리입니다.
    esp_err_t lock_state_err = update_lock_state_attribute(LockState::Unlocked);
    if (lock_state_err != ESP_OK) {
        ESP_LOGW(TAG, "Voice trigger unlocked servo, but DoorLock state update failed: %s",
                 esp_err_to_name(lock_state_err));
    }

    // 3초 뒤 자동으로 다시 잠그도록 타이머를 예약합니다.
    ESP_RETURN_ON_ERROR(schedule_auto_relock(), TAG, "Failed to schedule auto-relock");
    return ESP_OK;
}

LockState to_app_lock_state(DlLockState state)
{
    // ESP-Matter Door Lock 서버 콜백에서 넘겨주는 DlLockState를
    // 이 파일에서 사용하는 LockState enum으로 변환합니다.
    switch (state) {
    case DlLockState::kLocked:
        return LockState::Locked;
    case DlLockState::kUnlocked:
        return LockState::Unlocked;
    case DlLockState::kUnlatched:
        return LockState::Unlatched;
    case DlLockState::kNotFullyLocked:
        return LockState::NotFullyLocked;
    default:
        return LockState::Unknown;
    }
}

bool set_lock_state_from_matter_command(chip::EndpointId endpoint_id, DlLockState state,
                                        const Nullable<chip::FabricIndex> &fabric_idx,
                                        const Nullable<chip::NodeId> &node_id,
                                        OperationErrorEnum &err)
{
    // DoorLock 명령은 반드시 도어락 endpoint로 들어와야 합니다.
    // 다른 endpoint로 들어온 명령은 잘못된 명령으로 보고 무시합니다.
    if (endpoint_id != g_lock_endpoint_id) {
        err = OperationErrorEnum::kUnspecified;
        ESP_LOGW(TAG, "Ignoring DoorLock command for unexpected endpoint=%u", endpoint_id);
        return false;
    }

    // Matter DoorLock 명령의 상태 값을 애플리케이션 상태로 변환한 뒤,
    // 먼저 실제 서보를 움직입니다.
    LockState app_state = to_app_lock_state(state);
    ESP_LOGI(TAG, "Applying DoorLock command endpoint=%u state=%u app_state=%u", endpoint_id,
             static_cast<unsigned>(state), static_cast<unsigned>(app_state));
    if (move_servo_for_lock_state(app_state) != ESP_OK) {
        err = OperationErrorEnum::kUnspecified;
        ESP_LOGE(TAG, "Failed to move servo for Matter lock state %u", static_cast<unsigned>(state));
        return false;
    }

    // 서보 동작이 성공하면 Matter attribute도 같은 상태로 갱신합니다.
    // 이 갱신은 Google Home UI의 상태 표시와 subscription report에 영향을 줍니다.
    esp_err_t update_err = update_lock_state_attribute(app_state);
    if (update_err != ESP_OK) {
        ESP_LOGW(TAG, "Servo moved, but Matter lock state update failed: %s", esp_err_to_name(update_err));
    }

    // Google Home의 Door Lock 타일에서 직접 잠금 해제한 경우에도
    // 음성 트리거와 동일하게 3초 뒤 원위치로 자동 복귀시킵니다.
    if (app_state == LockState::Unlocked || app_state == LockState::Unlatched) {
        esp_err_t relock_err = schedule_auto_relock();
        if (relock_err != ESP_OK) {
            err = OperationErrorEnum::kUnspecified;
            ESP_LOGE(TAG, "Failed to schedule 3-second auto-relock: %s", esp_err_to_name(relock_err));
            return false;
        }
    }

    // 현재 구현에서는 fabric/node id와 PIN code를 별도로 검증하지 않습니다.
    // Google Home이 Matter 세션 보안을 통해 이미 인증된 controller로 접근한다고 가정합니다.
    // Matter Door Lock API에서 err는 return false일 때만 설정하는 값이므로
    // 성공(return true) 경로에서는 건드리지 않습니다.
    (void)fabric_idx;
    (void)node_id;
    return true;
}

void app_event_cb(const ChipDeviceEvent *event, intptr_t)
{
    // ESP-Matter forwards commissioning/fabric lifecycle events from the Matter
    // stack. We use them to keep GPIO5 and the commissioning-window retry logic
    // synchronized with the actual Matter state.
    switch (event->Type) {
    case chip::DeviceLayer::DeviceEventType::kCommissioningSessionStarted:
        g_commissioning_session_active = true;
        cancel_commissioning_retry();
        ESP_LOGI(TAG, "Matter commissioning session started");
        if (start_commissioning_led_blink() != ESP_OK) {
            ESP_LOGW(TAG, "Unable to keep commissioning LED blinking");
        }
        break;

    case chip::DeviceLayer::DeviceEventType::kCommissioningSessionStopped:
        g_commissioning_session_active = false;
        ESP_LOGI(TAG, "Matter commissioning session stopped");
        if (chip::Server::GetInstance().GetFabricTable().FabricCount() == 0) {
            schedule_commissioning_retry(kCommissioningRetryDelayUs);
        }
        break;

    case chip::DeviceLayer::DeviceEventType::kCommissioningWindowOpened:
        ESP_LOGI(TAG, "Matter commissioning window opened event");
        cancel_commissioning_retry();
        if (start_commissioning_led_blink() != ESP_OK) {
            ESP_LOGW(TAG, "Unable to start commissioning LED blink");
        }
        break;

    case chip::DeviceLayer::DeviceEventType::kCommissioningWindowClosed:
        ESP_LOGI(TAG, "Matter commissioning window closed event");
        // A window can close because a PASE session started. Do not fight an
        // active commissioning session. If there is no session/fabric, re-open
        // after a short delay so the device does not become permanently
        // undiscoverable after the 5-minute window expires.
        if (chip::Server::GetInstance().GetFabricTable().FabricCount() == 0 &&
            !g_commissioning_session_active) {
            schedule_commissioning_retry(kCommissioningRetryDelayUs);
        }
        break;

    case chip::DeviceLayer::DeviceEventType::kCommissioningComplete:
        g_commissioning_session_active = false;
        g_commissioning_dnssd_only = false;
        ESP_LOGI(TAG, "Matter commissioning complete");
        cancel_commissioning_retry();
        stop_commissioning_led_blink(true);
        break;

    case chip::DeviceLayer::DeviceEventType::kFailSafeTimerExpired:
        g_commissioning_session_active = false;
        ESP_LOGW(TAG, "Matter commissioning failed: fail-safe timer expired");
        if (chip::Server::GetInstance().GetFabricTable().FabricCount() == 0) {
            schedule_commissioning_retry(kCommissioningRetryDelayUs);
        }
        break;

    case chip::DeviceLayer::DeviceEventType::kFabricRemoved:
        g_commissioning_session_active = false;
        ESP_LOGI(TAG, "Matter fabric removed");
        if (chip::Server::GetInstance().GetFabricTable().FabricCount() == 0) {
            // Espressif examples keep Wi-Fi credentials after last-fabric removal
            // and re-advertise via DNS-SD. BLE may already be shut down on a
            // commissioned device, so do not force BLE in this runtime path.
            g_commissioning_dnssd_only = true;
            // Delay rather than reopening directly inside FabricRemoved. This
            // gives the SDK time to finish fabric/BLE cleanup before advertising
            // starts again.
            if (start_commissioning_led_blink() != ESP_OK) {
                ESP_LOGW(TAG, "Unable to start commissioning LED after fabric removal");
            }
            schedule_commissioning_retry(kCommissioningRetryDelayUs);
        }
        break;

    case chip::DeviceLayer::DeviceEventType::kFabricCommitted:
        ESP_LOGI(TAG, "Matter fabric committed");
        if (chip::Server::GetInstance().GetFabricTable().FabricCount() != 0) {
            g_commissioning_dnssd_only = false;
            cancel_commissioning_retry();
            stop_commissioning_led_blink(true);
        }
        break;

    default:
        break;
    }
}

const char *wifi_disconnect_reason_to_name(uint8_t reason)
{
    switch (reason) {
    case WIFI_REASON_AUTH_EXPIRE:
        return "AUTH_EXPIRE";
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
        return "4WAY_HANDSHAKE_TIMEOUT";
    case WIFI_REASON_NO_AP_FOUND:
        return "NO_AP_FOUND";
    case WIFI_REASON_AUTH_FAIL:
        return "AUTH_FAIL";
    case WIFI_REASON_ASSOC_FAIL:
        return "ASSOC_FAIL";
    case WIFI_REASON_HANDSHAKE_TIMEOUT:
        return "HANDSHAKE_TIMEOUT";
    case WIFI_REASON_CONNECTION_FAIL:
        return "CONNECTION_FAIL";
    case WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY:
        return "NO_AP_FOUND_WITH_COMPATIBLE_SECURITY";
    case WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD:
        return "NO_AP_FOUND_IN_AUTHMODE_THRESHOLD";
    case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD:
        return "NO_AP_FOUND_IN_RSSI_THRESHOLD";
    default:
        return "UNKNOWN";
    }
}

uint32_t hash_bytes(const uint8_t *data, size_t length)
{
    uint32_t hash = 2166136261UL;
    for (size_t i = 0; i < length; ++i) {
        hash ^= data[i];
        hash *= 16777619UL;
    }
    return hash;
}

void log_current_wifi_config()
{
    wifi_config_t config = {};
    esp_err_t err = esp_wifi_get_config(WIFI_IF_STA, &config);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Unable to read current Wi-Fi STA config: %s", esp_err_to_name(err));
        return;
    }

    const size_t ssid_len = strnlen(reinterpret_cast<const char *>(config.sta.ssid),
                                    sizeof(config.sta.ssid));
    const size_t password_len = strnlen(reinterpret_cast<const char *>(config.sta.password),
                                        sizeof(config.sta.password));
    const uint32_t password_hash = hash_bytes(config.sta.password, password_len);

    ESP_LOGI(TAG, "Current Wi-Fi config: ssid='%.*s' ssid_len=%u password_len=%u password_hash=0x%08lx",
             static_cast<int>(ssid_len),
             reinterpret_cast<const char *>(config.sta.ssid),
             static_cast<unsigned>(ssid_len),
             static_cast<unsigned>(password_len),
             static_cast<unsigned long>(password_hash));
}

void apply_wifi_tx_power_limit()
{
    // esp_wifi_set_max_tx_power()는 Wi-Fi 드라이버 시작 후 호출해야 합니다.
    // Matter가 아직 Wi-Fi를 시작하지 않았다면 STA_START 이벤트에서 재시도합니다.
    esp_err_t err = esp_wifi_set_max_tx_power(kWiFiMaxTxPowerQuarterDbm);
    if (err == ESP_ERR_WIFI_NOT_INIT || err == ESP_ERR_WIFI_NOT_STARTED) {
        ESP_LOGI(TAG, "Wi-Fi TX power limit deferred until Wi-Fi starts");
        return;
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to set Wi-Fi TX power: %s", esp_err_to_name(err));
        return;
    }

    // 드라이버가 보고하는 최대 전력 설정을 확인합니다.
    // 이 값은 모든 패킷의 실제 방사 전력을 측정한 값이 아닙니다.
    int8_t configured_power = 0;
    err = esp_wifi_get_max_tx_power(&configured_power);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to read Wi-Fi TX power: %s", esp_err_to_name(err));
        return;
    }

    const int power = static_cast<int>(configured_power);
    ESP_LOGI(TAG, "Wi-Fi TX power limit: %d.%02d dBm (raw=%d)",
             power / 4, (power % 4) * 25, power);
    if (power > kWiFiMaxTxPowerQuarterDbm) {
        ESP_LOGW(TAG, "Reported Wi-Fi TX power exceeds requested limit (raw=%d)",
                 static_cast<int>(kWiFiMaxTxPowerQuarterDbm));
    }
}

void configure_power_management()
{
#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
    esp_pm_config_t config = {};
    config.max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ;
    config.min_freq_mhz = kIdleCpuFrequencyMhz;
#if defined(CONFIG_FREERTOS_USE_TICKLESS_IDLE) && CONFIG_FREERTOS_USE_TICKLESS_IDLE
    config.light_sleep_enable = true;
#else
    config.light_sleep_enable = false;
    ESP_LOGW(TAG, "Tickless idle is disabled: automatic Light-sleep unavailable; enable CONFIG_FREERTOS_USE_TICKLESS_IDLE");
#endif

    const esp_err_t err = esp_pm_configure(&config);
    if (err != ESP_OK) {
        // 절전 설정 실패 때문에 도어락 제어까지 중단하지 않습니다.
        ESP_LOGW(TAG, "CPU power management setup failed: %s; Wi-Fi modem-sleep only",
                 esp_err_to_name(err));
        return;
    }
    ESP_LOGI(TAG, "Power management configured: CPU=%d..%d MHz automatic_light_sleep=%s",
             config.min_freq_mhz, config.max_freq_mhz,
             config.light_sleep_enable ? "enabled" : "disabled");
    // 설정 성공은 지금 실제로 잠들었다는 뜻이 아닙니다.
    // Wi-Fi/BLE/USB 드라이버와 실행 중인 작업이 절전 진입 가능 시점을 결정합니다.
#if defined(CONFIG_USJ_NO_AUTO_LS_ON_CONNECTION) && CONFIG_USJ_NO_AUTO_LS_ON_CONNECTION
    ESP_LOGI(TAG, "USB Serial/JTAG host connection prevents automatic Light-sleep");
#endif
    ESP_LOGI(TAG, "Light-sleep requires idle tasks and no blocking driver locks (including BLE)");
#else
    ESP_LOGW(TAG, "CONFIG_PM_ENABLE is disabled: CPU sleep unavailable; Wi-Fi modem-sleep only. Run configure_low_power.py");
#endif
}

void apply_wifi_sta_tuning()
{
    apply_wifi_tx_power_limit();

    // Wi-Fi 연결을 유지하며 AP의 DTIM 주기에 맞춰 수신합니다.
    // MAX_MODEM보다 대기 시간이 짧고 broadcast 수신에 유리한 MIN_MODEM을 사용합니다.
    // 실제 modem-sleep은 AP 연결 후 드라이버가 관리합니다.
    esp_err_t ps_err = esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
    if (ps_err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to enable Wi-Fi MIN_MODEM power save: %s", esp_err_to_name(ps_err));
    }

    esp_err_t bw_err = esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);
    if (bw_err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to force Wi-Fi HT20 bandwidth: %s", esp_err_to_name(bw_err));
    }

    if (ps_err == ESP_OK && bw_err == ESP_OK) {
        ESP_LOGI(TAG, "Wi-Fi STA tuning applied: power_save=MIN_MODEM bandwidth=HT20");
    }
}

void wifi_debug_event_handler(void *, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    if (event_base != WIFI_EVENT) {
        return;
    }

    if (event_id == WIFI_EVENT_STA_START) {
        apply_wifi_sta_tuning();
        return;
    }

    if (event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const auto *event = static_cast<wifi_event_sta_disconnected_t *>(event_data);
        ESP_LOGW(TAG, "Wi-Fi disconnected: ssid='%.*s' bssid=%02x:%02x:%02x:%02x:%02x:%02x reason=%u (%s) rssi=%d",
                 event->ssid_len,
                 reinterpret_cast<const char *>(event->ssid),
                 event->bssid[0], event->bssid[1], event->bssid[2],
                 event->bssid[3], event->bssid[4], event->bssid[5],
                 event->reason,
                 wifi_disconnect_reason_to_name(event->reason),
                 event->rssi);
        apply_wifi_sta_tuning();
        log_current_wifi_config();
        return;
    }

    if (event_id == WIFI_EVENT_STA_CONNECTED) {
        // AP 연결 후에도 절전 정책과 송신전력 제한을 다시 적용합니다.
        apply_wifi_sta_tuning();
        const auto *event = static_cast<wifi_event_sta_connected_t *>(event_data);
        ESP_LOGI(TAG, "Wi-Fi connected: ssid='%.*s' channel=%u authmode=%u",
                 event->ssid_len,
                 reinterpret_cast<const char *>(event->ssid),
                 event->channel,
                 event->authmode);
        log_current_wifi_config();
    }
}
void ip_debug_event_handler(void *, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    if (event_base != IP_EVENT) {
        return;
    }

    if (event_id == IP_EVENT_STA_GOT_IP) {
        const auto *event = static_cast<ip_event_got_ip_t *>(event_data);

        ESP_LOGI(TAG,
                 "Wi-Fi got IPv4: ip=" IPSTR " gateway=" IPSTR " netmask=" IPSTR,
                 IP2STR(&event->ip_info.ip),
                 IP2STR(&event->ip_info.gw),
                 IP2STR(&event->ip_info.netmask));

        log_current_wifi_config();
        return;
    }
}

esp_err_t app_identification_cb(identification::callback_type_t type, uint16_t endpoint_id, uint8_t effect_id,
                                uint8_t effect_variant, void *)
{
    // Google Home 앱에서 "기기 식별" 같은 동작을 실행할 때 호출될 수 있습니다.
    // 물리 LED나 LED GPIO는 사용하지 않으며, Matter 식별 요청은 로그만 남깁니다.
    ESP_LOGI(TAG, "Identify endpoint=%u type=%u effect=%u variant=%u", endpoint_id, type, effect_id, effect_variant);
    return ESP_OK;
}

esp_err_t app_attribute_update_cb(attribute::callback_type_t type, uint16_t endpoint_id, uint32_t cluster_id,
                                  uint32_t attribute_id, esp_matter_attr_val_t *value, void *)
{
    // esp-matter는 attribute가 변경되기 전(PRE_UPDATE)과 변경된 후(POST_UPDATE)에 콜백을 줄 수 있습니다.
    // 실제 서보 동작은 상태가 저장되기 전에 실패 여부를 반영할 수 있도록 PRE_UPDATE에서 처리합니다.
    if (type != PRE_UPDATE) {
        return ESP_OK;
    }

    // Door Lock의 실제 물리 구동은 emberAfPluginDoorLockOnDoorLockCommand() /
    // emberAfPluginDoorLockOnDoorUnlockCommand()에서 한 번만 수행합니다.
    // LockState attribute 갱신까지 여기서 다시 서보를 움직이면 동일 명령이 중복 실행됩니다.

    // 보조 On/Off 스위치 endpoint의 OnOff attribute가 바뀌면
    // "켜짐 = 잠깐 열기", "꺼짐 = 닫기"로 해석합니다.
    if (endpoint_id == g_voice_trigger_endpoint_id &&
        cluster_id == OnOff::Id && attribute_id == OnOff::Attributes::OnOff::Id) {
        if (g_internal_voice_trigger_attribute_update) {
            ESP_LOGI(TAG, "Voice trigger attribute synchronized internally: %s",
                     value->val.b ? "on" : "off");
            return ESP_OK;
        }
        return handle_voice_trigger(value->val.b);
    }

    return ESP_OK;
}

} // namespace

bool emberAfPluginDoorLockOnDoorLockCommand(chip::EndpointId endpoint_id,
                                            const Nullable<chip::FabricIndex> &fabric_idx,
                                            const Nullable<chip::NodeId> &node_id,
                                            const Optional<chip::ByteSpan> &pin_code,
                                            OperationErrorEnum &err)
{
    // Door Lock cluster의 LockDoor command가 들어오면 esp-matter가 이 weak callback을 호출합니다.
    // 여기서는 PIN 코드를 별도로 확인하지 않고, Matter 세션을 통해 인증된 controller의 명령으로 간주합니다.
    (void)pin_code;
    ESP_LOGI(TAG, "Matter LockDoor command received");
    return set_lock_state_from_matter_command(endpoint_id, DlLockState::kLocked, fabric_idx, node_id, err);
}

bool emberAfPluginDoorLockOnDoorUnlockCommand(chip::EndpointId endpoint_id,
                                              const Nullable<chip::FabricIndex> &fabric_idx,
                                              const Nullable<chip::NodeId> &node_id,
                                              const Optional<chip::ByteSpan> &pin_code,
                                              OperationErrorEnum &err)
{
    // Door Lock cluster의 UnlockDoor command가 들어왔을 때 호출됩니다.
    // Google Assistant 스피커는 보안 정책상 이 명령을 음성으로 직접 보내지 않지만,
    // Google Home 앱의 도어락 타일에서는 이 경로가 사용될 수 있습니다.
    (void)pin_code;
    ESP_LOGI(TAG, "Matter UnlockDoor command received");
    return set_lock_state_from_matter_command(endpoint_id, DlLockState::kUnlocked, fabric_idx, node_id, err);
}

bool emberAfPluginDoorLockOnDoorUnboltCommand(chip::EndpointId endpoint_id,
                                              const Nullable<chip::FabricIndex> &fabric_idx,
                                              const Nullable<chip::NodeId> &node_id,
                                              const Optional<chip::ByteSpan> &pin_code,
                                              OperationErrorEnum &err)
{
    // 일부 controller는 UnlockDoor 대신 UnboltDoor command를 사용할 수 있습니다.
    // 이 프로젝트에서는 Unbolt도 물리적으로는 "열림 위치로 이동"과 동일하게 처리합니다.
    (void)pin_code;
    ESP_LOGI(TAG, "Matter UnboltDoor command received");
    return set_lock_state_from_matter_command(endpoint_id, DlLockState::kUnlocked, fabric_idx, node_id, err);
}

void emberAfPluginDoorLockOnAutoRelock(chip::EndpointId endpoint_id)
{
    // Matter Door Lock cluster 자체의 AutoRelock 이벤트가 발생했을 때 호출될 수 있는 hook입니다.
    // 현재 실제 자동 재잠금은 보조 스위치용 esp_timer에서 직접 처리하므로 여기서는 로그만 남깁니다.
    ESP_LOGI(TAG, "Matter auto relock fired for endpoint=%u", endpoint_id);
}

extern "C" void app_main()
{
    // 가능한 한 부팅 초기에 GPIO5 전원 상태 LED를 켭니다.
    // 이 지점까지 펌웨어가 실행되면 LED는 계속 ON 상태를 유지합니다.
    ESP_ERROR_CHECK(init_power_status_led());

    // NVS는 Wi-Fi credential, Matter fabric, commissioning 정보 등을 저장하는 플래시 영역입니다.
    // NVS가 비어 있거나 버전이 맞지 않으면 지우고 다시 초기화합니다.
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    // GPIO6: 5초 길게 누르면 Matter factory reset을 요청하는 버튼입니다.
    // 버튼은 GPIO6-GND 사이에 연결하고 내부 pull-up을 사용합니다.
    ESP_ERROR_CHECK(init_factory_reset_button());

    // GPIO5 commissioning 점멸 타이머를 생성합니다.
    // Fabric이 없을 때만 0.5초 간격으로 실행되고, 정상 연결 상태에서는 정지합니다.
    const esp_timer_create_args_t commissioning_led_timer_args = {
        .callback = &commissioning_led_timer_cb,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "commission_led",
        .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(
        esp_timer_create(&commissioning_led_timer_args,
                         &g_commissioning_led_timer));

    // Commissioning window가 만료되거나 fabric 제거 직후 즉시 재오픈이
    // 불안정할 수 있으므로, 짧게 지연한 뒤 Matter thread에서 다시 엽니다.
    const esp_timer_create_args_t commissioning_retry_timer_args = {
        .callback = &commissioning_retry_timer_cb,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "commission_retry",
        .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(
        esp_timer_create(&commissioning_retry_timer_args,
                         &g_commissioning_retry_timer));

    // GPIO6을 50ms마다 확인하여 5초 연속 눌림을 판정합니다.
    const esp_timer_create_args_t factory_reset_button_timer_args = {
        .callback = &factory_reset_button_timer_cb,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "factory_btn",
        .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(
        esp_timer_create(&factory_reset_button_timer_args,
                         &g_factory_reset_button_timer));

    // 서보 PWM을 먼저 초기화합니다.
    // 부팅 중 잠김 -> 열림 -> 잠김 테스트 동작이 수행되므로,
    // 하드웨어 배선과 전원이 맞는지 초기 로그/동작으로 확인할 수 있습니다.
    ESP_ERROR_CHECK(g_servo_lock.init());

    // 배터리 모니터링용 ADC를 초기화합니다.
    // 실제 배터리 전압은 분압 회로를 통해 GPIO3 ADC 입력으로 들어온다고 가정합니다.
    ESP_ERROR_CHECK(g_battery_monitor.init());

    // Matter node/cluster를 만들기 전에 배터리를 한 번 읽습니다.
    // 이 값을 Power Source cluster의 초기 BatVoltage/BatPercentRemaining으로 넣어
    // Google Home이 처음 기기를 발견하는 시점부터 실제 잔량을 읽을 수 있게 합니다.
    BatterySample initial_battery_sample = {};
    ESP_ERROR_CHECK(g_battery_monitor.read(initial_battery_sample));
    if (initial_battery_sample.valid) {
        ESP_LOGI(TAG,
                 "Initial battery state for Google Home: GPIO3=%lumV pack=%lumV (%u%%)",
                 static_cast<unsigned long>(initial_battery_sample.gpio_mv),
                 static_cast<unsigned long>(initial_battery_sample.battery_mv),
                 initial_battery_sample.percent_remaining);
    } else {
        ESP_LOGE(TAG,
                 "Initial battery measurement invalid: GPIO3=%lumV reconstructed_pack=%lumV; Google Home battery percent will be NULL until wiring/measurement is valid",
                 static_cast<unsigned long>(initial_battery_sample.gpio_mv),
                 static_cast<unsigned long>(initial_battery_sample.battery_mv));
    }

    // 보조 음성 트리거가 켜졌을 때 3초 뒤 자동 잠금을 수행할 타이머를 생성합니다.
    // 실제 시작은 handle_voice_trigger(true)에서 esp_timer_start_once()로 합니다.
    const esp_timer_create_args_t auto_relock_timer_args = {
        .callback = &auto_relock_timer_cb,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "voice_relock",
        // 자동 잠금 시각을 Light-sleep의 wake-up deadline에 포함합니다.
        // true이면 잠금 타이머만으로 CPU를 깨우지 않으므로 사용하지 않습니다.
        .skip_unhandled_events = false,
    };
    ESP_ERROR_CHECK(esp_timer_create(&auto_relock_timer_args, &g_auto_relock_timer));

    const esp_timer_create_args_t battery_monitor_timer_args = {
        .callback = &battery_monitor_timer_cb,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "battery_mon",
        // 배터리 측정만을 위해 깨우지 않고 다음 wake-up에서 처리합니다.
        .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(esp_timer_create(&battery_monitor_timer_args, &g_battery_monitor_timer));

    // Matter node는 여러 endpoint를 담는 루트 객체입니다.
    // app_attribute_update_cb는 attribute 변경 시 호출되고,
    // app_identification_cb는 Identify cluster 동작 시 호출됩니다.
    node::config_t node_config;
    node_t *node = node::create(&node_config, app_attribute_update_cb, app_identification_cb);
    ESP_ERROR_CHECK(node == nullptr ? ESP_FAIL : ESP_OK);

    endpoint_t *root_endpoint = endpoint::get(node, 0);
    ESP_ERROR_CHECK(root_endpoint == nullptr ? ESP_FAIL : ESP_OK);
    ESP_ERROR_CHECK(add_battery_power_source_cluster(root_endpoint, initial_battery_sample));

    // 표준 Door Lock endpoint 설정입니다.
    // 초기 상태는 Locked로 두고, 물리 액추에이터가 있는 도어락임을 actuator_enabled로 표시합니다.
    door_lock::config_t lock_config;
    lock_config.door_lock.lock_state = static_cast<uint8_t>(LockState::Locked);
    lock_config.door_lock.lock_type = 0;
    lock_config.door_lock.actuator_enabled = true;

    // Door Lock endpoint를 생성하고 실제 endpoint id를 저장합니다.
    endpoint_t *endpoint = door_lock::create(node, &lock_config, ENDPOINT_FLAG_NONE, nullptr);
    ESP_ERROR_CHECK(endpoint == nullptr ? ESP_FAIL : ESP_OK);
    g_lock_endpoint_id = endpoint::get_id(endpoint);

    // esp-matter의 기본 door_lock endpoint에는 DoorState attribute가 없는 경우가 있어
    // Google Home과 DoorLock server 내부 처리가 실패할 수 있습니다.
    // 여기서는 DoorState를 명시적으로 추가하고, 1(door closed)로 둡니다.
    cluster_t *lock_cluster = cluster::get(endpoint, DoorLock::Id);
    ESP_ERROR_CHECK(lock_cluster == nullptr ? ESP_FAIL : ESP_OK);
    esp_matter::cluster::door_lock::attribute::create_door_state(lock_cluster, nullable<uint8_t>(1));

    // Matter attribute 상태도 초기 잠김 상태로 맞춰 둡니다.
    ESP_ERROR_CHECK(update_lock_state_attribute(LockState::Locked));
    ESP_LOGI(TAG, "Matter Door Lock endpoint created: endpoint=%u", g_lock_endpoint_id);

    // Google Assistant/Nest Mini는 Door Lock의 음성 잠금해제를 허용하지 않습니다.
    // 이를 우회하기 위해 별도 On/Off Plug-in Unit endpoint를 만들고,
    // 이 스위치의 "켜짐"을 "문을 3초 동안 열기" 트리거로 사용합니다.
    on_off_plugin_unit::config_t voice_trigger_config;
    voice_trigger_config.on_off.on_off = false;

    // 보조 스위치 endpoint를 생성하고 endpoint id를 저장합니다.
    // Google Home 앱에서는 도어락과 별개의 스위치/플러그류 기기로 보일 수 있습니다.
    endpoint_t *voice_trigger_endpoint =
        on_off_plugin_unit::create(node, &voice_trigger_config, ENDPOINT_FLAG_NONE, nullptr);
    ESP_ERROR_CHECK(voice_trigger_endpoint == nullptr ? ESP_FAIL : ESP_OK);
    g_voice_trigger_endpoint_id = endpoint::get_id(voice_trigger_endpoint);
    ESP_LOGI(TAG, "Matter voice trigger switch endpoint created: endpoint=%u", g_voice_trigger_endpoint_id);

    // Matter stack을 시작합니다.
    // 이후 Wi-Fi commissioning, CASE session, Google Home subscription 등이 Matter stack 내부에서 동작합니다.
    ESP_ERROR_CHECK(esp_matter::start(app_event_cb));

    // Matter stack이 시작된 뒤 GPIO6 reset 버튼 감시를 시작합니다.
    // factory reset API는 CHIP platform이 초기화된 이후에 호출되어야 합니다.
    ESP_ERROR_CHECK(
        esp_timer_start_periodic(g_factory_reset_button_timer,
                                 kFactoryResetPollIntervalUs));

    // Matter NetworkCommissioning이 실제 Wi-Fi 접속을 시도할 때 ESP-IDF가 제공하는 상세 끊김 사유를 남깁니다.
    // Google Home 앱에는 "액세서리를 추가할 수 없음"처럼 뭉뚱그려 보이기 때문에,
    // 여기서 reason 값을 봐야 비밀번호/보안방식/신호/공유기 거절 중 어느 쪽인지 구분할 수 있습니다.
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED,
                                               &wifi_debug_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_CONNECTED,
                                               &wifi_debug_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_START,
                                               &wifi_debug_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                           &ip_debug_event_handler, nullptr));
    apply_wifi_sta_tuning();

    // Matter stack 시작 직후, 아직 fabric이 없으면 commissioning window를 열도록 예약합니다.
    // 이미 Google Home에 등록된 장치라면 open_commissioning_window_if_needed()에서 아무 일도 하지 않습니다.
    chip::DeviceLayer::PlatformMgr().ScheduleWork(open_commissioning_window_work, 0);

    chip::DeviceLayer::PlatformMgr().ScheduleWork(battery_monitor_work, 0);
    ESP_ERROR_CHECK(esp_timer_start_periodic(g_battery_monitor_timer, kBatteryMonitorIntervalUs));

    // PWM 및 Matter 초기화가 끝난 뒤 자동 절전을 허용합니다.
    // 명령 수신/타이머 처리 후 실행할 작업이 없으면 RTOS가 다시 절전 상태로 진입합니다.
    configure_power_management();
}
