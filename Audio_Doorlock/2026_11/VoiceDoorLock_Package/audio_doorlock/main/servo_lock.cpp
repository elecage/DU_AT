#include "servo_lock.h"

#include "driver/ledc.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_pm.h"
#include "esp_timer.h"
#include "sdkconfig.h"

namespace {
constexpr char TAG[] = "servo_lock";

// ESP32-C3 Super Mini에서 MG90S Signal 선을 연결한 GPIO입니다.
// 현재 하드웨어 배선은 그대로 GPIO4를 사용합니다.
constexpr gpio_num_t kServoGpio = GPIO_NUM_4;

// ESP32-C3의 LEDC는 Low Speed mode만 사용합니다.
constexpr ledc_timer_t kTimer = LEDC_TIMER_0;
constexpr ledc_channel_t kChannel = LEDC_CHANNEL_0;
constexpr ledc_mode_t kSpeedMode = LEDC_LOW_SPEED_MODE;

// 50 Hz RC servo PWM.
constexpr ledc_timer_bit_t kDutyResolution = LEDC_TIMER_14_BIT;
constexpr int kPwmFrequencyHz = 50;
constexpr int kPwmPeriodUs = 1000000 / kPwmFrequencyHz;
constexpr int kDutyMax = (1 << 14) - 1;

// 현재 프로젝트의 기존 기계 동작은 변경하지 않습니다.
// 잠김 0도 / 열림 40도로 사용합니다.
constexpr int kLockedAngle = 0;
constexpr int kUnlockedAngle = 25;

// 설치 방향이 반대인 경우에만 false로 변경합니다.
constexpr bool kClockwiseUsesIncreasingPulse = true;

// 현재 프로젝트에서 사용하던 MG90S pulse range를 그대로 유지합니다.
constexpr int kMinPulseUs = 500;
constexpr int kMaxPulseUs = 2500;

// -----------------------------------------------------------------------------
// Servo low-power strategy
// -----------------------------------------------------------------------------
// 별도의 MOSFET/전자식 스위치 없이 서보 VCC는 계속 5 V에 연결되어 있습니다.
// 따라서 "완전 전원 차단"은 하지 않습니다.
//
// 명령을 받은 동안만 유효한 50 Hz PWM을 출력하고, 움직일 시간을 충분히 준 뒤
// PWM duty를 0으로 만들어 Signal을 LOW 상태로 둡니다.
// 이렇게 하면 서보에 계속 위치 유지 명령을 보내는 기존 KEEP_ALIVE 구조를 없애고,
// 대기 중에는 PWM holding을 하지 않습니다.
//
// 주의:
//  - 서보 내부 전자회로는 계속 전원을 받으므로 소비전류가 0 mA가 되지는 않습니다.
//  - PWM을 끄면 holding torque가 사라질 수 있으므로 문 기구가 외력으로 되밀리는지
//    실제 하드웨어에서 반드시 확인해야 합니다.
//
// TowerPro MG90S 공식 사양은 4.8 V에서 약 0.10 s / 60° 속도를 제시하지만,
// 도어락 기구 부하, 전원 강하, 개체차를 고려해 500 ms 동안 PWM을 유지합니다.
constexpr uint64_t kServoDriveWindowUs = 500ULL * 1000ULL;

int angle_to_pulse_us(int angle)
{
    if (angle < 0) {
        angle = 0;
    } else if (angle > 180) {
        angle = 180;
    }

    const int servo_angle =
        kClockwiseUsesIncreasingPulse ? angle : (180 - angle);

    return kMinPulseUs +
           ((kMaxPulseUs - kMinPulseUs) * servo_angle) / 180;
}

} // namespace

