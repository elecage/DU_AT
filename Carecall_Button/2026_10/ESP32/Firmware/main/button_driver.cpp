#include "button_driver.h"

#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {

constexpr char TAG[] = "BUTTON";

constexpr gpio_num_t BUTTON_GPIO = GPIO_NUM_4;

// GPIO 상태를 20ms마다 읽습니다.
constexpr int POLL_INTERVAL_MS = 20;

// 같은 상태가 80ms 동안 유지되어야 실제 변경으로 인정합니다.
constexpr int DEBOUNCE_TIME_MS = 80;

constexpr int REQUIRED_STABLE_SAMPLES =
    DEBOUNCE_TIME_MS / POLL_INTERVAL_MS;

ButtonPressedCallback g_pressed_callback = nullptr;

void button_task(void* parameter)
{
    (void)parameter;

    int stable_level = gpio_get_level(BUTTON_GPIO);
    int candidate_level = stable_level;
    int stable_sample_count = 0;

    ESP_LOGI(
        TAG,
        "Button monitoring started: GPIO=%d, initial_level=%d",
        static_cast<int>(BUTTON_GPIO),
        stable_level
    );

    while (true) {
        const int raw_level = gpio_get_level(BUTTON_GPIO);

        if (raw_level == candidate_level) {
            if (stable_sample_count < REQUIRED_STABLE_SAMPLES) {
                stable_sample_count++;
            }
        } else {
            candidate_level = raw_level;
            stable_sample_count = 1;
        }

        const bool state_is_stable =
            stable_sample_count >= REQUIRED_STABLE_SAMPLES;

        const bool state_has_changed =
            candidate_level != stable_level;

        if (state_is_stable && state_has_changed) {
            stable_level = candidate_level;

            if (stable_level == 0) {
                // Active-Low이므로 LOW가 눌림입니다.
                ESP_LOGI(TAG, "Call button pressed");

                if (g_pressed_callback != nullptr) {
                    g_pressed_callback();
                }
            } else {
                ESP_LOGI(TAG, "Call button released");
            }
        }

        vTaskDelay(pdMS_TO_TICKS(POLL_INTERVAL_MS));
    }
}

}  // namespace

esp_err_t button_driver_init(ButtonPressedCallback callback)
{
    if (callback == nullptr) {
        ESP_LOGE(TAG, "Button callback is null");
        return ESP_ERR_INVALID_ARG;
    }

    g_pressed_callback = callback;

    gpio_config_t io_config{};

    io_config.pin_bit_mask = 1ULL << GPIO_NUM_4;
    io_config.mode = GPIO_MODE_INPUT;
    io_config.pull_up_en = GPIO_PULLUP_ENABLE;
    io_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_config.intr_type = GPIO_INTR_DISABLE;

    const esp_err_t gpio_result = gpio_config(&io_config);

    if (gpio_result != ESP_OK) {
        ESP_LOGE(
            TAG,
            "GPIO4 configuration failed: %s",
            esp_err_to_name(gpio_result)
        );

        return gpio_result;
    }

    const BaseType_t task_result = xTaskCreate(
        button_task,
        "button_task",
        6144,
        nullptr,
        5,
        nullptr
    );

    if (task_result != pdPASS) {
        ESP_LOGE(TAG, "Failed to create button task");
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(
        TAG,
        "GPIO4 configured as active-low input with internal pull-up"
    );

    return ESP_OK;
}
