#pragma once

#include <cstddef>

#include "esp_err.h"

// 버튼을 활성화하기 전에 호출합니다.
esp_err_t call_manager_init();

// 성공은 ESP32 내부 저장 완료를 의미합니다.
esp_err_t call_manager_request_call();

void call_manager_notify_transport_changed();

void call_manager_receive_database_ack(
    const char* payload,
    std::size_t length
);
