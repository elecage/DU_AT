#pragma once

#include "esp_err.h"

using ButtonPressedCallback = void (*)();

/**
 * @brief GPIO4 호출 버튼을 초기화하고 버튼 감시 Task를 시작합니다.
 *
 * 배선:
 *   버튼 NO  -> GPIO4
 *   버튼 COM -> GND
 *
 * 논리:
 *   버튼 해제 -> HIGH
 *   버튼 누름 -> LOW
 */
esp_err_t button_driver_init(ButtonPressedCallback callback);
