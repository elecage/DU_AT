//풀업470K옴+Drift test 최적화
//기존 캘리 enter = toucgAvg + span*0.55, exit = touchAvg + span*0.70, 웨이크업채널 GPIO1  (13)-->같은 숫자끼리 호환
#include <Arduino.h>
#define private public                          // BleCombo.h를 읽는 동안 private과 protected를 public으로 취급하게 만든다 (접근가능) 
#define protected public
#include <BleCombo.h>                              
#undef private                                  // 통신코드 이후 포함되는 다른 라이브러리나 코드까지 영향을 받지 않게 하기위함 (접근불가능) 원래 되돌리는
#undef protected

#include <BLEDevice.h>                                     //BLE장치, 광고, 서버, 데이터 특성 등을 다룰 수 있도록 관련 선언
#include <BLEAdvertising.h>
#include <BLEUUID.h>
#include <BLEServer.h>
#include <BLECharacteristic.h>

#include "esp_bt.h"                                        //ESP32의 블루투스, 절전, GPIO 기능을 사용하기 위한 헤더
#include "esp_gap_ble_api.h"
#include "esp_sleep.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"

// ===================== 핀 맵 =====================
static const int CH_COUNT = 6;                                      //채널 6개        
static const int DISABLED_PIN = -1;
// ch0/ch1은 GUI 패킷 호환을 위해 자리만 남긴 비활성 더미 채널이다.                 // GUI의 형식을 유지해줘야함 (6개채널 형식)
// 실제 ADC 센싱 채널은 ch2~ch5 = GPIO3~GPIO6만 사용한다.                        //   채널은 ch0~6까지 있는데 ch 0,1은 안씀 / ch2~6까지 사용 
// GPIO1은 deep-sleep /wake 전용이고, GPIO2는 시스템에서 완전히 미사용이다.         // GPIO는 01~06까지 있는데 01은 딥슬립에서 -> 깨울때 02는 없음 03~06 방향 (위, 아래, 왼쪽 , 오른쪽 )    
static const int SENSE_PINS[CH_COUNT] = {DISABLED_PIN, DISABLED_PIN, 3, 4, 5, 6};   // [OFF,OFF,Up,Down,Left,Right]
static const gpio_num_t WAKE_PIN = GPIO_NUM_1;                                      // XIAO ESP32-S3 D0/A0 = GPIO1, wake-only
static const gpio_num_t UNUSED_PIN_GPIO2 = GPIO_NUM_2;                              // GPIO2는 항상 내부 바이어스 OFF
static const int WAKE_CH = -1;                                                      // 센스 채널과 분리된 전용 wake 입력

// ===================== ADC 범위 =====================
static const int ADC_RAW_MAX_MV = 3300;                                      //원본 측정값을 기준으로 사용하는 최대 전압 3.3v     
static const int ADC_GUI_MAX_MV = 3000;                                      //GUI 화면에 표시할 때 사용하는 최대 전압 3.0V

// ===================== 클릭 채널 시스템 OFF =====================
// 하드웨어는 연결돼 있어도, 시스템에서는 좌/우 클릭 //채널(ch0/ch1)을 완전히 비활성화
static const bool ENABLE_CLICK_CHANNELS = false;    

// ===================== 캘리 후 하드 차단 =====================
// 캘리브레이션 이후 런타임에서,
// 각 채널의 실제 전압 읽기값이 이 값 이상이면 무조건 "무터치" 처리    // 터치를 인식하기 위한 전압의 상한선 설정 2.4V (터치 판정을 받을수 있는 범위안)
static const int TOUCH_FORCE_OFF_MV = 2400;

// ====== 응답성 ======  
static const uint32_t LOOP_DELAY_MS = 10;                  //loop가 한번 끝날때마다 기본적으로 10초를 기다린다는 뜻 

// 샘플링
static const int ADC_SAMPLES_MAIN      = 7;               // ADC 전압을 7번 읽었다는 소리 -> 평소 전압측정 
static const int ADC_SAMPLES_BASELINE  = 9;               //  9번 읽음 -> 기준 전압측정시 흔들림 감소 
static const int ADC_SAMPLES_FAST      = 3;               //  3번 읽음 -> 측정 시간을 줄여 빠르게 읽기    

// ===================== 로그와 마우스 =====================
static uint32_t PRINT_INTERVAL_MS = 500;                 // 시리얼 로그와 관련 출력 간격, 마지막 출력시각 , 출력여부
static uint32_t lastPrintMs = 0;
static bool     enablePrint = true;

// ===================== 이동량 =====================
static const int MOVE_STEP = 4;                         //한번에 4칸씩 이동한다 -> 상하좌우로
static const int DIAG_STEP = 3;                         // 대각선으로 3칸씩 이동 

// ===================== 상태 =====================      // 현재 상태를 저장하는 변수(값이 계속 바뀌는 변수)
static bool  is_touched[CH_COUNT];                      // 현재 터치상태 (true , false로 저장)
static uint8_t deb_enter[CH_COUNT];                     // 연속 터치 감지 횟수 
static uint8_t deb_exit [CH_COUNT];                     // 연속 터치 해제 횟수 
                                                                     
// 채널별 debounce       -> 디바운스 ? -> 터치가 진짜 발생했는지 여러번 확인하는 기능(터치 인정 기준) , 터치센서가 순간적으로 잘못 반응하는 것을 막기 위함(안전장치 같은 역할 )
static const uint8_t ENTER_DEB_SAMPLES_CLICK = 3;   // 30 ms
static const uint8_t EXIT_DEB_SAMPLES_CLICK  = 3;                 // 30ms, 20ms 가 지나야 1번 터치로 인정 
static const uint8_t ENTER_DEB_SAMPLES_MOVE  = 2;   // 20 ms      
static const uint8_t EXIT_DEB_SAMPLES_MOVE   = 2;

// 클릭 edge 처리
static bool prev_left_cmd  = false;                         // 이전에 눌려 있던 상태였는지 기억하는 변수 
static bool prev_right_cmd = false;

// ===================== 채널별 보정치 =====================    //보정장치 (Bais) 
static int16_t bias_mv[CH_COUNT];                            // 각 채널의 측정 전압에 더할 보정값               

// ===================== 채널별 터치 임계(캘리 결과, RAW 기준) ===================== -> (각 채널의 터치 진입 해제 임계 전압을 저장 )
static int16_t touch_enter_mv[CH_COUNT];                // 몇 mV 이상 또는 이하일때   // int16_t는 부호가 있는 16비트 정수형 
static int16_t touch_exit_mv [CH_COUNT];

// ===================== baseline tracking (드리프트 대응) ===================== //드리프트? -> 환경변화에 측정기준을 조금씩 벗어나는 현상 // 베이스라인?-> 센서기준값 
static int16_t baseline_mv[CH_COUNT];    // 센서의 기준값 저장
static uint32_t lastBaselineUpdateMs = 0;  // 마지막 기준값 업데이트 저장 
static const int BASELINE_IIR_SHIFT = 6;                 // 1/64  
static const uint32_t BASELINE_UPDATE_PERIOD_MS = 40;      // 40ms마다 기준값 업데이트

// baseline 업데이트 제한
static const int BASELINE_FREEZE_DELTA_MV = 60;         // 애매한 하강이면 baseline 동결
static const int BASELINE_REJECT_DROP_MV  = 180;        // 급격한 하강은 반영 금지
static const int BASELINE_REJECT_RISE_MV  = 180;        // 급격한 상승도 반영 금지

// ===================== 최소 델타 보강(런타임) =====================
// 이동 채널
static const int MIN_TOUCH_DELTA_MV_MOVE   = 100;    // 일반적인 터치 감지 기준설정 
static const int MIN_RELEASE_DELTA_MV_MOVE = 70;
static const int NOISE_MARGIN_MV_MOVE      = 35;

// 클릭 채널(더 보수적)
static const int MIN_TOUCH_DELTA_MV_CLICK   = 140;  ///더 엄격한 터치 감지 기준 설정
static const int MIN_RELEASE_DELTA_MV_CLICK = 85;
static const int NOISE_MARGIN_MV_CLICK      = 45;

// 클릭 채널 우세도                                   //터치채널 어느쪽이 더 강하게 나타나는지
static const int CLICK_DOMINANCE_MARGIN_MV = 60;
static const int CLICK_PAIR_MARGIN_MV      = 80;

// (런타임) 무터치 노이즈 p2p 추정              //손가락을 대지 않을때 발생하는 센서 잡음 측정
static const uint32_t NOISE_WIN_MS = 250;
static int16_t noise_p2p_mv[CH_COUNT];
static int16_t noise_min_mv[CH_COUNT];
static int16_t noise_max_mv[CH_COUNT];
static uint32_t noise_t0_ms[CH_COUNT];

// ===================== 유휴 감지 & 딥슬립 =====================
// 기본은 30초 유휴 후 deep-sleep. Drift test 중에는 GUI가 BLE 명령으로
// 일시적으로 60초로 바꾼다. ESP32-S3 deep-sleep은 RTC memory/peripheral만
// 유지되는 저전력 모드이므로, Drift 무터치 30초 기록 중에는 진입하지 않게
// 유휴 제한 시간을 넉넉하게 둔다.
static const uint32_t IDLE_SLEEP_DEFAULT_MS = 30000;  // 평상시 딥슬립으로 바꾸는 기준 30초
static const uint32_t IDLE_SLEEP_DRIFT_MS   = 60000;   // Drift test 중에는 60초가 기준
static uint32_t g_idleSleepMs = IDLE_SLEEP_DEFAULT_MS;
static uint32_t lastTouchMs = 0;   

// ===================== BLE 이름 =====================
static const char* BLE_DEV_NAME = "정진우";   // 왜 이름을 붙여주는건지 ??

