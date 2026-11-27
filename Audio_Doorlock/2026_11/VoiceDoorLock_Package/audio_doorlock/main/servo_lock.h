#pragma once

#include "esp_err.h"
#include "esp_timer.h"

#include "sdkconfig.h"

#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
#include "esp_pm.h"
#endif

// MG90S 위치제어 서보모터 래퍼.
//
// 이번 버전의 핵심:
// - 서보 VCC/GND 배선은 기존 그대로 유지합니다.
// - MOSFET/전자식 전원 스위치를 추가하지 않습니다.
// - lock()/unlock() 명령이 들어올 때만 50 Hz PWM을 출력합니다.
// - 이동 시간을 준 뒤 Signal을 LOW(0% duty)로 만들어 PWM holding을 중지합니다.
// - ESP32-C3가 자동 Light-sleep을 사용할 때, 실제 서보 이동 중에만
//   ESP_PM_NO_LIGHT_SLEEP lock을 잠깐 잡고 이동이 끝나면 즉시 해제합니다.
//
// 따라서 이것은 "서보 전원 차단"이 아니라 "PWM/holding release idle"입니다.
// 서보 내부 전자회로에는 계속 전원이 공급되므로 소비전류가 완전히 0이 되지는 않습니다.
// 또한 PWM이 끊긴 상태에서 기계적인 holding torque가 사라질 수 있으므로,
// 실제 도어락 기구가 외력으로 움직이지 않는지 반드시 확인해야 합니다.
class ServoLock {
public:
    // LEDC와 PWM release timer를 초기화합니다.
    // 기존의 잠김 -> 열림 -> 잠김 부팅 테스트는 제거하고,
    // 부팅 시 잠김 위치만 한 번 지시한 뒤 PWM을 자동으로 해제합니다.
    esp_err_t init();

    // 잠김 위치로 이동합니다.
    // 이동 후 PWM은 자동으로 idle(LOW) 상태가 됩니다.
    esp_err_t lock();

    // 열림 위치로 이동합니다.
    // 이동 후 PWM은 자동으로 idle(LOW) 상태가 됩니다.
    esp_err_t unlock();

    // 마지막으로 요청한 논리 상태입니다.
    // 실제 물리 잠금 상태를 검출하는 센서 값은 아닙니다.
    bool is_locked() const;

private:
    esp_err_t command_angle(int angle);
    esp_err_t write_angle(int angle);
    esp_err_t write_pulse_us(int pulse_us);

    // PWM을 0% duty로 만들어 서보 position command를 중지하고
    // movement용 PM lock을 해제합니다.
    esp_err_t release_pwm();

    static void release_timer_cb(void *arg);

    bool initialized_ = false;
    bool locked_ = true;

    esp_timer_handle_t release_timer_ = nullptr;

    // 최신 PWM 명령 시각. 이전 one-shot callback과 새 명령이 경합할 때
    // 새 동작을 조기에 끊지 않도록 사용합니다.
    int64_t last_command_started_us_ = 0;

#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
    esp_pm_lock_handle_t no_light_sleep_lock_ = nullptr;
    bool no_light_sleep_lock_held_ = false;
#endif
};