esp_err_t ServoLock::init()
{
    if (initialized_) {
        return ESP_OK;
    }

    // LEDC timer configuration.
    ledc_timer_config_t timer_config = {
        .speed_mode = kSpeedMode,
        .duty_resolution = kDutyResolution,
        .timer_num = kTimer,
        .freq_hz = kPwmFrequencyHz,
        .clk_cfg = LEDC_USE_RC_FAST_CLK,
        .deconfigure = false,
    };
    ESP_RETURN_ON_ERROR(
        ledc_timer_config(&timer_config),
        TAG,
        "Failed to configure LEDC timer");

    ledc_channel_config_t channel_config = {
        .gpio_num = kServoGpio,
        .speed_mode = kSpeedMode,
        .channel = kChannel,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = kTimer,
        .duty = 0,
        .hpoint = 0,

        // 기존 KEEP_ALIVE는 Light-sleep 중에도 PWM/LEDC clock을 유지하므로
        // 이번 절전 목표와 반대입니다.
        // 움직일 때는 아래 ESP_PM_NO_LIGHT_SLEEP lock으로 잠깐 수면을 막고,
        // 대기 때는 PWM을 0으로 만든 뒤 Light-sleep 진입을 허용합니다.
        .sleep_mode = LEDC_SLEEP_MODE_NO_ALIVE_NO_PD,

        .flags = {
            .output_invert = 0,
        },
    };
    ESP_RETURN_ON_ERROR(
        ledc_channel_config(&channel_config),
        TAG,
        "Failed to configure LEDC channel");

    // ESP-IDF 5.4의 thread-safe LEDC API인 ledc_set_duty_and_update()는
    // 내부적으로 LEDC fade service를 사용하므로, 공식 API 요구사항에 따라
    // 한 번 설치해야 합니다. Fade 애니메이션을 쓰기 위한 것이 아니라,
    // Matter task와 esp_timer task 양쪽에서 같은 LEDC 채널을 안전하게
    // 갱신하기 위해 thread-safe API를 사용하기 위한 초기화입니다.
    //
    // 이미 다른 코드가 fade service를 설치한 경우 ESP_ERR_INVALID_STATE가
    // 반환될 수 있으므로, 그 경우는 정상 상태로 취급합니다.
    const esp_err_t fade_err = ledc_fade_func_install(0);
    if (fade_err != ESP_OK && fade_err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG,
                 "Failed to install LEDC fade service required by ledc_set_duty_and_update(): %s",
                 esp_err_to_name(fade_err));
        return fade_err;
    }

    ESP_LOGI(TAG,
             "LEDC thread-safe duty update service ready%s",
             fade_err == ESP_ERR_INVALID_STATE ? " (already installed)" : "");

    const esp_timer_create_args_t release_timer_args = {
        .callback = &ServoLock::release_timer_cb,
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "servo_pwm_idle",

        // 이 one-shot은 서보 동작 시작 후 500 ms 시점에 반드시 실행되어
        // PWM을 끄고 PM lock을 해제해야 하므로 skip하지 않습니다.
        .skip_unhandled_events = false,
    };
    ESP_RETURN_ON_ERROR(
        esp_timer_create(&release_timer_args, &release_timer_),
        TAG,
        "Failed to create servo PWM release timer");

#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
    // 서보가 움직이는 500 ms 동안 Auto Light-sleep에 들어가 PWM이 중간에
    // 끊기는 일을 방지합니다. PWM을 끈 직후 release_pwm()에서 해제됩니다.
    ESP_RETURN_ON_ERROR(
        esp_pm_lock_create(
            ESP_PM_NO_LIGHT_SLEEP,
            0,
            "servo_move",
            &no_light_sleep_lock_),
        TAG,
        "Failed to create servo no-light-sleep PM lock");
#endif

    initialized_ = true;

    ESP_LOGI(
        TAG,
        "Servo configured: GPIO%d 50Hz, PWM only during movement, idle signal=LOW",
        static_cast<int>(kServoGpio));

#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
    ESP_LOGI(
        TAG,
        "Servo PM integration: NO_LIGHT_SLEEP lock is used only during the %llums movement window",
        static_cast<unsigned long long>(kServoDriveWindowUs / 1000ULL));
#else
    ESP_LOGW(
        TAG,
        "CONFIG_PM_ENABLE is disabled: servo PWM idle still works, but ESP32 automatic Light-sleep is unavailable");
#endif

    // 기존의 잠김 -> 열림 -> 잠김 부팅 진단은 불필요한 모터 동작/전력 소모를
    // 발생시키므로 제거했습니다. 부팅 시 안전한 잠김 위치만 한 번 지시하고,
    // 500 ms 뒤 PWM을 자동으로 끕니다.
    return lock();
}

esp_err_t ServoLock::lock()
{
    ESP_LOGI(
        TAG,
        "Servo command: LOCK -> %d degrees; PWM will idle after %llums",
        kLockedAngle,
        static_cast<unsigned long long>(kServoDriveWindowUs / 1000ULL));

    ESP_RETURN_ON_ERROR(
        command_angle(kLockedAngle),
        TAG,
        "Failed to move servo to locked angle");

    locked_ = true;
    return ESP_OK;
}

esp_err_t ServoLock::unlock()
{
    ESP_LOGI(
        TAG,
        "Servo command: UNLOCK -> %d degrees; PWM will idle after %llums",
        kUnlockedAngle,
        static_cast<unsigned long long>(kServoDriveWindowUs / 1000ULL));

    ESP_RETURN_ON_ERROR(
        command_angle(kUnlockedAngle),
        TAG,
        "Failed to move servo to unlocked angle");

    locked_ = false;
    return ESP_OK;
}

bool ServoLock::is_locked() const
{
    return locked_;
}