// ===================== USER LED =====================
#ifndef LED_BUILTIN
#define LED_BUILTIN 21
#endif
static const int  USER_LED_PIN = LED_BUILTIN;
static const bool USER_LED_ACTIVE_LOW = true;
static inline void userLedWrite(bool on) {
  if (USER_LED_ACTIVE_LOW) digitalWrite(USER_LED_PIN, on ? LOW : HIGH);
  else                     digitalWrite(USER_LED_PIN, on ? HIGH : LOW);
}
static void blinkN(int n) {
  for (int i = 0; i < n; ++i) { userLedWrite(true); delay(70); userLedWrite(false); delay(70); }
}

static inline bool hidConnected() { return Keyboard.isConnected(); }
static inline bool isActiveSenseCh(int ch) { return (ch >= 2 && ch < CH_COUNT && SENSE_PINS[ch] >= 0); }
static inline bool isClickCh(int ch) { (void)ch; return false; }  // 좌/우 클릭 채널은 시스템 OFF
static inline bool forceNoTouchByVoltage(int mv) { return (mv >= TOUCH_FORCE_OFF_MV); }


// ===================== GPIO1 웨이크업 전극 전원 차단 =====================
// 목적:
//  - Deep-sleep 중에는 GPIO1만 LOW wake 입력으로 잠깐 사용한다.
//  - 깨어난 뒤/일반 동작 중에는 GPIO1 전극에 내부 pull-up/pull-down, ADC, RTC 입력 바이어스가 걸리지 않게 한다.
// 주의:
//  - GPIO1 전극에 외부 470 kΩ 풀업이 실제로 납땜되어 있으면, 소프트웨어로 그 외부 풀업 전류는 끊을 수 없다.
//  - 이 함수는 ESP32-S3 내부에서 걸 수 있는 모든 바이어스를 끄는 처리다.
static void disableRtcDigitalBias(gpio_num_t pin) {
  rtc_gpio_hold_dis(pin);
  gpio_hold_dis(pin);
  rtc_gpio_deinit(pin);
  gpio_reset_pin(pin);
  gpio_pullup_dis(pin);
  gpio_pulldown_dis(pin);
  gpio_set_direction(pin, GPIO_MODE_DISABLE);
}

static void wakeElectrodeRuntimeOff() {
  // GPIO1: wake 후 내부 pull-up/down, RTC 입력, GPIO 입력을 모두 끈다.
  disableRtcDigitalBias(WAKE_PIN);
  // GPIO2: 시스템에서 완전 미사용이므로 내부 바이어스도 항상 끈다.
  disableRtcDigitalBias(UNUSED_PIN_GPIO2);
}

static void wakeElectrodeEnableForSleep() {
  // sleep에 들어가기 직전에만 GPIO1을 EXT0 LOW wake 입력으로 살린다.
  // 중요한 수정:
  //  - 내부 gpio/rtc pull-up을 켜지 않는다.
  //  - 웨이크업 기준 전압은 PCB의 외부 470 kΩ 풀업만 사용한다.
  //  - 내부 pull-up을 추가하면 전류 경로가 강해져 전극맛이 커지고,
  //    혀/침 저항으로 LOW까지 충분히 떨어지지 않아 wake 실패가 날 수 있다.
  // GPIO2는 sleep 중에도 미사용 상태를 유지한다.
  disableRtcDigitalBias(UNUSED_PIN_GPIO2);

  rtc_gpio_hold_dis(WAKE_PIN);
  gpio_hold_dis(WAKE_PIN);

  // RTC wake 입력으로만 설정하고 내부 pull-up/pull-down은 모두 끈다.
  rtc_gpio_deinit(WAKE_PIN);
  gpio_reset_pin(WAKE_PIN);
  gpio_pullup_dis(WAKE_PIN);
  gpio_pulldown_dis(WAKE_PIN);
  gpio_set_direction(WAKE_PIN, GPIO_MODE_INPUT);

  rtc_gpio_init(WAKE_PIN);
  rtc_gpio_set_direction(WAKE_PIN, RTC_GPIO_MODE_INPUT_ONLY);
  rtc_gpio_pullup_dis(WAKE_PIN);
  rtc_gpio_pulldown_dis(WAKE_PIN);

  esp_sleep_enable_ext0_wakeup(WAKE_PIN, 0);  // GPIO1이 LOW가 되면 wake
}

// ===================== BLE Telemetry ===================== //데이터를 저전력 블루투스(BLE)를 통해 다른 기기로 전송하는 것
enum : uint8_t {
  CAL_STAGE_NONE     = 0,                                   //uint8_t ->비교적 작은 범위의 정수를 저장하는 자료형
  CAL_STAGE_BASELINE = 1,
  CAL_STAGE_WAIT     = 2,
  CAL_STAGE_CAPTURE  = 3,
  CAL_STAGE_DONE     = 4,
  CAL_STAGE_LOST     = 5,
  CAL_STAGE_WEAK     = 6,
  CAL_STAGE_FAIL     = 7,
};

enum : uint8_t {
  CMD_CODE_NONE       = 0,
  CMD_CODE_LCLICK     = 1,
  CMD_CODE_RCLICK     = 2,
  CMD_CODE_UP         = 3,
  CMD_CODE_DOWN       = 4,
  CMD_CODE_LEFT       = 5,
  CMD_CODE_RIGHT      = 6,
  CMD_CODE_UP_LEFT    = 7,
  CMD_CODE_UP_RIGHT   = 8,
  CMD_CODE_DOWN_LEFT  = 9,
  CMD_CODE_DOWN_RIGHT = 10,
};

static BLEAdvertising*       g_adv = nullptr;
static BLEAdvertisementData  g_advDataBase;
static BLEAdvertisementData  g_scanBase;

static const char* CTRL_SERVICE_UUID = "5f6d0001-7f4b-4b2a-8f6c-11d4a1b50001";
static const char* CTRL_CHAR_UUID    = "5f6d0002-7f4b-4b2a-8f6c-11d4a1b50002";
static const uint8_t CTRL_CMD_REINIT_RECAL       = 0xA1;
static const uint8_t CTRL_CMD_IDLE_SLEEP_60S   = 0xA2;  // GUI Drift 시작 시: 60초 유휴 후 sleep
static const uint8_t CTRL_CMD_IDLE_SLEEP_30S   = 0xA3;  // Drift 종료/다른 모드: 기본 30초 복귀

static BLEServer*         g_ctrlServer = nullptr;
static BLEService*        g_ctrlService = nullptr;
static BLECharacteristic* g_ctrlChar = nullptr;

static volatile bool    g_runtimeRecalReq = false;
static volatile uint8_t g_runtimeRecalCmd = 0;

static uint8_t  g_rawMask   = 0;
static uint8_t  g_cmdMask   = 0;
static bool     g_inCal     = false;
static uint8_t  g_calCh     = 255;
static uint8_t  g_calStage  = CAL_STAGE_NONE;
static uint8_t  g_cmdCode   = CMD_CODE_NONE;
static uint8_t  g_calQualityQ = 0;
static uint8_t  g_mvQ[CH_COUNT]    = {0};
static uint8_t  g_ratioQ[CH_COUNT] = {0};
static uint8_t  g_thrQ[CH_COUNT]   = {0};
static int16_t  g_deltaRawRt[CH_COUNT] = {0};
static int16_t  g_deltaEffRt[CH_COUNT] = {0};
static int16_t  g_commonModeDeltaRt = 0;
static bool     g_chValid[CH_COUNT] = {true, true, true, true, true, true};
static int16_t  g_calSpanMv[CH_COUNT] = {0};
static uint8_t  g_chQualityQ[CH_COUNT] = {0};
static uint16_t g_seq = 0;

static uint32_t g_lastAdvSendMs = 0;
static const uint32_t ADV_SEND_PERIOD_MS = 90;

// ===================== 부팅 안정화 =====================
static const uint32_t BOOT_IGNORE_MS = 600;
static uint32_t g_bootMs = 0;

static const int FLOOD_TOUCH_COUNT = 4;
static const int FLOOD_HARD_COUNT  = 5;
static const uint32_t FLOOD_ENTER_MS = 90;
static const uint32_t FLOOD_CLEAR_MS = 450;
static const uint32_t FLOOD_SOFT_CLEAR_MS = 180;
static const uint32_t FLOOD_RESCUE_AFTER_MS = 1400;
static const uint32_t FLOOD_FORCE_AFTER_MS = 2600;
static const uint32_t FLOOD_REBASE_MS = 220;
static const uint32_t POST_WET_IGNORE_MS = 250;

static bool     g_wetLock = false;
static uint32_t g_floodSinceMs = 0;
static uint32_t g_clearSinceMs = 0;
static uint32_t g_wetEnterMs = 0;
static uint32_t g_postWetIgnoreUntilMs = 0;

static const int COMMON_MODE_ENABLE_MIN_MV = 45;
static const int COMMON_MODE_MAX_SUB_MV    = 900;
static const int FLOOD_EFF_ACTIVE_MAX      = 2;
static const int FLOOD_EFF_PEAK_MAX_MV     = 85;

// ===================== 유틸 =====================
static inline void sortSmall(int *a, int n);
static inline uint8_t mvToQ_gui(int mv_raw) {
  int mv = mv_raw;
  if (mv < 0) mv = 0;
  if (mv > ADC_GUI_MAX_MV) mv = ADC_GUI_MAX_MV;
  int q = (mv + 5) / 11;
  if (q < 0) q = 0;
  if (q > 255) q = 255;
  return (uint8_t)q;
}
static inline int median6ish(const int *vals, int n) {
  if (n <= 0) return 0;
  int t[CH_COUNT];
  if (n > CH_COUNT) n = CH_COUNT;
  for (int i = 0; i < n; ++i) t[i] = vals[i];
  sortSmall(t, n);
  if (n & 1) return t[n / 2];
  return (t[n / 2 - 1] + t[n / 2]) / 2;
}

static inline int calcCommonModeDeltaRt() {
  int vals[CH_COUNT];
  int n = 0;
  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i) || !g_chValid[i]) continue;
    vals[n++] = g_deltaRawRt[i];
  }
  if (n < 3) return 0;
  int med = median6ish(vals, n);
  if (med < COMMON_MODE_ENABLE_MIN_MV) return 0;
  if (med > COMMON_MODE_MAX_SUB_MV) med = COMMON_MODE_MAX_SUB_MV;
  return med;
}


