# main.py (MicroPython on Raspberry Pi Pico W)
import time, network, machine, neopixel, ubinascii, ujson as json
from umqtt.simple import MQTTClient

# ===== Setup =====
DEVICE_ID     = "Neopixel_1"      # ← 보드별로 변경
NEO_PIN       = 28
NUM_LED       = 12
WIFI_SSID     = "HealthcareConvergenceLab"
WIFI_PASSWORD = "Healthcare1234!"
MQTT_BROKER   = "192.168.0.24"
MQTT_PORT     = 1883
KEEPALIVE     = 60

AUTO_RESET_ON_FATAL = False

# Topics
TOPIC_CMD_THIS = "neopixel/%s" % DEVICE_ID
TOPIC_CMD_ALL  = "neopixel/ALL"
TOPIC_STATUS   = "interfaceui/status/subscriber/%s" % DEVICE_ID
TOPIC_HELLO    = "interfaceui/registry/hello/%s" % DEVICE_ID
TOPIC_REQ      = "interfaceui/registry/request"
TOPIC_LOG      = "interfaceui/logs/subscriber/%s" % DEVICE_ID

# Defaults
DEFAULT_MOOD_RGB = (250, 248, 104)  # #FAF868
DEFAULT_BRIGHT   = 255

# Utils
def clamp(v, lo, hi): return lo if v < lo else (hi if v > hi else v)
def hex_to_rgb(s):
    s = s.strip().lstrip("#"); return (int(s[0:2],16), int(s[2:4],16), int(s[4:6],16))
