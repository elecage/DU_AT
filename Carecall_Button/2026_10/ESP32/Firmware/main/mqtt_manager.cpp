#include "mqtt_manager.h"
#include "call_manager.h"

#include <atomic>
#include <climits>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif_ip_addr.h"
#include "mdns.h"
#include "mqtt_client.h"
#include "sdkconfig.h"

namespace {

constexpr char TAG[] = "MQTT";

constexpr char CALL_TOPIC[] =
    "carecall/v1/devices/"
    CONFIG_CARECALL_DEVICE_ID
    "/call";

constexpr char ACK_TOPIC[] =
    "carecall/v1/devices/"
    CONFIG_CARECALL_DEVICE_ID
    "/ack";

constexpr int CALL_QOS = 1;
constexpr int CALL_RETAIN = 0;
constexpr int ACK_QOS = 1;

constexpr uint32_t MDNS_QUERY_TIMEOUT_MS = 2000;
constexpr uint32_t RECONNECT_DELAY_MS = 5000;
constexpr uint32_t EVENT_HANDLER_RETURN_DELAY_MS = 100;

constexpr uint32_t MQTT_MANAGER_TASK_STACK_SIZE = 6144;
constexpr UBaseType_t MQTT_MANAGER_TASK_PRIORITY = 4;

esp_mqtt_client_handle_t g_mqtt_client = nullptr;
TaskHandle_t g_mqtt_manager_task = nullptr;

std::atomic_bool g_connected{false};
std::atomic_bool g_ack_subscribed{false};
int g_ack_subscription_id = -1;
std::atomic_bool g_initialized{false};

bool is_valid_device_id(const char* device_id)
{
    if (device_id == nullptr || device_id[0] == '\0') {
        return false;
    }

    for (const char* cursor = device_id;
         *cursor != '\0';
         ++cursor) {

        const char value = *cursor;

        const bool is_lowercase =
            value >= 'a' && value <= 'z';

        const bool is_uppercase =
            value >= 'A' && value <= 'Z';

        const bool is_digit =
            value >= '0' && value <= '9';

        if (!is_lowercase &&
            !is_uppercase &&
            !is_digit &&
            value != '-' &&
            value != '_') {

            return false;
        }
    }

    return true;
}

esp_err_t validate_mqtt_configuration()
{
    if (!is_valid_device_id(CONFIG_CARECALL_DEVICE_ID) ||
        std::strlen(CONFIG_CARECALL_DEVICE_ID) > 32) {

        ESP_LOGE(
            TAG,
            "Device ID must be 1-32 characters and contain only "
            "letters, digits, '-' or '_'"
        );
        return ESP_ERR_INVALID_ARG;
    }

    if (std::strlen(
            CONFIG_CARECALL_MQTT_BROKER_MDNS_HOST
        ) == 0) {

        ESP_LOGE(TAG, "MQTT Broker mDNS hostname is empty");
        return ESP_ERR_INVALID_ARG;
    }

    if (CONFIG_CARECALL_MQTT_BROKER_PORT < 1 ||
        CONFIG_CARECALL_MQTT_BROKER_PORT > 65535) {

        ESP_LOGE(TAG, "MQTT Broker port is invalid");
        return ESP_ERR_INVALID_ARG;
    }

    if (std::strlen(CONFIG_CARECALL_MQTT_USERNAME) == 0) {
        ESP_LOGE(TAG, "MQTT username is empty");
        return ESP_ERR_INVALID_ARG;
    }

    if (std::strlen(CONFIG_CARECALL_MQTT_PASSWORD) == 0) {
        ESP_LOGE(TAG, "MQTT password is empty");
        return ESP_ERR_INVALID_ARG;
    }

    if (std::strlen(CONFIG_CARECALL_MQTT_CLIENT_ID) == 0) {
        ESP_LOGE(TAG, "MQTT Client ID is empty");
        return ESP_ERR_INVALID_ARG;
    }

    return ESP_OK;
}

bool is_ack_topic(const esp_mqtt_event_handle_t event)
{
    if (event == nullptr || event->topic == nullptr) {
        return false;
    }

    const int expected_length =
        static_cast<int>(std::strlen(ACK_TOPIC));

    return event->topic_len == expected_length &&
           std::memcmp(
               event->topic,
               ACK_TOPIC,
               expected_length
           ) == 0;
}

void notify_mqtt_manager_of_disconnect()
{
    if (g_mqtt_manager_task != nullptr) {
        xTaskNotifyGive(g_mqtt_manager_task);
    }
}

void mqtt_event_handler(
    void* handler_argument,
    esp_event_base_t event_base,
    int32_t event_id,
    void* event_data
)
{
    (void)handler_argument;
    (void)event_base;

    const auto event =
        static_cast<esp_mqtt_event_handle_t>(event_data);

    if (event == nullptr) {
        ESP_LOGE(TAG, "MQTT event data is null");
        return;
    }

    switch (static_cast<esp_mqtt_event_id_t>(event_id)) {
        case MQTT_EVENT_CONNECTED: {
            g_connected.store(true);
            g_ack_subscribed.store(false);

            ESP_LOGI(TAG, "Connected to MQTT Broker");

            const int message_id = esp_mqtt_client_subscribe(
                event->client,
                ACK_TOPIC,
                ACK_QOS
            );

            g_ack_subscription_id = message_id;

            if (message_id < 0) {
                ESP_LOGE(
                    TAG,
                    "ACK subscription request failed: result=%d",
                    message_id
                );

                esp_mqtt_client_disconnect(event->client);
            } else {
                ESP_LOGI(
                    TAG,
                    "ACK subscription requested: "
                    "topic=%s, qos=%d, message_id=%d",
                    ACK_TOPIC,
                    ACK_QOS,
                    message_id
                );
            }

            break;
        }

        case MQTT_EVENT_DISCONNECTED:
            g_connected.store(false);
            g_ack_subscribed.store(false);
            call_manager_notify_transport_changed();

            ESP_LOGW(
                TAG,
                "Disconnected from MQTT Broker; "
                "mDNS rediscovery will be attempted"
            );

            notify_mqtt_manager_of_disconnect();
            break;

        case MQTT_EVENT_SUBSCRIBED:
            if (event->msg_id == g_ack_subscription_id) {
                // 단일 토픽을 QoS 1로 요청했습니다.
                // 0x80 등 거절 응답은 성공으로 처리하지 않습니다.
                const bool accepted =
                    event->data != nullptr &&
                    event->data_len == 1 &&
                    static_cast<unsigned char>(event->data[0]) <= ACK_QOS;

                g_ack_subscribed.store(accepted);
                call_manager_notify_transport_changed();

                if (!accepted) {
                    ESP_LOGE(
                        TAG,
                        "ACK subscription rejected or malformed; reconnecting"
                    );

                    esp_mqtt_client_disconnect(event->client);
                }
            }

            ESP_LOGI(
                TAG,
                "MQTT SUBACK received: message_id=%d",
                event->msg_id
            );
            break;

        case MQTT_EVENT_PUBLISHED:
            ESP_LOGI(
                TAG,
                "MQTT PUBACK received: message_id=%d",
                event->msg_id
            );
            break;

        case MQTT_EVENT_DATA:
            if (is_ack_topic(event)) {
                // 완전한 ACK만 전달합니다.
                // 불완전한 경우 호출을 보존하고 재시도합니다.
                if (event->current_data_offset == 0 &&
                    event->data_len == event->total_data_len &&
                    event->data_len > 0) {
                    call_manager_receive_database_ack(
                        event->data,
                        static_cast<std::size_t>(event->data_len)
                    );
                }

                ESP_LOGI(
                    TAG,
                    "Application ACK received: "
                    "topic=%.*s, payload=%.*s",
                    event->topic_len,
                    event->topic,
                    event->data_len,
                    event->data != nullptr ? event->data : ""
                );
            } else {
                ESP_LOGW(
                    TAG,
                    "Message received from unexpected topic: %.*s",
                    event->topic_len,
                    event->topic != nullptr ? event->topic : ""
                );
            }
            break;

        case MQTT_EVENT_ERROR:
            g_connected.store(false);
            g_ack_subscribed.store(false);
            call_manager_notify_transport_changed();

            if (event->error_handle == nullptr) {
                ESP_LOGE(TAG, "MQTT error occurred");
                break;
            }

            if (event->error_handle->error_type ==
                MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
                ESP_LOGE(
                    TAG,
                    "Broker refused MQTT connection: return_code=%d",
                    static_cast<int>(
                        event->error_handle->connect_return_code
                    )
                );
            } else if (
                event->error_handle->error_type ==
                MQTT_ERROR_TYPE_TCP_TRANSPORT) {
                ESP_LOGE(
                    TAG,
                    "MQTT TCP transport error: "
                    "esp_error=0x%x, socket_errno=%d",
                    static_cast<unsigned int>(
                        event->error_handle->esp_tls_last_esp_err
                    ),
                    event->error_handle->esp_transport_sock_errno
                );
            } else {
                ESP_LOGE(
                    TAG,
                    "MQTT error occurred: error_type=%d",
                    static_cast<int>(
                        event->error_handle->error_type
                    )
                );
            }

            break;

        default:
            break;
    }
}

bool resolve_broker_uri(
    char* broker_uri,
    const std::size_t broker_uri_size
)
{
    esp_ip4_addr_t broker_address{};

    ESP_LOGI(
        TAG,
        "Resolving MQTT Broker by mDNS: %s.local",
        CONFIG_CARECALL_MQTT_BROKER_MDNS_HOST
    );

    const esp_err_t result =
        mdns_query_a(
            CONFIG_CARECALL_MQTT_BROKER_MDNS_HOST,
            MDNS_QUERY_TIMEOUT_MS,
            &broker_address
        );

    if (result != ESP_OK) {
        if (result == ESP_ERR_NOT_FOUND) {
            ESP_LOGW(
                TAG,
                "mDNS host not found: %s.local",
                CONFIG_CARECALL_MQTT_BROKER_MDNS_HOST
            );
        } else {
            ESP_LOGW(
                TAG,
                "mDNS query failed: host=%s.local, error=%s",
                CONFIG_CARECALL_MQTT_BROKER_MDNS_HOST,
                esp_err_to_name(result)
            );
        }

        return false;
    }

    const int written =
        std::snprintf(
            broker_uri,
            broker_uri_size,
            "mqtt://" IPSTR ":%d",
            IP2STR(&broker_address),
            CONFIG_CARECALL_MQTT_BROKER_PORT
        );

    if (written < 0 ||
        static_cast<std::size_t>(written) >= broker_uri_size) {

        ESP_LOGE(TAG, "MQTT Broker URI buffer is too small");
        return false;
    }

    ESP_LOGI(
        TAG,
        "mDNS resolved: %s.local -> " IPSTR,
        CONFIG_CARECALL_MQTT_BROKER_MDNS_HOST,
        IP2STR(&broker_address)
    );

    return true;
}

esp_mqtt_client_config_t make_mqtt_configuration(
    const char* broker_uri
)
{
    esp_mqtt_client_config_t mqtt_config{};

    mqtt_config.broker.address.uri = broker_uri;

    mqtt_config.credentials.username =
        CONFIG_CARECALL_MQTT_USERNAME;

    mqtt_config.credentials.client_id =
        CONFIG_CARECALL_MQTT_CLIENT_ID;

    mqtt_config.credentials.authentication.password =
        CONFIG_CARECALL_MQTT_PASSWORD;

    mqtt_config.session.protocol_ver =
        MQTT_PROTOCOL_V_3_1_1;

    mqtt_config.session.keepalive = 60;

    // 과거 IP로 ESP-MQTT가 자동 재연결하지 않도록 끕니다.
    // 이 파일의 관리 작업이 mDNS를 다시 조회한 뒤 재연결합니다.
    mqtt_config.network.disable_auto_reconnect = true;

    return mqtt_config;
}

bool create_and_start_mqtt_client(const char* broker_uri)
{
    const esp_mqtt_client_config_t mqtt_config =
        make_mqtt_configuration(broker_uri);

    g_mqtt_client =
        esp_mqtt_client_init(&mqtt_config);

    if (g_mqtt_client == nullptr) {
        ESP_LOGE(TAG, "Failed to create MQTT client");
        return false;
    }

    esp_err_t result =
        esp_mqtt_client_register_event(
            g_mqtt_client,
            MQTT_EVENT_ANY,
            mqtt_event_handler,
            nullptr
        );

    if (result != ESP_OK) {
        ESP_LOGE(
            TAG,
            "MQTT event registration failed: %s",
            esp_err_to_name(result)
        );

        esp_mqtt_client_destroy(g_mqtt_client);
        g_mqtt_client = nullptr;

        return false;
    }

    result = esp_mqtt_client_start(g_mqtt_client);

    if (result != ESP_OK) {
        ESP_LOGE(
            TAG,
            "MQTT client start failed: %s",
            esp_err_to_name(result)
        );

        esp_mqtt_client_destroy(g_mqtt_client);
        g_mqtt_client = nullptr;

        return false;
    }

    ESP_LOGI(
        TAG,
        "MQTT client started: broker=%s, client_id=%s",
        broker_uri,
        CONFIG_CARECALL_MQTT_CLIENT_ID
    );

    return true;
}

void mqtt_manager_task(void* task_argument)
{
    (void)task_argument;

    char broker_uri[64]{};

    while (!resolve_broker_uri(broker_uri, sizeof(broker_uri)) ||
           !create_and_start_mqtt_client(broker_uri)) {

        ESP_LOGW(
            TAG,
            "MQTT startup will be retried in %lu ms",
            static_cast<unsigned long>(RECONNECT_DELAY_MS)
        );

        vTaskDelay(pdMS_TO_TICKS(RECONNECT_DELAY_MS));
    }

    while (true) {
        // MQTT_EVENT_DISCONNECTED에서 전달되는 알림을 기다립니다.
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        // 이벤트 콜백이 완전히 반환된 뒤 MQTT API를 호출합니다.
        vTaskDelay(
            pdMS_TO_TICKS(EVENT_HANDLER_RETURN_DELAY_MS)
        );

        ESP_LOGW(
            TAG,
            "Waiting %lu ms before mDNS rediscovery",
            static_cast<unsigned long>(RECONNECT_DELAY_MS)
        );

        vTaskDelay(pdMS_TO_TICKS(RECONNECT_DELAY_MS));

        while (true) {
            if (!resolve_broker_uri(
                    broker_uri,
                    sizeof(broker_uri))) {

                ESP_LOGW(
                    TAG,
                    "mDNS rediscovery will be retried in %lu ms",
                    static_cast<unsigned long>(RECONNECT_DELAY_MS)
                );

                vTaskDelay(pdMS_TO_TICKS(RECONNECT_DELAY_MS));
                continue;
            }

            esp_err_t result =
                esp_mqtt_client_set_uri(
                    g_mqtt_client,
                    broker_uri
                );

            if (result != ESP_OK) {
                ESP_LOGE(
                    TAG,
                    "MQTT Broker URI update failed: %s",
                    esp_err_to_name(result)
                );

                vTaskDelay(pdMS_TO_TICKS(RECONNECT_DELAY_MS));
                continue;
            }

            result = esp_mqtt_client_reconnect(g_mqtt_client);

            if (result != ESP_OK) {
                ESP_LOGW(
                    TAG,
                    "MQTT reconnect request failed: %s; "
                    "retrying in %lu ms",
                    esp_err_to_name(result),
                    static_cast<unsigned long>(RECONNECT_DELAY_MS)
                );

                vTaskDelay(pdMS_TO_TICKS(RECONNECT_DELAY_MS));
                continue;
            }

            ESP_LOGI(
                TAG,
                "MQTT reconnect requested with refreshed address: %s",
                broker_uri
            );

            break;
        }
    }
}

}  // namespace

