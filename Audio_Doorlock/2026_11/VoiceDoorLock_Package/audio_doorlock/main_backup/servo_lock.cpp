#include "servo_lock.h"

#include "driver/ledc.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {
constexpr char TAG[] = "servo_lock";

// ESP32-C3 Super Mini에서 RC 서보의 Signal 선을 연결한 GPIO입니다.
// 현재 하드웨어 배선은 GPIO4를 기준으로 맞춰져 있습니다.
constexpr gpio_num_t kServoGpio = GPIO_NUM_4;

// LEDC는 조명용 LED가 아니라 ESP-IDF의 PWM 주변장치 이름입니다.
// MG90S 위치 제어에 필요한 PWM 신호를 만들기 위해 타이머와 채널을 하나씩 사용합니다.
constexpr ledc_timer_t kTimer = LEDC_TIMER_0;
constexpr ledc_channel_t kChannel = LEDC_CHANNEL_0;
constexpr ledc_mode_t kSpeedMode = LEDC_LOW_SPEED_MODE;

// 14-bit 해상도는 50 Hz 서보 펄스를 충분히 부드럽게 표현하기 위한 값입니다.
// duty 범위는 0부터 16383까지가 됩니다.
constexpr ledc_timer_bit_t kDutyResolution = LEDC_TIMER_14_BIT;

// MG90S는 50 Hz, 즉 20 ms 주기의 제어 신호를 사용합니다.
// 한 주기 안에서 HIGH 펄스 폭이 약 0.5 ms ~ 2.5 ms 사이로 바뀌며 각도를 결정합니다.
constexpr int kPwmFrequencyHz = 50;
constexpr int kPwmPeriodUs = 1000000 / kPwmFrequencyHz;
constexpr int kDutyMax = (1 << 14) - 1;

// 논리적인 잠김 위치는 0도, 열림 위치는 현재 코드와 같은 60도입니다.
// 전원을 끈 상태에서 혼을 분리한 뒤, 부팅 테스트가 끝나 잠김 위치에 정지했을 때
// 혼을 2시 방향으로 장착하면 2시가 실제 원위치가 됩니다.
constexpr int kLockedAngle = 0;
constexpr int kUnlockedAngle = 60;

// 축/혼의 정면에서 보았을 때 펄스 폭 증가 방향을 시계방향으로 사용합니다.
// 실제 보유한 MG90S가 반시계방향으로 움직이면 이 값만 false로 바꾸면 됩니다.
constexpr bool kClockwiseUsesIncreasingPulse = true;

// 일반적인 아날로그 RC 서보의 제어 펄스 범위입니다.
// 0도는 kMinPulseUs, 180도는 kMaxPulseUs에 매핑합니다.
constexpr int kMinPulseUs = 500;
constexpr int kMaxPulseUs = 2500;

int angle_to_pulse_us(int angle)
{
    // 서보에 비정상적인 각도 값이 들어가지 않도록 0~180도 범위로 제한합니다.
    // 이 방어 코드 덕분에 상위 로직에서 실수로 범위를 벗어난 값을 넘겨도
    // PWM 펄스가 과도하게 길거나 짧아지는 일을 막을 수 있습니다.
    if (angle < 0) {
        angle = 0;
    } else if (angle > 180) {
        angle = 180;
    }

    // 논리 각도가 증가할 때 실제 축이 시계방향으로 움직이도록 펄스 방향을 선택합니다.
    // MG90S 개체나 설치 방향 때문에 회전 방향이 반대라면 위 상수만 false로 바꿉니다.
    const int servo_angle = kClockwiseUsesIncreasingPulse ? angle : (180 - angle);

    // 각도를 선형 보간해서 펄스 폭으로 변환합니다.
    // 예를 들어 90도는 500us와 2500us의 중간인 약 1500us가 됩니다.
    return kMinPulseUs + ((kMaxPulseUs - kMinPulseUs) * servo_angle) / 180;
}
} // namespace

