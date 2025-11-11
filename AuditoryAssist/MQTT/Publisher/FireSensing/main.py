import machine
import time
import network
from simple import MQTTClient
import ujson

# 🔧 SHZ 센서 핀 설정
FIRE_SENSOR_PIN = 15
fire_sensor = machine.Pin(FIRE_SENSOR_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

# 🔧 Wi-Fi 정보
WIFI_SSID = 'HealthcareConvergenceLab'
WIFI_PASSWORD = 'Healthcare1234!'

# 🔧 MQTT 정보
MQTT_BROKER = '192.168.0.24'
MQTT_TOPIC = 'shz/sensor'
MQTT_CLIENT_ID = "shz_sensor_pico"

# ✅ 현재 시간 문자열 생성
def get_timestamp_string():
    now = time.localtime()
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*now)

# ✅ Wi-Fi 연결
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("📡 WiFi 연결 중...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        while not wlan.isconnected():
            time.sleep(0.5)
    print("✅ WiFi 연결 완료:", wlan.ifconfig())

# ✅ MQTT 연결
def connect_mqtt():
    try:
        client = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER)
        client.connect()
        print("✅ MQTT 연결 완료")
        return client
    except Exception as e:
        print("❌ MQTT 연결 실패:", e)
        return None

# ✅ 상태 메시지 전송
def send_status_message(client, value):
    payload = {
        "sensor_id": MQTT_CLIENT_ID,
        "event": "shz_detected",
        "value": value,  # 문자열: "감지됨" or "정상"
        "timestamp": get_timestamp_string()
    }
    print(f"📤 상태 전송: {value}")
    try:
        client.publish(MQTT_TOPIC, ujson.dumps(payload).encode()) # ✅ 반드시 JSON으로 변환
    except Exception as e:
        print("❗ MQTT 전송 실패:", e)

# ▶ 메인 루프
def main():
    connect_wifi()
    mqtt_client = connect_mqtt()
    if mqtt_client is None:
        return

    print("📍 SHZ 센서 모니터링 시작")
    is_fire_waiting = False
    fire_detected_time = 0

    while True:
        now = time.ticks_ms()
        sensor_val = fire_sensor.value()  # 0이면 감지됨

        if not is_fire_waiting and sensor_val == 0:
            send_status_message(mqtt_client, "감지됨")
            is_fire_waiting = True
            fire_detected_time = now

        if is_fire_waiting and time.ticks_diff(now, fire_detected_time) > 15000:
            send_status_message(mqtt_client, "정상")
            is_fire_waiting = False

        time.sleep(0.1)

main()

