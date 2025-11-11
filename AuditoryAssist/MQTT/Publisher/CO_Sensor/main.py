import machine, time, network, ujson
from simple import MQTTClient  # umqtt.simple

# ========= 하드웨어/네트워크 =========
MQ7_SENSOR_PIN = 27                 # ADC1 = GP27
mq7_sensor = machine.ADC(MQ7_SENSOR_PIN)

LED_PIN = 28                        # 동작 표시 LED
led = machine.Pin(LED_PIN, machine.Pin.OUT)

WIFI_SSID = 'HealthcareConvergenceLab'
WIFI_PASSWORD = 'Healthcare1234!'

MQTT_BROKER   = '192.168.0.24'
MQTT_TOPIC    = 'mq7/sensor'        # Node-RED가 구독하는 토픽
MQTT_CLIENTID = 'mq7_sensor_pico'   # 기기별로 고유하게!

# ========= 동작 파라미터 =========
KEEPALIVE_SEC    = 60
PING_INTERVAL_MS = 30_000    # 30초마다 ping
NORMAL_REPORT_MS = 2_000     # 평시 보고 주기
FIRE_HOLDOFF_MS  = 15_000    # 화재 발생 후 정상 복귀 대기
LED_BLINK_MS     = 500
THRESHOLD        = 30_000    # 필요 시 환경에 맞게 조정

# ========= 내부 상태 =========
wlan = None
client = None

# ========= 유틸 =========
def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]

def wifi_connect_blocking():
    """Wi‑Fi 연결될 때까지 대기."""
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
    """연결 끊기면 즉시 복구."""
    if not wlan.isconnected():
        wifi_connect_blocking()

def mqtt_connect_blocking():
    """MQTT 브로커 접속."""
    global client
    client = MQTTClient(MQTT_CLIENTID, MQTT_BROKER, keepalive=KEEPALIVE_SEC)
    client.connect()
    print("✅ MQTT 연결 완료")

def mqtt_ping():
    """ping으로 세션 유지(미지원이면 예외)."""
    try:
        client.ping()
        return True
    except Exception as e:
        print("⚠️ ping 실패:", e)
        return False

def mqtt_reconnect_with_backoff():
    """브로커 재연결을 지수 백오프로 시도."""
    backoff = 0.5
    for attempt in range(6):  # 0.5→1→2→4→5→5초
        try:
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
    """JSON 안전 발행. 실패 시 자동 재연결 후 재시도."""
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
            if not mqtt_reconnect_with_backoff():
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
    return False

def send_status(value, is_fire):
    payload = {
        "sensor_id": MQTT_CLIENTID,
        "event": "mq7_detected",
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

    print("📍 MQ7 센서 모니터링 시작 (자동 복구 모드)")

    led_state = False
    t_led  = time.ticks_ms()
    t_last = time.ticks_ms()
    t_ping = time.ticks_ms()

    is_in_fire_wait = False
    t_fire = 0

    while True:
        now = time.ticks_ms()

        # LED 토글(동작 표시)
        if time.ticks_diff(now, t_led) >= LED_BLINK_MS:
            led_state = not led_state
            led.value(led_state)
            t_led = now

        # 주기적 헬스체크
        if time.ticks_diff(now, t_ping) >= PING_INTERVAL_MS:
            wifi_ensure()
            if not mqtt_ping():
                mqtt_reconnect_with_backoff()
            t_ping = now

        # 센서 읽기
        mq7_value = mq7_sensor.read_u16()

        # 임계 초과 → 1회 알림 후 15초 휴지
        if (not is_in_fire_wait) and mq7_value > THRESHOLD:
            send_status(mq7_value, True)
            t_fire = now
            is_in_fire_wait = True
            print("🔥 MQ7 감지 → 15초 대기 시작")

        # 휴지 종료 후 정상 1회 알림
        if is_in_fire_wait and time.ticks_diff(now, t_fire) > FIRE_HOLDOFF_MS:
            send_status(mq7_value, False)
            is_in_fire_wait = False
            print("🔄 정상 상태 복귀")

        # 평시 정상 보고
        if (not is_in_fire_wait) and time.ticks_diff(now, t_last) > NORMAL_REPORT_MS:
            send_status(mq7_value, False)
            t_last = now

        time.sleep(0.1)

# 자동 실행
main()