esp_err_t ServoLock::command_angle(int angle)
{
    if (!initialized_) {
        return ESP_ERR_INVALID_STATE;
    }

    // 이전 명령의 PWM-off timer가 아직 대기 중이면 취소하고
    // 이번 동작 기준으로 500 ms window를 다시 시작합니다.
    if (release_timer_ != nullptr) {
        const esp_err_t stop_err = esp_timer_stop(release_timer_);
        if (stop_err != ESP_OK && stop_err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(
                TAG,
                "Failed to stop previous servo release timer: %s",
                esp_err_to_name(stop_err));
        }
    }

#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
    if (no_light_sleep_lock_ != nullptr && !no_light_sleep_lock_held_) {
        const esp_err_t lock_err =
            esp_pm_lock_acquire(no_light_sleep_lock_);
        if (lock_err != ESP_OK) {
            ESP_LOGE(
                TAG,
                "Failed to acquire servo PM lock: %s",
                esp_err_to_name(lock_err));
            return lock_err;
        }
        no_light_sleep_lock_held_ = true;
    }
#endif

    // Timer callback과 새 명령이 거의 동시에 겹쳐도 이전 callback이 새 PWM을
    // 바로 꺼 버리지 않도록 최신 명령 시각을 기록합니다.
    last_command_started_us_ = esp_timer_get_time();

    const esp_err_t write_err = write_angle(angle);
    if (write_err != ESP_OK) {
        release_pwm();
        return write_err;
    }

    const esp_err_t timer_err =
        esp_timer_start_once(release_timer_, kServoDriveWindowUs);
    if (timer_err != ESP_OK) {
        ESP_LOGE(
            TAG,
            "Failed to start servo release timer: %s",
            esp_err_to_name(timer_err));

        // 타이머 시작 실패 시 PWM을 계속 유지한 채 방치하지 않습니다.
        release_pwm();
        return timer_err;
    }

    return ESP_OK;
}

esp_err_t ServoLock::write_angle(int angle)
{
    return write_pulse_us(angle_to_pulse_us(angle));
}

esp_err_t ServoLock::write_pulse_us(int pulse_us)
{
    const uint32_t duty =
        (static_cast<uint32_t>(pulse_us) * kDutyMax) /
        kPwmPeriodUs;

    ESP_LOGI(
        TAG,
        "Servo PWM active: gpio=%d pulse=%dus duty=%lu/%d",
        static_cast<int>(kServoGpio),
        pulse_us,
        static_cast<unsigned long>(duty),
        kDutyMax);

    // thread-safe API 사용. Matter task와 ESP timer task에서 같은 LEDC channel을
    // 건드릴 가능성을 줄이기 위해 set_duty()+update_duty() 쌍 대신 사용합니다.
    return ledc_set_duty_and_update(
        kSpeedMode,
        kChannel,
        duty,
        0);
}

esp_err_t ServoLock::release_pwm()
{
    if (!initialized_) {
        return ESP_ERR_INVALID_STATE;
    }

    // 0% duty => Signal line LOW.
    // 서보 전원 VCC는 계속 살아 있지만 유효한 50 Hz position pulse는 더 이상
    // 전달하지 않습니다. 다음 lock()/unlock()에서 duty를 다시 설정하면 즉시 재개됩니다.
    const esp_err_t pwm_err =
        ledc_set_duty_and_update(
            kSpeedMode,
            kChannel,
            0,
            0);

    if (pwm_err != ESP_OK) {
        ESP_LOGE(
            TAG,
            "Failed to put servo PWM into idle: %s",
            esp_err_to_name(pwm_err));
    } else {
        ESP_LOGI(
            TAG,
            "Servo idle: PWM duty=0, signal LOW, VCC remains connected");
    }

#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
    if (no_light_sleep_lock_ != nullptr && no_light_sleep_lock_held_) {
        const esp_err_t unlock_err =
            esp_pm_lock_release(no_light_sleep_lock_);

        if (unlock_err != ESP_OK) {
            ESP_LOGE(
                TAG,
                "Failed to release servo PM lock: %s",
                esp_err_to_name(unlock_err));

            if (pwm_err == ESP_OK) {
                return unlock_err;
            }
        } else {
            no_light_sleep_lock_held_ = false;
            ESP_LOGI(
                TAG,
                "Servo movement complete: ESP32 Auto Light-sleep allowed again");
        }
    }
#endif

    return pwm_err;
}

void ServoLock::release_timer_cb(void *arg)
{
    auto *self = static_cast<ServoLock *>(arg);
    if (self == nullptr) {
        return;
    }

    // 새 servo 명령이 이 callback 직전에 들어왔다면 이전 timer callback이
    // 새 동작을 끊으면 안 됩니다. 최신 명령으로부터 500 ms가 아직 지나지
    // 않았다면 남은 시간만큼 one-shot을 다시 예약합니다.
    const int64_t now_us = esp_timer_get_time();
    const int64_t elapsed_us =
        now_us - self->last_command_started_us_;

    if (self->last_command_started_us_ > 0 &&
        elapsed_us >= 0 &&
        elapsed_us < static_cast<int64_t>(kServoDriveWindowUs)) {
        const uint64_t remaining_us =
            kServoDriveWindowUs - static_cast<uint64_t>(elapsed_us);

        const esp_err_t restart_err =
            esp_timer_start_once(self->release_timer_, remaining_us);

        if (restart_err != ESP_OK) {
            ESP_LOGE(
                TAG,
                "Failed to reschedule servo PWM release: %s",
                esp_err_to_name(restart_err));
            self->release_pwm();
        }
        return;
    }

    const esp_err_t err = self->release_pwm();
    if (err != ESP_OK) {
        ESP_LOGE(
            TAG,
            "Servo PWM release callback failed: %s",
            esp_err_to_name(err));
    }
}
