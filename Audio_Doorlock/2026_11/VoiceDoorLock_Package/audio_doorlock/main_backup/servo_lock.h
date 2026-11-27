#pragma once

#include "esp_err.h"

// MG90S 위치제어 서보모터로 잠금/잠금해제 위치를 제어하는 작은 래퍼 클래스입니다.
//
// 이 클래스는 Matter나 Google Home 같은 상위 로직을 전혀 알지 않습니다.
// 역할은 오직 다음 세 가지입니다.
//
// 1. ESP32-C3의 LEDC PWM 주변장치를 50 Hz 서보 신호용으로 초기화합니다.
// 2. "잠김" 위치와 "열림" 위치에 해당하는 PWM 펄스를 출력합니다.
// 3. 마지막으로 명령한 논리 상태를 locked_에 저장합니다.
//
// 실제 Matter endpoint, 자동 재잠금 타이머, Google Home 스위치 처리는
// app_main.cpp에서 담당합니다.
class ServoLock {
public:
    // LEDC 타이머/채널을 설정하고, 부팅 시 서보가 실제로 움직이는지 확인하기 위해
    // 잠김 -> 열림 -> 잠김 순서로 짧은 테스트 동작을 수행합니다.
    esp_err_t init();

    // 서보를 논리 0도인 원위치로 이동합니다.
    // 혼은 부팅 테스트가 끝난 이 위치에서 물리적으로 2시 방향에 장착합니다.
    esp_err_t lock();

    // 현재 설정된 열림 위치(논리 60도)로 이동합니다.
    esp_err_t unlock();

    // 마지막으로 명령한 논리 상태를 반환합니다.
    // 물리적인 센서 피드백이 아니라 소프트웨어가 기억하는 상태입니다.
    bool is_locked() const;

private:
    // 각도 값을 서보용 펄스 폭으로 변환한 뒤 실제 PWM으로 출력합니다.
    esp_err_t write_angle(int angle);

    // 마이크로초 단위 펄스 폭을 LEDC duty 값으로 변환해 하드웨어에 반영합니다.
    esp_err_t write_pulse_us(int pulse_us);

    // 마지막으로 lock()/unlock() 중 어느 명령을 수행했는지 저장합니다.
    // 실제 문이 물리적으로 잠겼는지를 검증하는 센서는 이 프로젝트에 포함되어 있지 않습니다.
    bool locked_ = true;
};