static inline uint8_t ratioToQ8(int ch, int deltaMv) {
  if (!isActiveSenseCh(ch) || !g_chValid[ch]) return 0;
  int span = g_calSpanMv[ch];
  if (span < 40) span = 40;
  int q = (deltaMv * 255) / span;
  if (q < 0) q = 0;
  if (q > 255) q = 255;
  return (uint8_t)q;
}

static inline int ratioTouchMinQ8(int ch) {
  return isClickCh(ch) ? 120 : 92;
}

static inline int ratioReleaseMaxQ8(int ch) {
  return isClickCh(ch) ? 72 : 58;
}

static bool allChannelsValid() {
  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) continue;
    if (!g_chValid[i]) return false;
  }
  return true;
}

static std::string buildThrPayloadV1() {
  std::string m;
  m.reserve(11);
  m.push_back((char)0xFE);
  m.push_back((char)0xFF);
  m.push_back('H');
  m.push_back('T');
  m.push_back((char)1);
  for (int i = 0; i < CH_COUNT; ++i) m.push_back((char)g_thrQ[i]);
  return m;
}

static std::string buildMfgPayloadV4() {
  std::string m;
  m.reserve(26);
  m.push_back((char)0xFF);
  m.push_back((char)0xFF);
  m.push_back('H');
  m.push_back('M');
  m.push_back((char)4);

  m.push_back((char)(g_seq & 0xFF));
  m.push_back((char)((g_seq >> 8) & 0xFF));

  m.push_back((char)g_rawMask);
  m.push_back((char)g_cmdMask);

  uint8_t flags = 0;
  if (g_inCal) flags |= 0x01;
  if (hidConnected()) flags |= 0x02;
  if (allChannelsValid()) flags |= 0x04;
  if (g_wetLock) flags |= 0x08;
  m.push_back((char)flags);

  m.push_back((char)g_calCh);
  m.push_back((char)g_calStage);
  m.push_back((char)g_cmdCode);
  m.push_back((char)g_calQualityQ);

  for (int i = 0; i < CH_COUNT; ++i) m.push_back((char)g_mvQ[i]);
  for (int i = 0; i < CH_COUNT; ++i) m.push_back((char)g_ratioQ[i]);
  return m;
}

static void advInit() {
  g_adv = BLEDevice::getAdvertising();
  if (!g_adv) return;

  g_adv->setScanResponse(true);

  g_advDataBase = BLEAdvertisementData();
  g_advDataBase.setFlags(0x06);
  // NOTE: HM v4 manufacturer payload is larger than v3.
  // Keep advertisement payload <= 31 bytes, otherwise many scanners will not
  // receive manufacturer_data reliably and GUI highlight will stay stale.
  // HID service UUID advertisement is omitted here to make room for telemetry.

  g_scanBase = BLEAdvertisementData();
  g_scanBase.setName(BLE_DEV_NAME);
  g_scanBase.setManufacturerData(buildThrPayloadV1());

  g_adv->setAdvertisementData(g_advDataBase);
  g_adv->setScanResponseData(g_scanBase);

  g_adv->setMinInterval(0x40);
  g_adv->setMaxInterval(0x80);

  g_adv->start();
}

static void advSendTick(bool force=false) {
  if (!g_adv) return;
  uint32_t now = millis();
  if (!force && (now - g_lastAdvSendMs) < ADV_SEND_PERIOD_MS) return;

  g_seq++;
  std::string mfg = buildMfgPayloadV4();

  g_adv->stop();
  BLEAdvertisementData advData = g_advDataBase;
  advData.setManufacturerData(mfg);

  BLEAdvertisementData scanData = BLEAdvertisementData();
  scanData.setName(BLE_DEV_NAME);
  scanData.setManufacturerData(buildThrPayloadV1());

  g_adv->setAdvertisementData(advData);
  g_adv->setScanResponseData(scanData);
  g_adv->start();

  g_lastAdvSendMs = now;
}

// ===================== LED 패턴 =====================
static inline void calLedPatternTick(int ch) {
  const uint32_t PERIOD_MS = 2000;
  const uint32_t SLOT_MS   = 240;
  const uint32_t ON_MS     = 120;
  int n = ch + 1;
  uint32_t t = millis() % PERIOD_MS;
  uint32_t slot = t / SLOT_MS;

  bool on = false;
  if ((int)slot < n) {
    uint32_t inSlot = t % SLOT_MS;
    on = (inSlot < ON_MS);
  }
  userLedWrite(on);
}

// BLE TX 전력 최대
static void setBleTxPowerModerate() {
  const esp_power_level_t LV = ESP_PWR_LVL_P6;
  esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_DEFAULT, LV);
  esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV,     LV);
  esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_SCAN,    LV);
  for (int i = 0; i <= 8; ++i) {
    esp_ble_power_type_t t = (esp_ble_power_type_t)(ESP_BLE_PWR_TYPE_CONN_HDL0 + i);
    esp_ble_tx_power_set(t, LV);
  }
}

class GuiControlCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *pCharacteristic) override {
    std::string v = pCharacteristic->getValue();
    if (v.empty()) return;

    const uint8_t cmd = (uint8_t)v[0];
    if (cmd == CTRL_CMD_REINIT_RECAL) {
      g_runtimeRecalCmd = cmd;
      g_runtimeRecalReq = true;
      return;
    }

    if (cmd == CTRL_CMD_IDLE_SLEEP_60S) {
      g_idleSleepMs = IDLE_SLEEP_DRIFT_MS;
      lastTouchMs = millis();  // 명령 수신 직후 바로 sleep 조건이 성립하지 않게 갱신
      if (enablePrint) Serial.println("[GUI] Idle sleep timeout -> 60s for Drift test");
      return;
    }

    if (cmd == CTRL_CMD_IDLE_SLEEP_30S) {
      g_idleSleepMs = IDLE_SLEEP_DEFAULT_MS;
      lastTouchMs = millis();
      if (enablePrint) Serial.println("[GUI] Idle sleep timeout -> 30s default");
      return;
    }
  }
};

static bool waitForBleComboReady(uint32_t timeoutMs) {
  uint32_t t0 = millis();
  while ((millis() - t0) < timeoutMs) {
    if (Keyboard.hid != nullptr) {
      BLEService* hidSvc = Keyboard.hid->hidService();
      if (hidSvc != nullptr && hidSvc->getServer() != nullptr) {
        BLEAdvertising* adv = BLEDevice::getAdvertising();
        if (adv != nullptr) return true;
      }
    }
    delay(20);
  }
  return false;
}

static bool ctrlInit() {
  if (g_ctrlChar != nullptr) return true;
  if (!waitForBleComboReady(6000)) return false;

  BLEService* hidSvc = Keyboard.hid->hidService();
  if (!hidSvc) return false;

  g_ctrlServer = hidSvc->getServer();
  if (!g_ctrlServer) return false;

  g_ctrlService = g_ctrlServer->createService(CTRL_SERVICE_UUID);
  if (!g_ctrlService) return false;

  g_ctrlChar = g_ctrlService->createCharacteristic(
    CTRL_CHAR_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_READ
  );
  if (!g_ctrlChar) return false;

  g_ctrlChar->setCallbacks(new GuiControlCallbacks());
  g_ctrlChar->setValue("READY");
  g_ctrlService->start();
  return true;
}

// ===================== ADC 읽기 (RAW) =====================
static inline void sortSmall(int *a, int n) {
  for (int i = 0; i < n - 1; ++i) {
    for (int j = i + 1; j < n; ++j) {
      if (a[j] < a[i]) { int t = a[i]; a[i] = a[j]; a[j] = t; }
    }
  }
}

static inline int readMvTrimmedMeanRaw(int pin, int samples) {
  if (pin < 0) return 0;
  pinMode(pin, INPUT);
  (void)analogRead(pin);

  const int MAXN = 16;
  if (samples > MAXN) samples = MAXN;
  if (samples < 3) samples = 3;

  int v[MAXN];
  for (int i = 0; i < samples; ++i) {
    int mv = (int)analogReadMilliVolts(pin);
    if (mv > ADC_RAW_MAX_MV) mv = ADC_RAW_MAX_MV;
    if (mv < 0) mv = 0;
    v[i] = mv;
  }

  if (samples == 3) {
    sortSmall(v, 3);
    return v[1];
  }

  sortSmall(v, samples);

  int64_t sum = 0;
  int cnt = 0;
  for (int i = 1; i < samples - 1; ++i) { sum += v[i]; cnt++; }
  int mv = (cnt > 0) ? (int)((sum + cnt / 2) / cnt) : v[samples / 2];

  if (mv > ADC_RAW_MAX_MV) mv = ADC_RAW_MAX_MV;
  if (mv < 0) mv = 0;
  return mv;
}

static inline int readSenseWithBiasRaw(int idx, int samples) {
  if (!isActiveSenseCh(idx)) return 0;
  int mv = readMvTrimmedMeanRaw(SENSE_PINS[idx], samples);
  int adj = mv + bias_mv[idx];
  if (adj > ADC_RAW_MAX_MV) adj = ADC_RAW_MAX_MV;
  if (adj < 0)              adj = 0;
  return adj;
}

// ===================== 채널별 보조 함수 =====================
static inline uint8_t enterDebNeeded(int ch) {
  return isClickCh(ch) ? ENTER_DEB_SAMPLES_CLICK : ENTER_DEB_SAMPLES_MOVE;
}
static inline uint8_t exitDebNeeded(int ch) {
  return isClickCh(ch) ? EXIT_DEB_SAMPLES_CLICK : EXIT_DEB_SAMPLES_MOVE;
}

static inline int scoreFromBaseline(int idx, int mv_now) {
  (void)mv_now;
  int s = (int)g_deltaEffRt[idx];
  return (s > 0) ? s : 0;
}

