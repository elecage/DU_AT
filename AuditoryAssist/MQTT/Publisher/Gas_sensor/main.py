import machine, time, network, ujson
from simple import MQTTClient   # Pico 내부의 umqtt.simple(=simple.py)를 사용

# ========= 하드웨어/네트워크 설정 =========
# MQ5 센서: ADC0 = GP26
GAS_SENSOR_PIN = 26
gas_sensor = machine.ADC(GAS_SENSOR_PIN)

# 상태 LED
LED_PIN = 28
led = machine.Pin(LED_PIN, machine.Pin.OUT)

# Wi‑Fi
WIFI_SSID = 'HealthcareConvergenceLab'
WIFI_PASSWORD = 'Healthcare1234!'

# MQTT
MQTT_BROKER   = '192.168.0.24'
MQTT_TOPIC    = 'gas/sensor'
MQTT_CLIENTID = 'gas_sensor_pico'   # 기기마다 고유 ID 권장

# 동작 파라미터
KEEPALIVE_SEC    = 60                 # 브로커 keepalive
PING_INTERVAL_MS = 30_000             # 30초마다 ping
NORMAL_REPORT_MS = 2_000              # 정상 상태 보고 주기
FIRE_HOLDOFF_MS  = 5_000             # 화재 감지 후 정상 복귀 대기
LED_BLINK_MS     = 500                # LED 점멸 주기
THRESHOLD        = 30_000             # MQ5 임계값(필요시 조정)

# 내부 상태
wlan = None
client = None

# ========= 유틸 =========
def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]

def wifi_connect_blocking():
    """Wi‑Fi가 연결될 때까지 블로킹 재시도."""
    global wlan
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

    if wlan.isconnected():
        return

    print("📡 Wi‑Fi 연결 중...")
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    while not wlan.isconnected():
        time.sleep(0.5)
    print("✅ Wi‑Fi 연결 완료:", wlan.ifconfig())

def wifi_ensure():
    """연결이 끊기면 즉시 재연결."""
    if not wlan.isconnected():
        wifi_connect_blocking()

def mqtt_connect_blocking():
    """MQTT 연결(블로킹)."""
    global client
    client = MQTTClient(MQTT_CLIENTID, MQTT_BROKER, keepalive=KEEPALIVE_SEC)
    client.connect()
    print("✅ MQTT 연결 완료")

def mqtt_ping():
    """가능하면 ping으로 세션 유지. 미지원이면 건너뜀."""
    try:
        client.ping()
        return True
    except Exception as e:
        print("⚠️ ping 실패:", e)
        return False

def mqtt_reconnect_with_backoff():
    """브로커 재연결을 지수 백오프로 시도."""
    backoff = 0.5
    for attempt in range(6):  # 최대 6회(0.5 → 1 → 2 → 4 → 5 → 5초)
        try:
            # Wi‑Fi도 혹시 끊겼다면 먼저 복구
            wifi_ensure()
            try:
                client.disconnect()
            except:
                pass
            mqtt_connect_blocking()
            return True
        except Exception as e:
            print("❌ MQTT 재연결 실패[%d]:" % (attempt+1), e)
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
    return False

def publish_json(topic, obj):
    """JSON을 안전하게 발행. 실패 시 자동 재연결 후 재시도."""
    msg = ujson.dumps(obj)
    if isinstance(msg, str):
        msg = msg.encode()

    backoff = 0.5
    for attempt in range(4):
        try:
            client.publish(topic, msg)
            return True
        except Exception as e:
            print("❗ publish 실패[%d]:" % (attempt+1), e)
            # 연결 상태 회복 시도
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
    return False

def send_status(value, is_fire):
    payload = {
        "sensor_id": MQTT_CLIENTID,
        "event": "gas_detected",
        "status": "화재 감지!" if is_fire else "정상",
        "value": value,
        "timestamp": now_str()
    }
    print("📤 상태 전송:", "🔥 화재 감지!" if is_fire else "✅ 정상", f"({value})")
    publish_json(MQTT_TOPIC, payload)

# ========= 메인 =========
def main():
    wifi_connect_blocking()
    mqtt_connect_blocking()

    print("📍 MQ5 센서 모니터링 시작 (자동 복구 모드)")

    led_state = False
    t_led  = time.ticks_ms()
    t_last = time.ticks_ms()
    t_ping = time.ticks_ms()

    is_in_fire_wait = False
    t_fire = 0

    while True:
        now = time.ticks_ms()

        # LED 토글로 작동 확인
        if time.ticks_diff(now, t_led) >= LED_BLINK_MS:
            led_state = not led_state
            led.value(led_state)
            t_led = now

        # 주기적 Wi‑Fi / MQTT 헬스체크
        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            wifi_ensure()
            # ping 실패 시 재연결
            if not mqtt_ping():
                mqtt_reconnect_with_backoff()
            t_ping = now

        # 센서 읽기
        gas_value = gas_sensor.read_u16()

        # 임계 초과 → 1회 알림 후 15초 휴지
        if (not is_in_fire_wait) and gas_value > THRESHOLD:
            send_status(gas_value, True)
            t_fire = now
            is_in_fire_wait = True
            print("🔥 MQ5 감지 → 15초 대기 시작")

        # 휴지 시간 종료 → 정상 1회 알림
        if is_in_fire_wait and time.ticks_diff(now, t_fire) > FIRE_HOLDOFF_MS:
            send_status(gas_value, False)
            is_in_fire_wait = False
            print("🔄 정상 상태 복귀")

        # 평시 정상 보고(2초 간격)
        if (not is_in_fire_wait) and time.ticks_diff(now, t_last) > NORMAL_REPORT_MS:
            send_status(gas_value, False)
            t_last = now

        time.sleep(0.1)

# 자동 실행
main()


