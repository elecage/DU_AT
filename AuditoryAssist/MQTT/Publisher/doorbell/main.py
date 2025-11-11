import machine
import time
import network
from umqtt.simple import MQTTClient
import ujson

# ── Wi-Fi ──────────────────────────────────────────────────────────────────────
WIFI_SSID = 'HealthcareConvergenceLab'
WIFI_PASSWORD = 'Healthcare1234!'

# ── MQTT ───────────────────────────────────────────────────────────────────────
MQTT_BROKER = '192.168.0.24'
MQTT_TOPIC  = 'doorbell/sensor'      # 판단서버 config.json 기준
MQTT_CLIENT_ID = "doorbell_1"

# ── GPIO / 버튼 ───────────────────────────────────────────────────────────────
BUTTON_PIN   = 1                      # GPIO 1번
DEBOUNCE_MS  = 300                    # 디바운스 시간

button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
# 배선: GPIO1 ↔ 버튼 ↔ GND
# PULL_UP이므로 평소 HIGH(1), 누르면 GND로 FALLING(0)

# 상태 플래그
_last_press_ms = 0
_press_flag = False

# ── 유틸 ───────────────────────────────────────────────────────────────────────
def get_timestamp_string():
    now = time.localtime()
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*now)

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("📡 WiFi 연결 중...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        while not wlan.isconnected():
            time.sleep(0.3)
    print("✅ WiFi 연결 완료:", wlan.ifconfig())
    return wlan

def connect_mqtt():
    try:
        client = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER)
        client.connect()
        print("✅ MQTT 연결 완료")
        return client
    except Exception as e:
        print("❌ MQTT 연결 실패:", e)
        return None

def send_status_message(client, value):
    """value: 1=버튼 눌림 이벤트"""
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "button_pressed" if value == 1 else "normal",
        "value": value,
        "timestamp": get_timestamp_string()
    }
    try:
        client.publish(MQTT_TOPIC, ujson.dumps(payload))
        print(f"📤 전송 완료 → {MQTT_TOPIC}: {payload}")
    except Exception as e:
        print("❗ MQTT 전송 실패:", e)

# ── IRQ 콜백: 가볍게(플래그만 세움) ───────────────────────────────────────────
def _button_irq_handler(pin):
    global _last_press_ms, _press_flag
    now = time.ticks_ms()
    # 최소 간격(디바운스)
    if time.ticks_diff(now, _last_press_ms) < DEBOUNCE_MS:
        return
    _press_flag = True
    _last_press_ms = now

# ── 메인 ──────────────────────────────────────────────────────────────────────
def main():
    # Wi-Fi / MQTT 연결
    connect_wifi()
    mqtt_client = connect_mqtt()
    if mqtt_client is None:
        return

    # 버튼: FALLING(1→0)에서 눌림 감지
    button.irq(trigger=machine.Pin.IRQ_FALLING, handler=_button_irq_handler)

    print("🔔 버튼 대기 중... (GPIO 1, PULL_UP)")

    while True:
        try:
            global _press_flag
            if _press_flag:
                _press_flag = False
                # 노이즈 억제용 소량 지연 후 실제 값 재확인
                time.sleep_ms(25)
                if button.value() == 0:  # 여전히 LOW이면 진짜 눌림
                    print("🔔 버튼 눌림 확정!")
                    send_status_message(mqtt_client, 1)  # 1 = 버튼 눌림 이벤트
            time.sleep_ms(20)
        except KeyboardInterrupt:
            print("\n🛑 종료")
            break
        except Exception as e:
            print("⚠️ 루프 오류:", e)
            time.sleep_ms(200)

# 실행
main()

f
