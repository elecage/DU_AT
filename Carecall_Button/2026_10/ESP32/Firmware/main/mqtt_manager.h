#pragma once

#include <cstddef>

#include "esp_err.h"

/**
 * @brief MQTT 클라이언트를 초기화하고 Broker 연결을 시작합니다.
 *
 * Wi-Fi에서 IPv4 주소를 받은 뒤 호출해야 합니다.
 */
esp_err_t mqtt_manager_init();

/**
 * @brief MQTT Broker와 현재 연결되어 있는지 반환합니다.
 */
bool mqtt_manager_is_connected();

bool mqtt_manager_is_ready_to_publish();
/**
 * @brief 호출 JSON을 장치의 call 토픽으로 QoS 1 발행 큐에 넣습니다.
 *
 * MQTT Broker와 연결된 상태에서만 호출할 수 있습니다.
 * Retain은 false로 고정됩니다.
 *
 * @param payload 전송할 JSON 바이트 배열
 * @param payload_length JSON 길이(널 종료 문자 제외)
 * @param message_id 성공 시 MQTT message ID가 저장될 포인터
 */
esp_err_t mqtt_manager_publish_call(
    const char* payload,
    std::size_t payload_length,
    int* message_id
);
