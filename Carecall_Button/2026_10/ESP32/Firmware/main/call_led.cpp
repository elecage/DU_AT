#include "call_led.h"
#include "esp_log.h"
#include "sdkconfig.h"

#if CONFIG_CARECALL_LED_GPIO >= 0
#include "driver/gpio.h"
#include "led_strip.h"
namespace {
// One WS2812 ring module containing 16 individually addressable pixels.
constexpr int LED_COUNT = 16;
led_strip_handle_t g_strip = nullptr;
}
#endif

esp_err_t call_led_init()
{
#if CONFIG_CARECALL_LED_GPIO < 0
    ESP_LOGW("CALL_LED", "LED output disabled: set CareCall LED GPIO when wiring is ready");
    return ESP_OK;
#else
    constexpr int pin = CONFIG_CARECALL_LED_GPIO;
    if (!GPIO_IS_VALID_OUTPUT_GPIO(pin) || pin == 4 || pin == 18 || pin == 19) {
        ESP_LOGE("CALL_LED", "Invalid LED GPIO or conflict with call button / native USB");
        return ESP_ERR_INVALID_ARG;
    }
    led_strip_config_t strip{};
    strip.strip_gpio_num = pin;
    strip.max_leds = LED_COUNT;
    strip.led_pixel_format = LED_PIXEL_FORMAT_GRB;
    strip.led_model = LED_MODEL_WS2812;
    led_strip_rmt_config_t rmt{};
    rmt.clk_src = RMT_CLK_SRC_DEFAULT;
    rmt.resolution_hz = 10 * 1000 * 1000;
    rmt.flags.with_dma = false;
    const esp_err_t result = led_strip_new_rmt_device(&strip, &rmt, &g_strip);
    if (result == ESP_OK) {
        ESP_LOGI("CALL_LED", "NeoPixel ready: gpio=%d, leds=%d, brightness=%d",
                 pin, LED_COUNT, CONFIG_CARECALL_LED_BRIGHTNESS);
    }
    return result;
#endif
}

esp_err_t call_led_set_waiting(bool waiting)
{
#if CONFIG_CARECALL_LED_GPIO < 0
    ESP_LOGI("CALL_LED", "Waiting state=%s (physical LED disabled)", waiting ? "ON" : "OFF");
    return ESP_OK;
#else
    if (g_strip == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    for (int i = 0; i < LED_COUNT; ++i) {
        const auto result = led_strip_set_pixel(g_strip, i,
            waiting ? CONFIG_CARECALL_LED_BRIGHTNESS : 0,
            waiting ? CONFIG_CARECALL_LED_BRIGHTNESS / 2 : 0, 0);
        if (result != ESP_OK) {
            return result;
        }
    }
    return led_strip_refresh(g_strip);
#endif
}
