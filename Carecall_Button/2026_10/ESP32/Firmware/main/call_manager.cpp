#include "call_manager.h"

#include <cinttypes>
#include <cstdio>
#include <cstring>

#include "call_outbox.h"
#include "call_led.h"
#include "cJSON.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mqtt_manager.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

namespace {
constexpr char TAG[] = "CALL";
constexpr std::int64_t RETRY_US = 10 * 1000 * 1000;
constexpr std::size_t MAX_ACK_SIZE = 512;
struct DatabaseAck {
    char event_id[CallOutbox::EVENT_ID_SIZE];
    bool guardian;
};

CallOutbox g_outbox;
SemaphoreHandle_t g_mutex = nullptr;
QueueHandle_t g_acks = nullptr;
TaskHandle_t g_task = nullptr;

void report_confirmation(const char* event_id, const char* status)
{
    char payload[320]{};
    const int length = std::snprintf(payload, sizeof(payload),
        "{\"schema_version\":1,\"event_type\":\"care_confirmation_result\","
        "\"event_id\":\"%s\",\"device_id\":\"%s\",\"status\":\"%s\"}",
        event_id, CONFIG_CARECALL_DEVICE_ID, status);
    int mid = -1;
    if (length > 0 && static_cast<std::size_t>(length) < sizeof(payload) &&
        mqtt_manager_is_ready_to_publish()) {
        const esp_err_t result = mqtt_manager_publish_call(payload, length, &mid);
        if (result != ESP_OK) {
            ESP_LOGW(TAG, "Confirmation result deferred; Pi will retry the command");
        }
    }
}

void delivery_task(void*)
{
    char last_attempt_id[CallOutbox::EVENT_ID_SIZE]{};
    std::int64_t retry_at = 0;
    while (true) {
        DatabaseAck ack{};
        while (xQueueReceive(g_acks, &ack, 0) == pdTRUE) {
            xSemaphoreTake(g_mutex, portMAX_DELAY);
            esp_err_t result;
            if (ack.guardian) {
                result = g_outbox.confirm_latest(ack.event_id);
                if (result == ESP_OK) {
                    result = call_led_set_waiting(g_outbox.waiting());
                }
            } else {
                result = g_outbox.acknowledge_stored(ack.event_id);
            }
            xSemaphoreGive(g_mutex);
            if (ack.guardian) {
                if (result == ESP_OK || result == ESP_ERR_NOT_FOUND) {
                    const char* status = result == ESP_OK ? "applied" : "stale";
                    ESP_LOGI(TAG, "Guardian confirmation %s: event_id=%s", status, ack.event_id);
                    report_confirmation(ack.event_id, status);
                } else {
                    ESP_LOGE(TAG, "Guardian confirmation not applied: %s", esp_err_to_name(result));
                }
                continue;
            }
            if (result == ESP_OK) {
                ESP_LOGI(TAG, "Pi database storage confirmed: event_id=%s", ack.event_id);
            } else if (result != ESP_ERR_NOT_FOUND) {
                ESP_LOGE(TAG, "Failed to persist database ACK: %s", esp_err_to_name(result));
            }
        }

        StoredCall call{};
        char event_id[CallOutbox::EVENT_ID_SIZE]{};
        xSemaphoreTake(g_mutex, portMAX_DELAY);
        const bool pending = g_outbox.front(&call);
        if (pending) {
            g_outbox.event_id(call, event_id);
        }
        xSemaphoreGive(g_mutex);

        TickType_t wait = portMAX_DELAY;
        if (pending && mqtt_manager_is_ready_to_publish()) {
            if (std::strcmp(last_attempt_id, event_id) != 0) {
                retry_at = 0;
            }
            const std::int64_t now = esp_timer_get_time();
            if (now >= retry_at) {
                char payload[320]{};
                const int length = std::snprintf(
                    payload, sizeof(payload),
                    "{\"schema_version\":1,\"event_id\":\"%s\","
                    "\"device_id\":\"%s\",\"event_type\":\"care_call\","
                    "\"sequence\":%" PRIu32 ",\"uptime_ms\":%" PRIu64 "}",
                    event_id, CONFIG_CARECALL_DEVICE_ID, call.sequence, call.uptime_ms);
                int message_id = -1;
                if (length > 0 && static_cast<std::size_t>(length) < sizeof(payload)) {
                    const esp_err_t result = mqtt_manager_publish_call(
                        payload, static_cast<std::size_t>(length), &message_id);
                    if (result != ESP_OK) {
                        ESP_LOGW(TAG, "Stored call will be retried: %s", esp_err_to_name(result));
                    }
                }
                std::strcpy(last_attempt_id, event_id);
                retry_at = now + RETRY_US;
            }
            const auto remaining_ms = (retry_at - esp_timer_get_time()) / 1000;
            wait = pdMS_TO_TICKS(remaining_ms > 0 ? remaining_ms + 1 : 1);
            if (wait == 0) {
                wait = 1;
            }
        } else {
            // Reconnection sends immediately. Offline tasks do not poll or write.
            last_attempt_id[0] = '\0';
            retry_at = 0;
        }
        ulTaskNotifyTake(pdTRUE, wait);
    }
}
}