esp_err_t ServoLock::init()
{
    // LEDC 타이머는 PWM의 주파수와 duty 해상도를 결정합니다.
    // 이 프로젝트에서는 서보 표준에 맞춰 50 Hz를 사용합니다.
    ledc_timer_config_t timer_config = {
        .speed_mode = kSpeedMode,
        .duty_resolution = kDutyResolution,
        .timer_num = kTimer,
        .freq_hz = kPwmFrequencyHz,
        // CPU/APB 주파수 변경과 Light-sleep 중에도 사용할 수 있는 클럭입니다.
        .clk_cfg = LEDC_USE_RC_FAST_CLK,
        .deconfigure = false,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer_config), TAG, "Failed to configure LEDC timer");

    // LEDC 채널은 실제 GPIO 출력 핀과 타이머를 연결합니다.
    // duty는 아래 write_pulse_us()에서 매번 갱신하므로 초기값은 0으로 둡니다.
    ledc_channel_config_t channel_config = {
        .gpio_num = kServoGpio,
        .speed_mode = kSpeedMode,
        .channel = kChannel,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = kTimer,
        .duty = 0,
        .hpoint = 0,
        // 잠김 위치 유지용 PWM은 CPU가 쉬는 동안에도 출력합니다.
        // 서보 전원/PWM을 끊지 않으므로 서보의 유지 전류는 계속 소모됩니다.
        .sleep_mode = LEDC_SLEEP_MODE_KEEP_ALIVE,
        .flags = {
            .output_invert = 0,
        },
    };
    ESP_RETURN_ON_ERROR(ledc_channel_config(&channel_config), TAG, "Failed to configure LEDC channel");
    ESP_LOGI(TAG, "Servo PWM configured: 50 Hz RC_FAST, output maintained during Light-sleep");

    // 부팅 직후 서보가 정상적으로 연결되어 있는지 눈으로 확인하기 위한 진단 동작입니다.
    // 최종 상태는 항상 잠김 위치로 되돌려 둡니다.
    //
    // 순서:
    // 1. 잠김 위치로 이동
    // 2. 600 ms 대기
    // 3. 열림 위치로 이동
    // 4. 600 ms 대기
    // 5. 다시 잠김 위치로 이동
    ESP_RETURN_ON_ERROR(lock(), TAG, "Failed to move servo to initial locked angle");
    vTaskDelay(pdMS_TO_TICKS(600));
    ESP_RETURN_ON_ERROR(unlock(), TAG, "Failed to move servo during startup test");
    vTaskDelay(pdMS_TO_TICKS(600));
    return lock();
}

esp_err_t ServoLock::lock()
{
    ESP_LOGI(TAG, "Moving to home position (0 degrees / physical 2 o'clock)");
    // 실제 PWM 출력이 성공한 뒤에만 locked_를 true로 갱신합니다.
    ESP_RETURN_ON_ERROR(write_angle(kLockedAngle), TAG, "Failed to move servo to locked angle");
    locked_ = true;
    return ESP_OK;
}

esp_err_t ServoLock::unlock()
{
    ESP_LOGI(TAG, "Moving to unlocked position (%d degrees)", kUnlockedAngle);
    // 실제 PWM 출력이 성공한 뒤에만 locked_를 false로 갱신합니다.
    ESP_RETURN_ON_ERROR(write_angle(kUnlockedAngle), TAG, "Failed to move servo to unlocked angle");
    locked_ = false;
    return ESP_OK;
}

bool ServoLock::is_locked() const
{
    return locked_;
}

esp_err_t ServoLock::write_angle(int angle)
{
    // 상위 로직은 "각도"만 알면 되고, 실제 서보 펄스 폭 계산은 여기서 숨깁니다.
    return write_pulse_us(angle_to_pulse_us(angle));
}

esp_err_t ServoLock::write_pulse_us(int pulse_us)
{
    // LEDC duty는 "한 PWM 주기 중 HIGH로 유지되는 비율"입니다.
    //
    // 예:
    // - 50 Hz의 주기는 20,000 us
    // - 666 us 펄스는 666 / 20000 만큼 HIGH
    // - 14-bit duty 최대값 16383에 그 비율을 곱하면 LEDC duty 값이 됩니다.
    const uint32_t duty = (static_cast<uint32_t>(pulse_us) * kDutyMax) / kPwmPeriodUs;
    ESP_LOGI(TAG, "Writing PWM: gpio=%d pulse=%dus duty=%lu/%d",
             static_cast<int>(kServoGpio), pulse_us, static_cast<unsigned long>(duty), kDutyMax);

    // ledc_set_duty()는 내부 duty 레지스터 값을 설정하고,
    // ledc_update_duty()는 그 값을 실제 출력에 반영합니다.
    // 두 호출이 모두 성공해야 서보 신호가 바뀝니다.
    ESP_RETURN_ON_ERROR(ledc_set_duty(kSpeedMode, kChannel, duty), TAG, "Failed to set duty");
    ESP_RETURN_ON_ERROR(ledc_update_duty(kSpeedMode, kChannel), TAG, "Failed to update duty");
    return ESP_OK;
}
