#include "button_driver.h"
#include "call_manager.h"
#include "mqtt_manager.h"
#include "wifi_manager.h"

#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {

constexpr char TAG[] = "CARECALL";

void on_call_button_pressed()
{
    ESP_LOGW(TAG, "CALL REQUEST detected");

    const esp_err_t result = call_manager_request_call();

    if (result != ESP_OK) {
        ESP_LOGW(
            TAG,
            "Call request was not queued: %s",
            esp_err_to_name(result)
        );
    }
}

}  // namespace

extern "C" void app_main(void)
{
    ESP_LOGI(
        TAG,
        "CareCall ESP32-C3 firmware started"
    );

    ESP_LOGI(
        TAG,
        "Hardware profile: GPIO4 call button, "
        "Wi-Fi Station and MQTT"
    );

    ESP_ERROR_CHECK(
        call_manager_init()
    );

    ESP_ERROR_CHECK(
        button_driver_init(on_call_button_pressed)
    );

    ESP_LOGI(
        TAG,
        "Call button system is ready"
    );

    ESP_ERROR_CHECK(
        wifi_manager_init()
    );

    ESP_LOGI(
        TAG,
        "Waiting for Wi-Fi IPv4 address"
    );

    /*
     * app_main만 기다립니다.
     * 버튼 감시 Task는 별도로 실행되므로 버튼 동작은 계속 유지됩니다.
     */
    while (!wifi_manager_is_connected()) {
        vTaskDelay(pdMS_TO_TICKS(100));
    }

    ESP_LOGI(
        TAG,
        "Wi-Fi IPv4 address acquired; starting MQTT"
    );

    ESP_ERROR_CHECK(
        mqtt_manager_init()
    );

    ESP_LOGI(
        TAG,
        "MQTT initialization is ready"
    );
}