esp_err_t call_manager_init()
{
    if (g_task != nullptr) {
        return ESP_OK;
    }
    esp_err_t result = nvs_flash_init();
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "NVS initialization failed; saved calls were not erased: %s",
                 esp_err_to_name(result));
        return result;
    }
    std::uint64_t seed = 0;
    esp_fill_random(&seed, sizeof(seed));
    result = g_outbox.open(CONFIG_CARECALL_DEVICE_ID, seed);
    if (result != ESP_OK) {
        return result;
    }
    g_mutex = xSemaphoreCreateMutex();
    g_acks = xQueueCreate(8, sizeof(DatabaseAck));
    if (g_mutex == nullptr || g_acks == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    result = call_led_init();
    if (result == ESP_OK) {
        result = call_led_set_waiting(g_outbox.waiting());
    }
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Call LED unavailable: %s; call delivery remains enabled",
                 esp_err_to_name(result));
    }
    if (xTaskCreate(delivery_task, "call_delivery", 6144, nullptr, 4, &g_task) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "Persistent call outbox ready: restored=%u, capacity=%u",
             static_cast<unsigned>(g_outbox.size()),
             static_cast<unsigned>(CallOutbox::CAPACITY));
    return ESP_OK;
}

esp_err_t call_manager_request_call()
{
    if (g_task == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    StoredCall accepted{};
    char event_id[CallOutbox::EVENT_ID_SIZE]{};
    xSemaphoreTake(g_mutex, portMAX_DELAY);
    const esp_err_t result = g_outbox.append(
        static_cast<std::uint64_t>(esp_timer_get_time()) / 1000, &accepted);
    if (result == ESP_OK) {
        g_outbox.event_id(accepted, event_id);
        const esp_err_t led_result = call_led_set_waiting(true);
        if (led_result != ESP_OK) {
            ESP_LOGE(TAG, "Call accepted but LED update failed: %s", esp_err_to_name(led_result));
        }
    }
    xSemaphoreGive(g_mutex);
    if (result == ESP_OK) {
        ESP_LOGI(TAG, "Call durably accepted: event_id=%s", event_id);
        xTaskNotifyGive(g_task);
    } else {
        ESP_LOGE(TAG, "Call not accepted; outbox full or storage unavailable: %s",
                 esp_err_to_name(result));
    }
    return result;
}

void call_manager_notify_transport_changed()
{
    if (g_task != nullptr) {
        xTaskNotifyGive(g_task);
    }
}

void call_manager_receive_database_ack(const char* payload, std::size_t length)
{
    if (g_acks == nullptr || payload == nullptr || length == 0 || length > MAX_ACK_SIZE) {
        return;
    }
    char text[MAX_ACK_SIZE + 1]{};
    std::memcpy(text, payload, length);
    if (std::memchr(text, '\0', length) != nullptr) {
        return;
    }
    cJSON* root = cJSON_ParseWithLengthOpts(text, length + 1, nullptr, true);
    if (root == nullptr) {
        return;
    }
    const cJSON* id = cJSON_GetObjectItemCaseSensitive(root, "event_id");
    const cJSON* device = cJSON_GetObjectItemCaseSensitive(root, "device_id");
    const cJSON* status = cJSON_GetObjectItemCaseSensitive(root, "status");
    // Exactly these three fields: reject duplicate keys as well.
    if (cJSON_IsObject(root) && cJSON_GetArraySize(root) == 3 &&
        cJSON_IsString(id) && cJSON_IsString(device) && cJSON_IsString(status) &&
        std::strlen(id->valuestring) > 0 &&
        std::strlen(id->valuestring) < CallOutbox::EVENT_ID_SIZE &&
        std::strspn(id->valuestring,
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") ==
            std::strlen(id->valuestring) &&
        std::strcmp(device->valuestring, CONFIG_CARECALL_DEVICE_ID) == 0 &&
        (std::strcmp(status->valuestring, "stored") == 0 ||
         std::strcmp(status->valuestring, "confirmed") == 0)) {
        DatabaseAck ack{};
        ack.guardian = std::strcmp(status->valuestring, "confirmed") == 0;
        std::strcpy(ack.event_id, id->valuestring);
        if (xQueueSend(g_acks, &ack, 0) == pdTRUE) {
            call_manager_notify_transport_changed();
        }
        // Full ACK queue: keep the call; the same event_id will be retried.
    }
    cJSON_Delete(root);
}
