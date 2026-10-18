#pragma once
#include "esp_err.h"

esp_err_t call_led_init();
// Caller serializes this with durable call/confirmation state changes.
esp_err_t call_led_set_waiting(bool waiting);