def apply_brightness(rgb, br):
    r,g,b = rgb; return (r*br//255, g*br//255, b*br//255)

# Globals
np = neopixel.NeoPixel(machine.Pin(NEO_PIN), NUM_LED)
mood_rgb    = DEFAULT_MOOD_RGB
mood_bright = DEFAULT_BRIGHT
client = None

# 🔑 효과 프리엠션 토큰
_EFFECT_GEN = 0

def set_all(color):
    for i in range(NUM_LED):
        np[i] = color
    np.write()

# Log helper
def log(level, msg, **extra):
    try:
        if extra:
            print(f"[{DEVICE_ID}][{level}] {msg} {extra}")
        else:
            print(f"[{DEVICE_ID}][{level}] {msg}")
    except Exception:
        pass
    try:
        if client is None:
            return
        rec = {"id": DEVICE_ID, "type": "subscriber", "level": level,
               "msg": msg, "ts": int(time.time())}
        if extra: rec.update(extra)
        client.publish(TOPIC_LOG, json.dumps(rec))
    except Exception:
        pass

# Status / Hello
def publish_status(c, online=True):
    payload = json.dumps({
        "id": DEVICE_ID, "name": DEVICE_ID, "type": "subscriber",
        "status": "online" if online else "offline", "ts": int(time.time())
    })
    try:
        c.publish(TOPIC_STATUS, payload, retain=True)
        log("info", "status published", online=online)
    except Exception as e:
        print("⚠️ status publish 실패:", e)

def publish_hello(c):
    ip = network.WLAN(network.STA_IF).ifconfig()[0]
    payload = json.dumps({
        "id": DEVICE_ID, "ip": ip, "name": DEVICE_ID,
        "type": "subscriber", "ts": int(time.time())
    })
    try:
        c.publish(TOPIC_HELLO, payload, retain=True)
        print("📣 HELLO 보냄:", TOPIC_HELLO, "→", payload)
        log("info", "hello published", ip=ip)
    except Exception as e:
        print("⚠️ HELLO publish 실패:", e)

def make_client():
    cid = b"pico-" + ubinascii.hexlify(machine.unique_id())
    c = MQTTClient(cid, MQTT_BROKER, port=MQTT_PORT, keepalive=KEEPALIVE)
    will = json.dumps({"id": DEVICE_ID, "name": DEVICE_ID, "type":"subscriber",
                       "status":"offline", "ts": int(time.time())})
    c.set_last_will(TOPIC_STATUS, will, retain=True)
    return c

def connect_wifi():
    print("🔍 브로커 도달 확인 중...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(30):
            if wlan.isconnected(): break
            time.sleep(0.5)
    if wlan.isconnected():
        print("✅ Wi-Fi 연결 완료:", wlan.ifconfig())
        return True
    print("❌ Wi-Fi 연결 실패"); return False

NAMED = {
    "red":(255,0,0), "yellow":(255,255,0), "green":(0,255,0), "blue":(0,0,255),
    "purple":(128,0,128), "brown":(165,42,42), "white":(255,255,255), "black":(0,0,0)
}

# ── 토큰 기반 슬립 ───────────────────────────────────────────────────────
def _new_effect_token():
    global _EFFECT_GEN
    _EFFECT_GEN = (_EFFECT_GEN + 1) & 0x7fffffff
    return _EFFECT_GEN

def _is_current(token):
    return token == _EFFECT_GEN

def _sleep_with_token(c, seconds, token, poll=0.1):
    end = time.ticks_add(time.ticks_ms(), int(seconds*1000))
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        try: c.check_msg()
        except: pass
        if not _is_current(token):
            return False
        time.sleep(poll)
    return _is_current(token)

def handle_message(c, topic_b, msg_b):
    global mood_rgb, mood_bright
    topic = topic_b.decode() if isinstance(topic_b, bytes) else str(topic_b)
    raw   = msg_b.decode()   if isinstance(msg_b, bytes)   else str(msg_b)
    print("🔔 명령 수신:", topic, "→", raw)
    log("debug", "cmd recv", topic=topic, raw=raw[:120])

    try:
        data = json.loads(raw) if raw and raw[0] in "{[" else {"text": raw}
        cmd  = (data.get("command") or data.get("text","")).strip()
        sensor_id = data.get("sensor_id")

        if topic == TOPIC_REQ:
            publish_hello(c); return

        # ----- 리맵 규칙 (구버전/외부 발행 대비) -----
        # 1) yellow_flash → (가스=보라, 나머지=주황)
        if cmd in ("fire_warning", "yellow_flash"):
            if sensor_id == "gas_sensor_pico":
                cmd = "hex_flash"; data["color"] = data.get("color", "#8300FD")
                data["duration_ms"] = data.get("duration_ms", 5000)
            else:
                cmd = "hex_flash"; data["color"] = data.get("color", "#FD6A00")
                data["duration_ms"] = data.get("duration_ms", 5000)
        # 2) 수위/초인종 예전 명령 → 새 색으로
        if cmd == "purple_blink_3s" and sensor_id == "water_level_1":
            cmd = "hex_flash"; data["color"] = "#0045FD"; data["duration_ms"] = 5000  # ← 3000→5000

        if cmd == "brown_blink_3s" and sensor_id == "doorbell_1":
            cmd = "hex_flash"; data["color"] = "#00FD05"; data["duration_ms"] = 5000  # ← 3000→5000
        # -----------------------------------------

        if cmd == "set_mood":
            token = _new_effect_token()
            hex_color  = (data.get("color") or "#FFFFFF").strip()
            brightness = clamp(int(data.get("brightness", DEFAULT_BRIGHT)), 0, 255)
            mood_rgb, mood_bright = hex_to_rgb(hex_color), brightness
            color = apply_brightness(mood_rgb, mood_bright)
            set_all(color)
            print("🌈 mood 적용:", hex_color, "brightness=", brightness, "→", color)
            log("info", "mood applied", color=hex_color, brightness=brightness, rgb=color)
            return

        if cmd == "hex_flash":
            token = _new_effect_token()
            hex_color = (data.get("color") or "#FFFFFF").strip()
            try:
                rgb = hex_to_rgb(hex_color)
            except Exception:
                rgb = (255,255,255)
            duration_ms = int(data.get("duration_ms", 5000))
            flash = apply_brightness(rgb, mood_bright)
            base  = apply_brightness(mood_rgb, mood_bright)
            set_all(flash)
            log("info", "hex flash start", color=hex_color, duration_ms=duration_ms, rgb=flash)
            if _sleep_with_token(c, duration_ms/1000.0, token):
                set_all(base)
                log("info", "hex flash end", restored_rgb=base)
            return

        if cmd.endswith("_blink_3s"):
            # (호환) 여전히 들어오면 기존 색으로 3초 깜박
            token = _new_effect_token()
            name = cmd.replace("_blink_3s","")
            blink = apply_brightness(NAMED.get(name, NAMED["white"]), mood_bright)
            base  = apply_brightness(mood_rgb, mood_bright)
            log("info", "color blink 3s start", color=name)
            end = time.ticks_add(time.ticks_ms(), 3_000)
            on = False
            while time.ticks_diff(end, time.ticks_ms()) > 0 and _is_current(token):
                set_all(blink if on else base)
                on = not on
                _sleep_with_token(c, 0.25, token)
            if _is_current(token):
                set_all(base)
                log("info", "color blink 3s end", color=name)
            return

        if cmd in ("fire_confirmed", "red_blink"):
            token = _new_effect_token()
            red  = apply_brightness(NAMED["red"],  mood_bright)
            base = apply_brightness(mood_rgb,      mood_bright)
            end  = time.ticks_add(time.ticks_ms(), 10_000)
            log("info", "red blink start", seconds=10)
            on = False
            while time.ticks_diff(end, time.ticks_ms()) > 0 and _is_current(token):
                set_all(red if on else base)
                on = not on
                _sleep_with_token(c, 0.25, token)
            if _is_current(token):
                set_all(base)
                log("info", "red blink end")
            return

        if cmd in ("off","black"):
            token = _new_effect_token()
            set_all((0,0,0)); log("info", "off")
            return

        print("⚠️ 알 수 없는 명령:", cmd)
        log("warn", "unknown cmd", cmd=cmd)

    except Exception as e:
        print("❌ 처리 오류:", e)
        log("error", "handle_message error", error=str(e))

def mqtt_connect_and_subscribe():
    global client
    if not connect_wifi(): return False
    print("📡 MQTT 연결 시도 중...")
    client = make_client()
    try:
        client.set_callback(lambda t,m: handle_message(client, t, m))
        client.connect()
        publish_status(client, True)
        publish_hello(client)
        client.subscribe(TOPIC_CMD_THIS, qos=1);  print("📶 구독:", TOPIC_CMD_THIS)
        client.subscribe(TOPIC_CMD_ALL,  qos=1);  print("📶 구독:", TOPIC_CMD_ALL)
        client.subscribe(TOPIC_REQ,      qos=1);  print("📶 구독:", TOPIC_REQ)
        print("✅ MQTT 연결 완료")
        log("info", "mqtt connected")
        return True
    except Exception as e:
        print("❌ MQTT 연결 실패:", repr(e))
        try: client.disconnect()
        except: pass
        client = None
        return False

def main():
    set_all(apply_brightness(mood_rgb, mood_bright))
    while not mqtt_connect_and_subscribe():
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
            print("❌ MQTT 오류:", e)
            log("error", "mqtt loop error", error=str(e))
            ok = False
            for _ in range(10):
                if mqtt_connect_and_subscribe():
                    ok = True; break
                time.sleep(3)
            if not ok and AUTO_RESET_ON_FATAL:
                print("♻️ 재연결 실패 → 보드 리셋")
                time.sleep(1); machine.reset()

main()