static inline int dynMinTouchDelta(int ch) {
  int dyn = (int)noise_p2p_mv[ch] + (isClickCh(ch) ? NOISE_MARGIN_MV_CLICK : NOISE_MARGIN_MV_MOVE);
  int minv = isClickCh(ch) ? MIN_TOUCH_DELTA_MV_CLICK : MIN_TOUCH_DELTA_MV_MOVE;
  if (dyn < minv) dyn = minv;
  return dyn;
}

static inline int dynMinReleaseDelta(int ch) {
  int dynEnter = dynMinTouchDelta(ch);
  int dyn = (int)noise_p2p_mv[ch] + (isClickCh(ch) ? 25 : 20);
  int minv = isClickCh(ch) ? MIN_RELEASE_DELTA_MV_CLICK : MIN_RELEASE_DELTA_MV_MOVE;
  if (dyn < minv) dyn = minv;

  int maxAllowed = dynEnter - 25;
  if (maxAllowed < minv) maxAllowed = minv;
  if (dyn > maxAllowed) dyn = maxAllowed;

  return dyn;
}

static inline bool touchEnterCh(int ch, int mv) {
  if (!isActiveSenseCh(ch) || !g_chValid[ch]) return false;
  if (forceNoTouchByVoltage(mv)) return false;

  int deltaEff = (int)g_deltaEffRt[ch];
  bool cal = (mv <= touch_enter_mv[ch]);
  bool deltaOk = (deltaEff >= dynMinTouchDelta(ch));
  bool ratioOk = ((int)ratioToQ8(ch, deltaEff) >= ratioTouchMinQ8(ch));

  if (isClickCh(ch)) return (cal && deltaOk && ratioOk);
  return ((cal || deltaOk) && ratioOk);
}

static inline bool touchExitCh(int ch, int mv) {
  if (!isActiveSenseCh(ch) || !g_chValid[ch]) return true;
  if (forceNoTouchByVoltage(mv)) return true;

  bool cal = (mv >= touch_exit_mv[ch]);
  int deltaEff = (int)g_deltaEffRt[ch];
  bool deltaRelax = (deltaEff <= dynMinReleaseDelta(ch));
  bool ratioRelax = ((int)ratioToQ8(ch, deltaEff) <= ratioReleaseMaxQ8(ch));

  return (cal || deltaRelax || ratioRelax);
}

static bool clickDominantEnough(int ch, const int mv_now[CH_COUNT], const bool touch_raw[CH_COUNT]) {
  int my = scoreFromBaseline(ch, mv_now[ch]);
  int maxOther = 0;

  for (int j = 0; j < CH_COUNT; ++j) {
    if (j == ch) continue;
    if (!touch_raw[j]) continue;
    int s = scoreFromBaseline(j, mv_now[j]);
    if (s > maxOther) maxOther = s;
  }
  return (my >= (maxOther + CLICK_DOMINANCE_MARGIN_MV));
}

static void clearTouchStates() {
  for (int i = 0; i < CH_COUNT; ++i) {
    is_touched[i] = false;
    deb_enter[i] = 0;
    deb_exit[i] = 0;
  }
}

static void releaseAllHidOutputs() {
  Mouse.release(MOUSE_LEFT);
  Mouse.release(MOUSE_RIGHT);
  if (hidConnected()) Keyboard.releaseAll();
  prev_left_cmd = false;
  prev_right_cmd = false;
}

static void enterWetLock() {
  g_wetLock = true;
  g_wetEnterMs = millis();
  g_clearSinceMs = 0;
  clearTouchStates();
  releaseAllHidOutputs();
  g_cmdMask = 0;
  g_cmdCode = CMD_CODE_NONE;
}

static void quickRebaselineAll() {
  int64_t acc[CH_COUNT] = {0};
  uint32_t cnt[CH_COUNT] = {0};
  uint32_t t0 = millis();
  while (millis() - t0 < FLOOD_REBASE_MS) {
    for (int i = 0; i < CH_COUNT; ++i) {
      if (!isActiveSenseCh(i)) {
        g_mvQ[i] = 0;
        g_ratioQ[i] = 0;
        continue;
      }
      int mv = readSenseWithBiasRaw(i, ADC_SAMPLES_FAST);
      acc[i] += mv;
      cnt[i] += 1;
      g_mvQ[i] = mvToQ_gui(mv);
    }
    advSendTick(false);
    delay(5);
  }

  uint32_t now = millis();
  for (int i = 0; i < CH_COUNT; ++i) {
    if (cnt[i] > 0) {
      int mv = (int)(acc[i] / (int32_t)cnt[i]);
      baseline_mv[i] = (int16_t)constrain(mv, 0, ADC_RAW_MAX_MV);
      noise_p2p_mv[i] = 0;
      noise_min_mv[i] = mv;
      noise_max_mv[i] = mv;
      noise_t0_ms[i] = now;
      g_ratioQ[i] = 0;
    }
  }
  clearTouchStates();
  g_postWetIgnoreUntilMs = millis() + POST_WET_IGNORE_MS;
}


// ===================== 캘리 파라미터 =====================
static const uint32_t CAL_TOUCH_MS      = 5000;
static const uint32_t CAL_IDLE_MS_BOOT  = 350;
static const int      CAL_FS_HZ         = 1000;
static const int      CAL_WIN_SAMPLES   = 1000;
static const int      CAL_HOP_SAMPLES   = 500;
static const int      CAL_IDLE_FS_HZ    = 200;

// WAIT: 안정화(디케이 방식이라 너무 크게 필요 없음)
static const int TOUCH_STABLE_COUNT = 6;

// 기본 시작 delta
static const int TOUCH_START_DELTA_MV = 110;
static inline int touchStartDeltaForCh(int ch) {
  if (ch == 3) return 150;
  return TOUCH_START_DELTA_MV;
}

// WAIT 자동완화
static const uint32_t CAL_WAIT_RELAX_AFTER_MS = 250;
static const uint32_t CAL_WAIT_RELAX_STEP_MS  = 180;
static const int      CAL_WAIT_RELAX_STEP_MV  = 15;

// 최소 delta
static const int      CAL_WAIT_MIN_DELTA_MV   = 15;

// delta 상한
static const int      CAL_WAIT_DELTA_CAP_MV   = 170;

// 히스테리시스(유지) 폭
static const int      CAL_WAIT_HOLD_MV        = 60;

// CAPTURE 중 이탈 감지
static const int      TOUCH_LOST_THR_RELAX_MV = 70;
static const int      TOUCH_LOST_STABLE_SAMPLES = 50;

static const int ACTIVE_CAL_COUNT = 4;
static const uint8_t CAL_ORDER[ACTIVE_CAL_COUNT] = {2, 3, 4, 5};  // Up, Down, Left, Right

// ===================== CAL 중 터치 마스크 =====================
static uint32_t g_lastCalMaskMs = 0;
static bool    cal_is_touched[CH_COUNT] = {false};
static uint8_t cal_deb_cnt[CH_COUNT]    = {0};

static void checkIdleAndSleep(); // forward

static void calUpdateTouchMaskTick(bool force=false) {
  uint32_t now = millis();
  if (!force && (now - g_lastCalMaskMs) < ADV_SEND_PERIOD_MS) return;
  g_lastCalMaskMs = now;

  uint8_t mask = 0;

  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) {
      cal_is_touched[i] = false;
      cal_deb_cnt[i] = 0;
      g_mvQ[i] = 0;
      g_ratioQ[i] = 0;
      continue;
    }

    int mv = readSenseWithBiasRaw(i, 3);
    g_mvQ[i] = mvToQ_gui(mv);
    int deltaNow = (int)baseline_mv[i] - mv;
    if (deltaNow < 0) deltaNow = 0;
    g_ratioQ[i] = ratioToQ8(i, deltaNow);

    int delta = touchStartDeltaForCh(i);
    int enterThr = (int)baseline_mv[i] - delta;
    int exitThr  = (int)baseline_mv[i] - (delta - 30);

    bool wantTouch = cal_is_touched[i] ? !(mv >= exitThr) : (mv <= enterThr);
    if (wantTouch != cal_is_touched[i]) {
      if (++cal_deb_cnt[i] >= 2) {
        cal_is_touched[i] = wantTouch;
        cal_deb_cnt[i] = 0;
      }
    } else {
      cal_deb_cnt[i] = 0;
    }

    if (cal_is_touched[i]) mask |= (1u << i);
  }

  g_rawMask = mask;
  g_cmdMask = 0;
  g_cmdCode = CMD_CODE_NONE;
}

// ===================== baseline(무터치) 측정 =====================
struct IdleStats {
  int mean;      // 상위 20% 평균
  int hi_p2p;    // 상위 20% 구간 p2p
  int minv;
  int maxv;
};

static IdleStats measureIdleBaseline(int ch) {
  g_calStage = CAL_STAGE_BASELINE;
  g_calCh = (uint8_t)ch;
  g_rawMask = 0;

  calUpdateTouchMaskTick(true);
  advSendTick(true);

  const int N = CAL_IDLE_FS_HZ * 1;
  static int s[260];
  int n = N;
  if (n > 250) n = 250;

  uint32_t nextUs = micros();
  for (int i = 0; i < n; ++i) {
    calLedPatternTick(ch);

    int mv = readSenseWithBiasRaw(ch, ADC_SAMPLES_BASELINE);
    s[i] = mv;

    g_mvQ[ch] = mvToQ_gui(mv);
    calUpdateTouchMaskTick(false);
    advSendTick(false);

    if ((i % 20) == 0) checkIdleAndSleep();

    nextUs += 1000000UL / CAL_IDLE_FS_HZ;
    int32_t waitUs = (int32_t)(nextUs - micros());
    if (waitUs > 0) delayMicroseconds((uint32_t)waitUs);
    else nextUs = micros();
  }

  sortSmall(s, n);

  int minv = s[0];
  int maxv = s[n - 1];

  int hiStart = (int)(n * 0.80f);
  if (hiStart < 0) hiStart = 0;
  if (hiStart > n - 1) hiStart = n - 1;

  int64_t acc = 0;
  int cnt = 0;
  for (int i = hiStart; i < n; ++i) { acc += s[i]; cnt++; }
  int mean_hi = (cnt > 0) ? (int)((acc + cnt/2) / cnt) : s[n/2];

  int hi_min = s[hiStart];
  int hi_max = s[n - 1];
  int hi_p2p = hi_max - hi_min;
  if (hi_p2p < 0) hi_p2p = 0;
  if (hi_p2p > 250) hi_p2p = 250;

  IdleStats out;
  out.mean   = mean_hi;
  out.hi_p2p = hi_p2p;
  out.minv   = minv;
  out.maxv   = maxv;
  return out;
}