esp_err_t mqtt_manager_init()
{
    bool expected = false;

    if (!g_initialized.compare_exchange_strong(
            expected,
            true)) {

        ESP_LOGW(TAG, "MQTT manager is already initialized");
        return ESP_OK;
    }

    const esp_err_t validation_result =
        validate_mqtt_configuration();

    if (validation_result != ESP_OK) {
        g_initialized.store(false);
        return validation_result;
    }

    const esp_err_t mdns_result = mdns_init();

    if (mdns_result != ESP_OK) {
        ESP_LOGE(
            TAG,
            "mDNS initialization failed: %s",
            esp_err_to_name(mdns_result)
        );

        g_initialized.store(false);
        return mdns_result;
    }

    const BaseType_t task_result =
        xTaskCreate(
            mqtt_manager_task,
            "mqtt_mdns_mgr",
            MQTT_MANAGER_TASK_STACK_SIZE,
            nullptr,
            MQTT_MANAGER_TASK_PRIORITY,
            &g_mqtt_manager_task
        );

    if (task_result != pdPASS) {
        ESP_LOGE(TAG, "Failed to create MQTT manager task");

        mdns_free();
        g_mqtt_manager_task = nullptr;
        g_initialized.store(false);

        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(
        TAG,
        "MQTT manager initialized: mDNS host=%s.local, port=%d",
        CONFIG_CARECALL_MQTT_BROKER_MDNS_HOST,
        CONFIG_CARECALL_MQTT_BROKER_PORT
    );

    return ESP_OK;
}

bool mqtt_manager_is_connected()
{
    return g_connected.load();
}

bool mqtt_manager_is_ready_to_publish()
{
    return g_connected.load() && g_ack_subscribed.load();
}

esp_err_t mqtt_manager_publish_call(
    const char* payload,
    const std::size_t payload_length,
    int* message_id
)
{
    if (payload == nullptr ||
        payload_length == 0 ||
        message_id == nullptr) {

        ESP_LOGE(TAG, "Call publish arguments are invalid");
        return ESP_ERR_INVALID_ARG;
    }

    if (payload_length > static_cast<std::size_t>(INT_MAX)) {
        ESP_LOGE(TAG, "Call payload is too large");
        return ESP_ERR_INVALID_ARG;
    }

    if (!g_initialized.load() ||
        g_mqtt_client == nullptr ||
        !mqtt_manager_is_ready_to_publish()) {

        ESP_LOGW(
            TAG,
            "Call publish rejected: MQTT is not connected"
        );
        return ESP_ERR_INVALID_STATE;
    }

    const int queued_message_id =
        esp_mqtt_client_enqueue(
            g_mqtt_client,
            CALL_TOPIC,
            payload,
            static_cast<int>(payload_length),
            CALL_QOS,
            CALL_RETAIN,
            true
        );

    if (queued_message_id < 0) {
        ESP_LOGE(
            TAG,
            "Call publish queue failed: result=%d",
            queued_message_id
        );

        if (queued_message_id == -2) {
            return ESP_ERR_NO_MEM;
        }

        return ESP_FAIL;
    }

    *message_id = queued_message_id;

    ESP_LOGI(
        TAG,
        "Call publish queued: topic=%s, qos=%d, "
        "retain=false, message_id=%d, payload=%.*s",
        CALL_TOPIC,
        CALL_QOS,
        queued_message_id,
        static_cast<int>(payload_length),
        payload
    );

    return ESP_OK;
}
