# main.py (MicroPython, Pico W + L298N, MQTT 제어)
import network, time, ubinascii, ujson as json
from machine import Pin
from umqtt.simple import MQTTClient

# ==== 하드웨어 핀 (L298N 예시) ====
ENA = Pin(15, Pin.OUT, value=0)  # ENA PWM대신 ON/OFF
IN1 = Pin(16, Pin.OUT, value=0)
IN2 = Pin(17, Pin.OUT, value=0)

def beacon_on():
    IN1.value(0); IN2.value(0)  # 저측 싱크
    ENA.value(1)

def beacon_off():
    ENA.value(0)

# ==== 설정 ====
DEVICE_ID   = "Beacon_1"
WIFI_SSID   = "HealthcareConvergenceLab"
WIFI_PW     = "Healthcare1234!"
BROKER      = "192.168.0.24"
PORT        = 1883
KEEPALIVE   = 60

TOPIC_CMD   = "beacon/%s" % DEVICE_ID
TOPIC_STATUS= "interfaceui/status/subscriber/%s" % DEVICE_ID
TOPIC_HELLO = "interfaceui/registry/hello/%s" % DEVICE_ID
TOPIC_REQ   = "interfaceui/registry/request"
TOPIC_LOG   = "interfaceui/logs/subscriber/%s" % DEVICE_ID

client = None
_EFFECT_GEN = 0  # 프리엠션 토큰

def log(level, msg, **extra):
    try:
        print("[{}][{}] {} {}".format(DEVICE_ID, level, msg, extra if extra else ""))
    except: pass
    try:
        if client:
            rec = {"id": DEVICE_ID, "type": "subscriber", "level": level, "msg": msg, "ts": int(time.time())}
            if extra: rec.update(extra)
            client.publish(TOPIC_LOG, json.dumps(rec))
    except: pass

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PW)
        for _ in range(40):
            if wlan.isconnected(): break
            time.sleep(0.25)
    return wlan.isconnected()

def publish_status(c, online=True):
    try:
        c.publish(TOPIC_STATUS, json.dumps({
            "id": DEVICE_ID, "name": DEVICE_ID, "type": "subscriber",
            "status": "online" if online else "offline", "ts": int(time.time())
        }), retain=True)
    except Exception as e:
        print("status publish err:", e)

def publish_hello(c):
    try:
        ip = network.WLAN(network.STA_IF).ifconfig()[0]
        c.publish(TOPIC_HELLO, json.dumps({
            "id": DEVICE_ID, "ip": ip, "name": DEVICE_ID,
            "type": "subscriber", "ts": int(time.time())
        }), retain=True)
        log("info", "hello published", ip=ip)
    except Exception as e:
        print("hello publish err:", e)

def make_client():
    cid = b"beacon-" + ubinascii.hexlify(network.WLAN().config('mac'))
    c = MQTTClient(cid, BROKER, port=PORT, keepalive=KEEPALIVE)
    c.set_last_will(TOPIC_STATUS, json.dumps({
        "id": DEVICE_ID, "name": DEVICE_ID, "type":"subscriber",
        "status":"offline", "ts": int(time.time())
    }), retain=True)
    return c

def _new_token():
    global _EFFECT_GEN
    _EFFECT_GEN = (_EFFECT_GEN + 1) & 0x7fffffff
    return _EFFECT_GEN

def _is_current(token): return token == _EFFECT_GEN

def _sleep_with_token(c, ms, token):
    end = time.ticks_add(time.ticks_ms(), ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        try: c.check_msg()
        except: pass
        if not _is_current(token):
            return False
        time.sleep(0.05)
    return _is_current(token)

def handle_message(c, t_b, m_b):
    topic = t_b.decode() if isinstance(t_b, bytes) else str(t_b)
    raw   = m_b.decode() if isinstance(m_b, bytes) else str(m_b)
    print("🔔", topic, "→", raw)
    try:
        data = json.loads(raw) if raw and raw[0] in "{[" else {"text": raw}
        cmd  = (data.get("command") or data.get("text","")).strip()
    except Exception:
        log("error", "json parse failed", raw=raw); return

    if topic == TOPIC_REQ:
        publish_hello(c); return

    if cmd == "beacon_fire_alert":
        token = _new_token()
        duration_ms = int(data.get("duration_ms", 10000))
        on_ms  = int(data.get("on_ms", 250))
        off_ms = int(data.get("off_ms", 250))
        log("info", "beacon fire alert start", duration_ms=duration_ms, on_ms=on_ms, off_ms=off_ms)
        end = time.ticks_add(time.ticks_ms(), duration_ms)
        state = False
        while time.ticks_diff(end, time.ticks_ms()) > 0 and _is_current(token):
            state = not state
            if state: beacon_on()
            else:     beacon_off()
            if not _sleep_with_token(c, on_ms if state else off_ms, token):
                break
        if _is_current(token):
            beacon_off()
            log("info", "beacon fire alert end")
        return

    if cmd == "beacon_stop":
        _new_token()    # 다음 루프 즉시 종료
        beacon_off()
        log("info", "beacon stop")
        return

    log("warn", "unknown cmd", cmd=cmd)

def mqtt_connect():
    global client
    if not connect_wifi():
        print("❌ Wi-Fi 연결 실패"); return False
    client = make_client()
    try:
        client.set_callback(lambda t,m: handle_message(client, t, m))
        client.connect()
        publish_status(client, True)
        publish_hello(client)
        client.subscribe(TOPIC_CMD, qos=1); print("📶 구독:", TOPIC_CMD)
        client.subscribe(TOPIC_REQ, qos=1); print("📶 구독:", TOPIC_REQ)
        log("info", "mqtt connected")
        return True
    except Exception as e:
        print("MQTT connect err:", e)
        try: client.disconnect()
        except: pass
        client = None
        return False

def main():
    beacon_off()  # 안전 소등
    while not mqtt_connect():
        time.sleep(3)

    last_ping, last_hello = time.time(), time.time()
    while True:
        try:
            client.check_msg()
            now = time.time()
            if now - last_ping >= KEEPALIVE//2:
                try: client.ping()
                except Exception as e: raise e
                last_ping = now
            if now - last_hello >= 60:
                publish_hello(client); last_hello = now
            time.sleep(0.05)
        except Exception as e:
            print("loop err:", e)
            time.sleep(1)
            if not mqtt_connect():
                time.sleep(3)

main()