// ===================== 캘리 WAIT (터치 시작 대기) =====================
static void waitForTouchStart(int ch, const IdleStats& idle, int deltaBase) {
  g_calStage = CAL_STAGE_WAIT;
  g_calCh = (uint8_t)ch;
  g_rawMask = 0;

  calUpdateTouchMaskTick(true);
  advSendTick(true);

  int deltaMin = (idle.hi_p2p / 2) + 12;
  if (deltaMin < CAL_WAIT_MIN_DELTA_MV) deltaMin = CAL_WAIT_MIN_DELTA_MV;

  int delta = deltaBase;
  if (delta < deltaMin) delta = deltaMin;
  if (delta > CAL_WAIT_DELTA_CAP_MV) delta = CAL_WAIT_DELTA_CAP_MV;

  uint32_t tStart = millis();

  int idleRef = idle.maxv;

  if (enablePrint) {
    Serial.print("[CAL] ch "); Serial.print(ch);
    Serial.print(" WAIT (idleMean="); Serial.print(idle.mean);
    Serial.print(", idleMax="); Serial.print(idle.maxv);
    Serial.print(", hiP2P="); Serial.print(idle.hi_p2p);
    Serial.print(", delta0="); Serial.print(delta);
    Serial.println(")");
  }

  int stable = 0;
  bool inTouch = false;

  while (stable < TOUCH_STABLE_COUNT) {
    calLedPatternTick(ch);

    uint32_t el = millis() - tStart;
    int relaxSteps = 0;
    if (el > CAL_WAIT_RELAX_AFTER_MS) {
      relaxSteps = (int)((el - CAL_WAIT_RELAX_AFTER_MS) / CAL_WAIT_RELAX_STEP_MS);
    }
    int deltaEff = delta - relaxSteps * CAL_WAIT_RELAX_STEP_MV;
    if (deltaEff < CAL_WAIT_MIN_DELTA_MV) deltaEff = CAL_WAIT_MIN_DELTA_MV;

    int mv = readSenseWithBiasRaw(ch, ADC_SAMPLES_BASELINE);

    if (!inTouch) {
      if (mv > idleRef) idleRef = mv;
      else {
        idleRef -= ((idleRef - mv) >> 6); // 1/64
      }
    }

    int thr      = idleRef - deltaEff;
    int thr_hold = thr + CAL_WAIT_HOLD_MV;

    bool touched = inTouch ? (mv <= thr_hold) : (mv <= thr);

    g_mvQ[ch] = mvToQ_gui(mv);
    calUpdateTouchMaskTick(false);
    advSendTick(false);

    if (감동) {
      inTouch = true;
      if (stable < TOUCH_STABLE_COUNT) stable++;
      lastTouchMs = millis();
    } else {
      inTouch = false;
      if (stable > 0) stable--;
    }

    checkIdleAndSleep();
    delay(1000 / CAL_IDLE_FS_HZ);
  }
}

// 이탈 후 재터치 대기
static void waitForReTouchStart(int ch, const IdleStats& idle, int deltaBase) {
  g_calStage = CAL_STAGE_LOST;
  g_calCh = (uint8_t)ch;
  g_rawMask = 0;

  calUpdateTouchMaskTick(true);
  advSendTick(true);

  if (enablePrint) {
    Serial.print("[CAL] ch "); Serial.print(ch);
    Serial.println(" TOUCH LOST! Re-touch to restart 5s capture...");
  }
  waitForTouchStart(ch, idle, deltaBase);
}

// 1kHz 캡처 + 윈도우 평균
static int captureTouchAvgWindowed(int ch, const IdleStats& idle, int deltaBase) {
  const int N_TOTAL = (int)(CAL_TOUCH_MS * CAL_FS_HZ / 1000);

  int deltaMin = (idle.hi_p2p / 2) + 12;
  if (deltaMin < CAL_WAIT_MIN_DELTA_MV) deltaMin = CAL_WAIT_MIN_DELTA_MV;

  int delta = deltaBase;
  if (delta < deltaMin) delta = deltaMin;
  if (delta > CAL_WAIT_DELTA_CAP_MV) delta = CAL_WAIT_DELTA_CAP_MV;

  const int startThr = idle.maxv - delta;
  const int lostThr  = startThr + TOUCH_LOST_THR_RELAX_MV;

  static int buf[CAL_WIN_SAMPLES];

  for (;;) {
    g_calStage = CAL_STAGE_CAPTURE;
    g_calCh = (uint8_t)ch;

    calUpdateTouchMaskTick(true);
    advSendTick(true);

    int head = 0, filled = 0;
    int64_t winSum = 0;

    int hopCounter = 0;
    int64_t winAvgAcc = 0;
    int winAvgCount = 0;

    int lostStable = 0;

    userLedWrite(true);
    lastTouchMs = millis();

    uint32_t nextUs = micros();
    bool lost = false;

    for (int n = 0; n < N_TOTAL; ++n) {
      int mv = readSenseWithBiasRaw(ch, ADC_SAMPLES_FAST);
      g_mvQ[ch] = mvToQ_gui(mv);

      calUpdateTouchMaskTick(false);

      if (mv >= lostThr) lostStable++;
      else              lostStable = 0;

      if (lostStable >= TOUCH_LOST_STABLE_SAMPLES) {
        userLedWrite(false);
        g_calStage = CAL_STAGE_LOST;

        calUpdateTouchMaskTick(true);
        advSendTick(true);

        if (enablePrint) {
          Serial.print("[CAL] ch "); Serial.print(ch);
          Serial.println(" CAPTURE interrupted: touch released -> restart after re-touch");
        }

        waitForReTouchStart(ch, idle, deltaBase);
        lost = true;
        break;
      }

      if (filled < CAL_WIN_SAMPLES) {
        buf[head] = mv;
        winSum += mv;
        head = (head + 1) % CAL_WIN_SAMPLES;
        filled++;
      } else {
        int old = buf[head];
        winSum -= old;
        buf[head] = mv;
        winSum += mv;
        head = (head + 1) % CAL_WIN_SAMPLES;
      }

      hopCounter++;
      if (filled == CAL_WIN_SAMPLES && hopCounter >= CAL_HOP_SAMPLES) {
        hopCounter = 0;
        int winAvg = (int)(winSum / CAL_WIN_SAMPLES);
        winAvgAcc += winAvg;
        winAvgCount++;
      }

      if ((n & 0x3F) == 0) {
        lastTouchMs = millis();
        advSendTick(false);
        checkIdleAndSleep();
      } else {
        advSendTick(false);
      }

      nextUs += 1000000UL / CAL_FS_HZ;
      int32_t waitUs = (int32_t)(nextUs - micros());
      if (waitUs > 0) delayMicroseconds((uint32_t)waitUs);
      else nextUs = micros();
    }

    userLedWrite(false);

    if (lost) {
      delay(120);
      checkIdleAndSleep();
      continue;
    }

    if (winAvgCount <= 0) return readSenseWithBiasRaw(ch, ADC_SAMPLES_FAST);
    return (int)(winAvgAcc / winAvgCount);
  }
}

static uint8_t calcCalQualityQ(int span, int idleHiP2P) {
  int effective = span - (idleHiP2P * 2) - 10;
  if (effective < 0) effective = 0;
  if (effective > 220) effective = 220;
  return (uint8_t)((effective * 255 + 110) / 220);
}

// ===================== 캘리브레이션 전체 =====================
static void runCalibrationBlocking() {
  while (millis() - g_bootMs < BOOT_IGNORE_MS) {
    advSendTick(false);
    delay(10);
  }

  g_inCal = true;
  g_calStage = CAL_STAGE_NONE;
  g_calCh = 255;
  g_rawMask = 0;
  g_cmdMask = 0;
  g_cmdCode = CMD_CODE_NONE;
  g_calQualityQ = 0;
  for (int i = 0; i < CH_COUNT; ++i) {
    g_mvQ[i] = 0;
    g_ratioQ[i] = 0;
    g_thrQ[i] = mvToQ_gui(touch_enter_mv[i]);
    g_chValid[i] = isActiveSenseCh(i);
    g_calSpanMv[i] = 100;
    g_chQualityQ[i] = 0;

    if (!isActiveSenseCh(i)) {
      touch_enter_mv[i] = 0;
      touch_exit_mv[i]  = ADC_RAW_MAX_MV;
      g_thrQ[i] = 0;
    }
  }

  g_lastCalMaskMs = 0;
  for (int i = 0; i < CH_COUNT; ++i) { cal_is_touched[i] = false; cal_deb_cnt[i] = 0; }

  calUpdateTouchMaskTick(true);
  advSendTick(true);

  if (hidConnected() && !g_wetLock) {
    Mouse.release(MOUSE_LEFT);
    Mouse.release(MOUSE_RIGHT);
    Keyboard.releaseAll();
  }

  if (enablePrint) {
    Serial.println("=== CALIBRATION START ===");
    Serial.println("ch0/ch1 = OFF dummy, ch2..5 = GPIO3,GPIO4,GPIO5,GPIO6 = [Up,Down,Left,Right]");
    Serial.println("GPIO1 = wake-only before sleep, runtime electrode bias OFF; GPIO2 = fully unused/off");
    Serial.println("Calibration order = [Up(GPIO3), Down(GPIO4), Left(GPIO5), Right(GPIO6)]");
  }

  for (int orderIdx = 0; orderIdx < ACTIVE_CAL_COUNT; ++orderIdx) {
    const int ch = CAL_ORDER[orderIdx];
    if (enablePrint) {
      Serial.print("[CAL] === CHANNEL "); Serial.print(ch);
      Serial.print(" (GPIO"); Serial.print(SENSE_PINS[ch]); Serial.println(") ===");
      Serial.println("[CAL] 1) Release electrode -> baseline");
    }

    bool success = false;
    int attempts = 0;
    IdleStats idle = {0,0,0,0};
    int touchAvg = 0;
    int enter = 0, exit = 0, span = 0;
    uint8_t qualityQ = 0;

    while (!success && attempts < 2) {
      attempts++;
      idle = measureIdleBaseline(ch);
      baseline_mv[ch] = (int16_t)constrain(idle.mean, 0, ADC_RAW_MAX_MV);

      if (enablePrint) Serial.println("[CAL] 2) Touch to start capture (wait)");
      waitForTouchStart(ch, idle, touchStartDeltaForCh(ch));

      if (enablePrint) Serial.println("[CAL] 3) Capturing 5s... (touch must stay ON)");
      touchAvg = captureTouchAvgWindowed(ch, idle, touchStartDeltaForCh(ch));

      span = idle.mean - touchAvg;
      if (span < 25) span = 25;

      qualityQ = calcCalQualityQ(span, idle.hi_p2p);
      g_calQualityQ = qualityQ;
      g_chQualityQ[ch] = qualityQ;
      g_calSpanMv[ch] = (int16_t)span;

      if (qualityQ < 90) {
        g_calStage = CAL_STAGE_WEAK;
        g_calCh = (uint8_t)ch;
        advSendTick(true);
        if (enablePrint) {
          Serial.print("[CAL] WEAK ch "); Serial.print(ch);
          Serial.print(" span="); Serial.print(span);
          Serial.print(" hiP2P="); Serial.print(idle.hi_p2p);
          Serial.print(" quality="); Serial.println((int)qualityQ);
        }
        delay(250);
      } else {
        success = true;
      }
    }

    if (!success) {
      g_chValid[ch] = false;
      g_calStage = CAL_STAGE_FAIL;
      g_calCh = (uint8_t)ch;
      g_calQualityQ = qualityQ;
      touch_enter_mv[ch] = 0;
      touch_exit_mv[ch]  = ADC_RAW_MAX_MV;
      g_thrQ[ch] = 0;
      if (enablePrint) {
        Serial.print("[CAL] FAIL ch "); Serial.print(ch);
        Serial.println(" -> runtime disabled");
      }
      advSendTick(true);
      blinkN(5);
      delay(250);
      continue;
    }

    int useSpan = span;
    if (useSpan < 40) useSpan = 40;

    enter = touchAvg + (useSpan * 30) / 100;
    exit  = enter;

    enter = constrain(enter, 0, ADC_RAW_MAX_MV);
    exit  = constrain(exit,  0, ADC_RAW_MAX_MV);

    touch_enter_mv[ch] = (int16_t)enter;
    touch_exit_mv[ch]  = (int16_t)exit;
    g_thrQ[ch] = mvToQ_gui(enter);
    g_chValid[ch] = true;

    if (enablePrint) {
      Serial.print("[CAL] DONE ch "); Serial.print(ch);
      Serial.print(" idleMean="); Serial.print(idle.mean);
      Serial.print(" idleMax=");  Serial.print(idle.maxv);
      Serial.print(" hiP2P=");    Serial.print(idle.hi_p2p);
      Serial.print(" touchAvg="); Serial.print(touchAvg);
      Serial.print(" span=");     Serial.print(span);
      Serial.print(" quality=");  Serial.print((int)qualityQ);
      Serial.print(" enter=");    Serial.print(enter);
      Serial.print(" exit=");     Serial.println(exit);
    }

    g_calStage = CAL_STAGE_DONE;
    g_calCh = (uint8_t)ch;
    g_calQualityQ = qualityQ;
    calUpdateTouchMaskTick(true);
    g_mvQ[ch] = mvToQ_gui(touchAvg);
    advSendTick(true);

    blinkN(3);
    delay(250);
    checkIdleAndSleep();
  }

  if (enablePrint) Serial.println("\n=== CALIBRATION END ===");

  g_inCal = false;
  g_calStage = CAL_STAGE_NONE;
  g_calCh = 255;
  g_rawMask = 0;
  g_cmdMask = 0;
  g_cmdCode = CMD_CODE_NONE;
  g_calQualityQ = 0;
  g_wetLock = false;
  g_floodSinceMs = 0;
  g_clearSinceMs = 0;
  g_wetEnterMs = 0;
  g_postWetIgnoreUntilMs = 0;
  advSendTick(true);
}

// ===================== 딥슬립 =====================
static void checkIdleAndSleep() {
  uint32_t now = millis();
  if (now - lastTouchMs < g_idleSleepMs) return;

  if (enablePrint) {
    Serial.print(">>> Enter DEEP SLEEP (idle ");
    Serial.print(g_idleSleepMs / 1000UL);
    Serial.println("s, GPIO1 EXT0 wake enabled; internal pull OFF; external 470k pull-up only)");
  }

  Mouse.release(MOUSE_LEFT);
  Mouse.release(MOUSE_RIGHT);
  if (hidConnected()) Keyboard.releaseAll();

  btStop();

  wakeElectrodeEnableForSleep();

  Serial.flush();
  esp_deep_sleep_start();
}

// ===================== baseline 업데이트 =====================
static void baselineTick(const int mv_now[CH_COUNT], const bool touch_raw[CH_COUNT]) {
  uint32_t now = millis();
  if (now - lastBaselineUpdateMs < BASELINE_UPDATE_PERIOD_MS) return;
  lastBaselineUpdateMs = now;

  bool anyTouch = false;
  for (int i = 0; i < CH_COUNT; ++i) {
    if (touch_raw[i]) { anyTouch = true; break; }
  }
  if (anyTouch) return;

  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) continue;
    int cur  = baseline_mv[i];
    int meas = mv_now[i];
    int delta = cur - meas;

    // 애매한 하강이면 baseline 동결
    if (delta > BASELINE_FREEZE_DELTA_MV) continue;

    // 급격한 변화는 무시
    if (meas < cur - BASELINE_REJECT_DROP_MV) continue;
    if (meas > cur + BASELINE_REJECT_RISE_MV) continue;

    int diff = meas - cur;
    cur += (diff >> BASELINE_IIR_SHIFT);
    baseline_mv[i] = (int16_t)constrain(cur, 0, ADC_RAW_MAX_MV);
  }
}

// ===================== 런타임 노이즈 p2p =====================
static void noiseTick(const int mv_now[CH_COUNT], const bool touch_raw[CH_COUNT]) {
  bool anyTouch = false;
  for (int i = 0; i < CH_COUNT; ++i) {
    if (touch_raw[i]) { anyTouch = true; break; }
  }
  if (anyTouch) return;

  uint32_t now = millis();
  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) continue;
    if (noise_t0_ms[i] == 0) {
      noise_t0_ms[i] = now;
      noise_min_mv[i] = mv_now[i];
      noise_max_mv[i] = mv_now[i];
    } else {
      if (mv_now[i] < noise_min_mv[i]) noise_min_mv[i] = mv_now[i];
      if (mv_now[i] > noise_max_mv[i]) noise_max_mv[i] = mv_now[i];
    }

    if (now - noise_t0_ms[i] >= NOISE_WIN_MS) {
      int p2p = noise_max_mv[i] - noise_min_mv[i];
      if (p2p < 0) p2p = 0;
      if (p2p > 400) p2p = 400;
      noise_p2p_mv[i] = (int16_t)p2p;

      noise_t0_ms[i] = now;
      noise_min_mv[i] = mv_now[i];
      noise_max_mv[i] = mv_now[i];
    }
  }
}

static void refreshBootBaselineBlocking(uint32_t sampleMs = CAL_IDLE_MS_BOOT) {
  uint32_t t0 = millis();
  uint32_t cnt[CH_COUNT] = {0};
  int64_t  acc[CH_COUNT] = {0};

  lastTouchMs = millis();

  while (millis() - t0 < sampleMs) {
    for (int i = 0; i < CH_COUNT; ++i) {
      if (!isActiveSenseCh(i)) {
        g_mvQ[i] = 0;
        g_ratioQ[i] = 0;
        continue;
      }
      int mv = readMvTrimmedMeanRaw(SENSE_PINS[i], ADC_SAMPLES_BASELINE);
      acc[i] += mv;
      cnt[i] += 1;
      g_mvQ[i] = mvToQ_gui(mv);
    }
    advSendTick(false);
    delay(5);
    lastTouchMs = millis();
  }

  const uint32_t now = millis();
  for (int i = 0; i < CH_COUNT; ++i) {
    if (cnt[i] > 0) {
      int idle = (int)(acc[i] / (int32_t)cnt[i]);
      baseline_mv[i] = (int16_t)constrain(idle, 0, ADC_RAW_MAX_MV);
      g_mvQ[i] = mvToQ_gui(idle);
      noise_p2p_mv[i] = 0;
      noise_min_mv[i] = idle;
      noise_max_mv[i] = idle;
      noise_t0_ms[i]  = now;
    }
  }
}

static void handlePendingRuntimeRecalibration() {
  if (!g_runtimeRecalReq) return;

  const uint8_t cmd = g_runtimeRecalCmd;
  g_runtimeRecalReq = false;
  g_runtimeRecalCmd = 0;

  if (cmd != CTRL_CMD_REINIT_RECAL) return;

  if (enablePrint) {
    Serial.println("[GUI] Runtime re-init + recalibration requested");
  }

  releaseAllHidOutputs();
  clearTouchStates();

  g_wetLock = false;
  g_floodSinceMs = 0;
  g_clearSinceMs = 0;
  g_wetEnterMs = 0;
  g_postWetIgnoreUntilMs = 0;
  g_rawMask = 0;
  g_cmdMask = 0;
  g_cmdCode = CMD_CODE_NONE;
  g_calQualityQ = 0;
  g_lastCalMaskMs = 0;

  for (int i = 0; i < CH_COUNT; ++i) {
    cal_is_touched[i] = false;
    cal_deb_cnt[i] = 0;
    g_ratioQ[i] = 0;
    g_chQualityQ[i] = 0;
    g_calSpanMv[i] = 100;

    if (!isActiveSenseCh(i)) {
      g_chValid[i] = false;
      touch_enter_mv[i] = 0;
      touch_exit_mv[i]  = ADC_RAW_MAX_MV;
      g_thrQ[i] = 0;
      g_mvQ[i] = 0;
    } else {
      g_chValid[i] = true;
      touch_enter_mv[i] = 1500;
      touch_exit_mv[i]  = 2000;
      g_thrQ[i] = mvToQ_gui(touch_enter_mv[i]);
    }
  }

  advSendTick(true);
  refreshBootBaselineBlocking(CAL_IDLE_MS_BOOT);
  advSendTick(true);

  lastTouchMs = millis();
  lastBaselineUpdateMs = millis();

  runCalibrationBlocking();

  lastTouchMs = millis();
  lastBaselineUpdateMs = millis();
  advSendTick(true);
}

// ===================== SETUP =====================
void setup() {
  Serial.begin(115200);
  delay(200);

  g_bootMs = millis();

  pinMode(USER_LED_PIN, OUTPUT);
  userLedWrite(false);

  wakeElectrodeRuntimeOff();  // GPIO1은 부팅/웨이크업 후 전극 바이어스 완전 OFF

  Keyboard.begin();
  Mouse.begin();

  bool bleComboReady = waitForBleComboReady(6000);
  setBleTxPowerModerate();

  for (int i = 0; i < CH_COUNT; ++i) {
    if (isActiveSenseCh(i)) {
      analogSetPinAttenuation(SENSE_PINS[i], ADC_11db);
      pinMode(SENSE_PINS[i], INPUT);
    }

    is_touched[i] = false;
    deb_enter[i] = 0;
    deb_exit[i]  = 0;

    bias_mv[i] = 0;
    touch_enter_mv[i] = 1500;
    touch_exit_mv[i]  = 2000;
    baseline_mv[i] = 2500;

    g_mvQ[i] = 0;
    g_ratioQ[i] = 0;
    g_thrQ[i] = mvToQ_gui(touch_enter_mv[i]);
    g_chValid[i] = isActiveSenseCh(i);
    g_calSpanMv[i] = 100;
    g_chQualityQ[i] = 0;

    if (!isActiveSenseCh(i)) {
      touch_enter_mv[i] = 0;
      touch_exit_mv[i]  = ADC_RAW_MAX_MV;
      g_thrQ[i] = 0;
    }

    noise_p2p_mv[i] = 0;
    noise_min_mv[i] = 0;
    noise_max_mv[i] = 0;
    noise_t0_ms[i]  = 0;
  }

  prev_left_cmd  = false;
  prev_right_cmd = false;
  g_rawMask = 0;
  g_cmdMask = 0;
  g_cmdCode = CMD_CODE_NONE;
  g_calQualityQ = 0;

  if (!bleComboReady && enablePrint) {
    Serial.println("[BLE] BleCombo HID server wait timeout before telemetry/control init");
  }

  if (!ctrlInit() && enablePrint) {
    Serial.println("[BLE] Control service init failed");
  }
  advInit();

  lastTouchMs = millis();

  // ---- 부팅 직후 baseline ----
  refreshBootBaselineBlocking(CAL_IDLE_MS_BOOT);
  advSendTick(true);

  // ---- 캘리브레이션 ----
  runCalibrationBlocking();

  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) {
      g_chValid[i] = false;
      touch_enter_mv[i] = 0;
      touch_exit_mv[i]  = ADC_RAW_MAX_MV;
      g_thrQ[i] = 0;
      g_mvQ[i] = 0;
      g_ratioQ[i] = 0;
      g_deltaRawRt[i] = 0;
      g_deltaEffRt[i] = 0;
    }
  }

  lastTouchMs = millis();
  lastBaselineUpdateMs = millis();

  if (enablePrint) {
    Serial.println("Init done. Start monitoring. (HM v4 telemetry + HT v1 threshold header)");
    Serial.println("active_ch,gpio,mv_now,base,delta,dynEnter,dynExit,noise,enter,exit,raw,cmd");
    Serial.println("[HOME] removed (GPIO9/D10 not used)");
    Serial.print("[FORCE_OFF] mv >= ");
    Serial.print(TOUCH_FORCE_OFF_MV);
    Serial.println(" => no touch");
  }
}

// ===================== LOOP =====================
void loop() {
  if (millis() - g_bootMs < BOOT_IGNORE_MS) {
    advSendTick(false);
    checkIdleAndSleep();
    delay(LOOP_DELAY_MS);
    return;
  }

  if (g_inCal) {
    advSendTick(false);
    checkIdleAndSleep();
    delay(LOOP_DELAY_MS);
    return;
  }

  if (g_runtimeRecalReq) {
    handlePendingRuntimeRecalibration();
    delay(LOOP_DELAY_MS);
    return;
  }

  int  mv_now[CH_COUNT];
  bool touch_raw[CH_COUNT];
  bool touch_cmd[CH_COUNT];
  bool abs_touch_like[CH_COUNT];

  // 1) 측정 (RAW)
  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i) || !g_chValid[i]) {
      mv_now[i] = 0;
      touch_raw[i] = false;
      touch_cmd[i] = false;
      abs_touch_like[i] = false;
      is_touched[i] = false;
      deb_enter[i] = 0;
      deb_exit[i] = 0;
      g_mvQ[i] = 0;
      g_ratioQ[i] = 0;
      g_deltaRawRt[i] = 0;
      g_deltaEffRt[i] = 0;
      continue;
    }

    int mv = readSenseWithBiasRaw(i, ADC_SAMPLES_MAIN);
    mv_now[i] = mv;
    g_mvQ[i] = mvToQ_gui(mv);
    int delta = (int)baseline_mv[i] - mv;
    if (delta < 0) delta = 0;
    g_deltaRawRt[i] = (int16_t)delta;
    g_deltaEffRt[i] = (int16_t)delta;
  }

  g_commonModeDeltaRt = (int16_t)calcCommonModeDeltaRt();
  for (int i = 0; i < CH_COUNT; ++i) {
    int eff = (int)g_deltaRawRt[i] - (int)g_commonModeDeltaRt;
    if (eff < 0) eff = 0;
    g_deltaEffRt[i] = (int16_t)eff;
    g_ratioQ[i] = ratioToQ8(i, eff);

    abs_touch_like[i] = false;
    if (g_chValid[i] && !forceNoTouchByVoltage(mv_now[i])) {
      bool calLike = (mv_now[i] <= touch_enter_mv[i]);
      bool deltaLike = ((int)g_deltaRawRt[i] >= dynMinTouchDelta(i));
      abs_touch_like[i] = (calLike || deltaLike);
    }
  }

  // 2) 1차 판정(raw)
  for (int i = 0; i < CH_COUNT; ++i) {
    // ===== 런타임 하드 차단 =====
    // 캘리브레이션 이후, 읽기값이 1.8V 이상이면 즉시 무터치 처리
    if (!isActiveSenseCh(i) || !g_chValid[i] || forceNoTouchByVoltage(mv_now[i])) {
      is_touched[i] = false;
      deb_enter[i]  = 0;
      deb_exit[i]   = 0;
      touch_raw[i]  = false;
      continue;
    }

    uint8_t enterNeed = enterDebNeeded(i);
    uint8_t exitNeed  = exitDebNeeded(i);

    if (!is_touched[i]) {
      if (touchEnterCh(i, mv_now[i])) {
        if (++deb_enter[i] >= enterNeed) {
          is_touched[i] = true;
          deb_enter[i] = 0;
          deb_exit[i]  = 0;
        }
      } else {
        deb_enter[i] = 0;
      }
    } else {
      if (touchExitCh(i, mv_now[i])) {
        if (++deb_exit[i] >= exitNeed) {
          is_touched[i] = false;
          deb_exit[i]  = 0;
          deb_enter[i] = 0;
        }
      } else {
        deb_exit[i] = 0;
      }
    }
    touch_raw[i] = is_touched[i];
  }

  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) {
      is_touched[i] = false;
      deb_enter[i] = 0;
      deb_exit[i] = 0;
      touch_raw[i] = false;
      abs_touch_like[i] = false;
      g_mvQ[i] = 0;
      g_ratioQ[i] = 0;
      g_deltaRawRt[i] = 0;
      g_deltaEffRt[i] = 0;
    }
  }

  int rawActiveCount = 0;
  int absActiveCount = 0;
  int effPeakDelta = 0;
  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) continue;
    if (touch_raw[i]) rawActiveCount++;
    if (abs_touch_like[i]) absActiveCount++;
    if ((int)g_deltaEffRt[i] > effPeakDelta) effPeakDelta = (int)g_deltaEffRt[i];
  }

  bool floodCandidate = (absActiveCount >= FLOOD_TOUCH_COUNT) && ((rawActiveCount > FLOOD_EFF_ACTIVE_MAX) || (effPeakDelta <= FLOOD_EFF_PEAK_MAX_MV));
  if (absActiveCount >= FLOOD_HARD_COUNT && ((rawActiveCount > FLOOD_EFF_ACTIVE_MAX) || (effPeakDelta <= FLOOD_EFF_PEAK_MAX_MV))) {
    if (g_floodSinceMs == 0) g_floodSinceMs = millis();
  } else if (floodCandidate) {
    if (g_floodSinceMs == 0) g_floodSinceMs = millis();
  } else {
    g_floodSinceMs = 0;
  }

  if (!g_wetLock && g_floodSinceMs != 0 && (millis() - g_floodSinceMs) >= FLOOD_ENTER_MS) {
    enterWetLock();
  }

  if (!g_wetLock) {
    // 3) baseline/노이즈 (raw 기준)
    baselineTick(mv_now, touch_raw);
    noiseTick(mv_now, touch_raw);
  }

  // 4) 실제 명령용 상태(cmd) 구성
  for (int i = 0; i < CH_COUNT; ++i) touch_cmd[i] = touch_raw[i];

  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) touch_cmd[i] = false;
  }

  if (g_wetLock) {
    clearTouchStates();
    for (int i = 0; i < CH_COUNT; ++i) touch_cmd[i] = false;

    uint32_t wetElapsedMs = millis() - g_wetEnterMs;
    bool canSoftClear = (absActiveCount <= 1);
    bool canRescueClear = (wetElapsedMs >= FLOOD_RESCUE_AFTER_MS) && (absActiveCount <= 2);
    bool canForceClear = (wetElapsedMs >= FLOOD_FORCE_AFTER_MS);

    if (absActiveCount == 0) {
      if (g_clearSinceMs == 0) g_clearSinceMs = millis();
      if ((millis() - g_clearSinceMs) >= FLOOD_CLEAR_MS) {
        quickRebaselineAll();
        g_wetLock = false;
        g_floodSinceMs = 0;
        g_clearSinceMs = 0;
        g_wetEnterMs = 0;
      }
    } else if (canSoftClear) {
      if (g_clearSinceMs == 0) g_clearSinceMs = millis();
      if ((millis() - g_clearSinceMs) >= FLOOD_SOFT_CLEAR_MS) {
        quickRebaselineAll();
        g_wetLock = false;
        g_floodSinceMs = 0;
        g_clearSinceMs = 0;
        g_wetEnterMs = 0;
      }
    } else if (canRescueClear || canForceClear) {
      quickRebaselineAll();
      g_wetLock = false;
      g_floodSinceMs = 0;
      g_clearSinceMs = 0;
      g_wetEnterMs = 0;
    } else {
      g_clearSinceMs = 0;
    }
  }

  if (!g_wetLock && millis() < g_postWetIgnoreUntilMs) {
    clearTouchStates();
    for (int i = 0; i < CH_COUNT; ++i) {
      touch_raw[i] = false;
      touch_cmd[i] = false;
    }
    rawActiveCount = 0;
  }

  // 4-0) 활성 4채널 전체 동시터치 제한
  // 허용:
  //   - 단일 터치 1개
  //   - 정확히 2개 동시터치이면서 방향 대각선 4조합만
  //     * Up   + Left
  //     * Up   + Right
  //     * Down + Left
  //     * Down + Right
  // 그 외의 2개 이상 동시터치는 전부 무효 처리
  bool allowMultiTouch = false;
  if (rawActiveCount == 2) {
    bool rawUp    = touch_raw[2];
    bool rawDown  = touch_raw[3];
    bool rawLeft  = touch_raw[4];
    bool rawRight = touch_raw[5];
    bool rawClick = ENABLE_CLICK_CHANNELS ? (touch_raw[0] || touch_raw[1]) : false;

    const bool oneVertical   = (rawUp ^ rawDown);
    const bool oneHorizontal = (rawLeft ^ rawRight);

    allowMultiTouch = (!rawClick && oneVertical && oneHorizontal);
  }

  if (rawActiveCount >= 2 && !allowMultiTouch) {
    for (int i = 0; i < CH_COUNT; ++i) touch_cmd[i] = false;
  }

  // 4-1) 상하/좌우 충돌 해소
  auto score = [&](int idx){ return scoreFromBaseline(idx, mv_now[idx]); };
  static const int PAIR_MARGIN_MV = 120;

  bool up    = touch_cmd[2];
  bool down  = touch_cmd[3];
  bool left  = touch_cmd[4];
  bool right = touch_cmd[5];

  if (up && down) {
    int su = score(2), sd = score(3);
    if      (su > sd + PAIR_MARGIN_MV) down = false;
    else if (sd > su + PAIR_MARGIN_MV) up   = false;
    else { up = down = false; }
  }
  if (left && right) {
    int sl = score(4), sr = score(5);
    if      (sl > sr + PAIR_MARGIN_MV) right = false;
    else if (sr > sl + PAIR_MARGIN_MV) left  = false;
    else { left = right = false; }
  }

  touch_cmd[2] = up;
  touch_cmd[3] = down;
  touch_cmd[4] = left;
  touch_cmd[5] = right;

  // 4-2) 클릭 채널 우세도 조건
  if (ENABLE_CLICK_CHANNELS) {
    if (touch_cmd[0] && !clickDominantEnough(0, mv_now, touch_raw)) touch_cmd[0] = false;
    if (touch_cmd[1] && !clickDominantEnough(1, mv_now, touch_raw)) touch_cmd[1] = false;
  } else {
    touch_cmd[0] = false;
    touch_cmd[1] = false;
  }

  // 4-3) 좌/우 클릭 동시 발생 시 하나만 허용
  if (ENABLE_CLICK_CHANNELS && touch_cmd[0] && touch_cmd[1]) {
    int s0 = score(0), s1 = score(1);
    if      (s0 > s1 + CLICK_PAIR_MARGIN_MV) touch_cmd[1] = false;
    else if (s1 > s0 + CLICK_PAIR_MARGIN_MV) touch_cmd[0] = false;
    else { touch_cmd[0] = false; touch_cmd[1] = false; }
  }

  // 5) 마스크/텔레메트리
  uint8_t rawMask = 0;
  uint8_t cmdMask = 0;
  for (int i = 0; i < CH_COUNT; ++i) {
    if (!isActiveSenseCh(i)) continue;
    if (touch_raw[i]) rawMask |= (1u << i);
    if (touch_cmd[i]) cmdMask |= (1u << i);
  }
  g_rawMask = rawMask;
  g_cmdMask = cmdMask;

  uint8_t cmdCode = CMD_CODE_NONE;
  bool cL = touch_cmd[0];
  bool cR = touch_cmd[1];
  bool up2 = touch_cmd[2];
  bool dn2 = touch_cmd[3];
  bool lf2 = touch_cmd[4];
  bool rt2 = touch_cmd[5];

  if      (cL)               cmdCode = CMD_CODE_LCLICK;
  else if (cR)               cmdCode = CMD_CODE_RCLICK;
  else if (up2 && lf2)       cmdCode = CMD_CODE_UP_LEFT;
  else if (up2 && rt2)       cmdCode = CMD_CODE_UP_RIGHT;
  else if (dn2 && lf2)       cmdCode = CMD_CODE_DOWN_LEFT;
  else if (dn2 && rt2)       cmdCode = CMD_CODE_DOWN_RIGHT;
  else if (up2)              cmdCode = CMD_CODE_UP;
  else if (dn2)              cmdCode = CMD_CODE_DOWN;
  else if (lf2)              cmdCode = CMD_CODE_LEFT;
  else if (rt2)              cmdCode = CMD_CODE_RIGHT;
  if (g_wetLock) cmdCode = CMD_CODE_NONE;
  g_cmdCode = cmdCode;
  advSendTick(false);

  // 6) 로그
  if (enablePrint && (millis() - lastPrintMs) >= PRINT_INTERVAL_MS) {
    lastPrintMs = millis();
    for (int i = 0; i < CH_COUNT; ++i) {
      if (!isActiveSenseCh(i)) continue;
      int delta = g_deltaEffRt[i];
      Serial.print(i); Serial.print(',');
      Serial.print(SENSE_PINS[i]); Serial.print(',');
      Serial.print(mv_now[i]); Serial.print(',');
      Serial.print(baseline_mv[i]); Serial.print(',');
      Serial.print(delta); Serial.print(',');
      Serial.print(dynMinTouchDelta(i)); Serial.print(',');
      Serial.print(dynMinReleaseDelta(i)); Serial.print(',');
      Serial.print(noise_p2p_mv[i]); Serial.print(',');
      Serial.print(touch_enter_mv[i]); Serial.print(',');
      Serial.print(touch_exit_mv[i]); Serial.print(',');
      Serial.print(touch_raw[i] ? 1 : 0); Serial.print(',');
      Serial.println(touch_cmd[i] ? 1 : 0);
    }
  }

  // 7) HID
  if (hidConnected() && !g_wetLock) {
    bool leftClickCmd  = ENABLE_CLICK_CHANNELS ? touch_cmd[0] : false;
    bool rightClickCmd = ENABLE_CLICK_CHANNELS ? touch_cmd[1] : false;

    if (leftClickCmd != prev_left_cmd) {
      if (leftClickCmd) Mouse.press(MOUSE_LEFT);
      else              Mouse.release(MOUSE_LEFT);
      prev_left_cmd = leftClickCmd;
    }

    if (rightClickCmd != prev_right_cmd) {
      if (rightClickCmd) Mouse.press(MOUSE_RIGHT);
      else               Mouse.release(MOUSE_RIGHT);
      prev_right_cmd = rightClickCmd;
    }

    int vx = (touch_cmd[5] ? 1 : 0) - (touch_cmd[4] ? 1 : 0);
    int vy = (touch_cmd[2] ? 1 : 0) - (touch_cmd[3] ? 1 : 0);

    if (vx || vy) {
      int dx = (vx && vy) ? (vx > 0 ? DIAG_STEP : -DIAG_STEP)
                          : (vx > 0 ? MOVE_STEP : (vx < 0 ? -MOVE_STEP : 0));

      int dy = (vx && vy) ? (vy > 0 ? -DIAG_STEP : +DIAG_STEP)
                          : (vy > 0 ? -MOVE_STEP : (vy < 0 ? +MOVE_STEP : 0));

      Mouse.move(dx, dy);
    }
  } else {
    Mouse.release(MOUSE_LEFT);
    Mouse.release(MOUSE_RIGHT);
    prev_left_cmd  = false;
    prev_right_cmd = false;
  }

  // 8) idle
  bool anyRawTouch = false;
  for (int i = 0; i < CH_COUNT; ++i) {
    if (touch_raw[i]) { anyRawTouch = true; break; }
  }
  if (anyRawTouch || g_wetLock) lastTouchMs = millis();

  checkIdleAndSleep();
  delay(LOOP_DELAY_MS);
}'





























