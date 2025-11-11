# main.py — Raspberry Pi Pico W (MicroPython)
# Vibrator subscriber for ALL-TRUE fire alert
# Topics:
#   - vibrator/<DEVICE_ID>
#   - vibrator/broadcast   (optional)
#
# Commands:
#   - {"command":"vibrate_fire_alert","duration_ms":10000,"on_ms":400,"off_ms":200,"intensity":0.85}
#   - {"command":"vibrate_once","ms":800,"intensity":0.8}
#   - {"command":"vibrate_stop"}

import sys, time, ubinascii, machine

# --- 반드시 Pico W 전용 MicroPython이어야 합니다. (network 모듈 확인)
try:
    import network
except ImportError:
    raise OSError(
        "이 보드에는 'network' 모듈이 없습니다.\n"
        "Pico W 전용 MicroPython 펌웨어를 Thonny에서 설치하세요.\n"
        "도구 > 옵션 > 인터프리터 > MicroPython 설치/업데이트 > 장치: Raspberry Pi Pico W"
    )

from micropython import const
from machine import Pin, PWM

# MQTT 라이브러리: robust 우선, simple로 폴백
try:
    from umqtt.robust import MQTTClient
except Exception:
    try:
        from umqtt.simple import MQTTClient
    except Exception:
        raise OSError(
            "umqtt 라이브러리를 찾을 수 없습니다.\n"
            "REPL에서 설치하세요:\n"
            "  import mip; mip.install('umqtt.simple'); mip.install('umqtt.robust')\n"
            "또는 /umqtt/simple.py, /umqtt/robust.py 파일을 보드에 복사하세요."
        )

# ====== 설정 ======
WIFI_SSID     = "HealthcareConvergenceLab"
WIFI_PASSWORD = "Healthcare1234!"
BROKER        = "192.168.0.24"
PORT          = 1883
KEEPALIVE     = const(30)
DEVICE_ID     = "Vibrator_1"

CLIENT_ID = b"PICO_" + ubinascii.hexlify(machine.unique_id())
SUB_TOPICS = (
    b"vibrator/%s" % DEVICE_ID.encode(),
    b"vibrator/broadcast",
)

# ====== 핀 매핑 & PWM ======
PWM_FREQ = const(1000)  # 1 kHz

ENA = PWM(Pin(15))   # 모터 A
IN1 = Pin(14, Pin.OUT)
IN2 = Pin(13, Pin.OUT)

ENB = PWM(Pin(12))   # 모터 B
IN3 = Pin(11, Pin.OUT)
IN4 = Pin(10, Pin.OUT)

ENA.freq(PWM_FREQ)
ENB.freq(PWM_FREQ)

# ====== 모터 제어 ======
def set_motor_forward():
    IN1.value(1); IN2.value(0)
    IN3.value(1); IN4.value(0)

def set_power_ratio(ratio: float):
    # ratio: 0.0~1.0 → duty_u16: 0~65535
    if ratio <= 0.0:
        duty = 0
    elif ratio >= 1.0:
        duty = 65535
    else:
        duty = int(65535 * ratio)
    ENA.duty_u16(duty)
    ENB.duty_u16(duty)

def stop_all():
    set_power_ratio(0.0)
    IN1.value(0); IN2.value(0)
    IN3.value(0); IN4.value(0)

# ====== 진동 패턴 상태(FSM, 논블로킹) ======
_state = {
    "active": False,
    "end_ms": 0,
    "on_ms": 300,
    "off_ms": 300,
    "next_toggle_ms": 0,
    "vibrating": False,
    "intensity": 0.8,
}

def ticks_ms():
    return time.ticks_ms()

def start_fire_alert(duration_ms=10000, on_ms=400, off_ms=200, intensity=0.85):
    set_motor_forward()
    now = ticks_ms()
    _state.update({
        "active": True,
        "end_ms": time.ticks_add(now, int(duration_ms)),
        "on_ms": int(on_ms),
        "off_ms": int(off_ms),
        "next_toggle_ms": now,   # 즉시 토글 시도
        "vibrating": False,
        "intensity": max(0.0, min(1.0, float(intensity))),
    })

def stop_pattern():
    _state["active"] = False
    stop_all()

def pattern_tick():
    if not _state["active"]:
        return
    now = ticks_ms()
    # 종료 시각 도달
    if time.ticks_diff(_state["end_ms"], now) <= 0:
        stop_pattern()
        return
    # 토글 타이밍 도달?
    if time.ticks_diff(now, _state["next_toggle_ms"]) >= 0:
        if _state["vibrating"]:
            # turn OFF
            set_power_ratio(0.0)
            _state["vibrating"] = False
            _state["next_toggle_ms"] = time.ticks_add(now, _state["off_ms"])
        else:
            # turn ON
            set_motor_forward()
            set_power_ratio(_state["intensity"])
            _state["vibrating"] = True
            _state["next_toggle_ms"] = time.ticks_add(now, _state["on_ms"])

# ====== Wi-Fi / MQTT ======
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(100):  # 최대 ~10초
            if wlan.isconnected():
                break
            time.sleep(0.1)
    print("WiFi:", "OK" if wlan.isconnected() else "FAIL")
    if wlan.isconnected():
        try:
            print("ifconfig:", wlan.ifconfig())
        except Exception:
            pass
    return wlan.isconnected()

def on_msg(topic, msg):
    try:
        s = msg.decode() if isinstance(msg, (bytes, bytearray)) else str(msg)
        # ujson이 더 가볍지만, 표준 json도 충분. (보드에 ujson 내장됨)
        import ujson as json
        payload = json.loads(s) if s and s[0] in "{[" else {}
    except Exception as e:
        print("JSON parse error:", e, msg)
        return

    cmd = str(payload.get("command", "")).strip()

    if cmd == "vibrate_fire_alert":
        dur = int(payload.get("duration_ms", 10000))
        on  = int(payload.get("on_ms", 400))
        off = int(payload.get("off_ms", 200))
        inten = float(payload.get("intensity", 0.85))
        print("[VIB] fire_alert start:", dur, on, off, inten)
        start_fire_alert(dur, on, off, inten)

    elif cmd == "vibrate_once":
        ms = int(payload.get("ms", 800))
        inten = float(payload.get("intensity", 0.8))
        print("[VIB] once:", ms, inten)
        # 한 번 켰다가 자연 종료 (off 구간을 매우 길게)
        start_fire_alert(ms, ms, 9999999, inten)

    elif cmd == "vibrate_stop":
        print("[VIB] stop")
        stop_pattern()

    else:
        print("[VIB] unknown cmd:", cmd, payload)

def mqtt_connect_and_sub():
    c = MQTTClient(CLIENT_ID, BROKER, port=PORT, keepalive=KEEPALIVE)
    c.set_callback(on_msg)
    c.connect()
    for t in SUB_TOPICS:
        c.subscribe(t, qos=1)
        print("SUB:", t)
    return c

def main():
    wifi_connect()  # 실패해도 루프에서 재시도
    client = None
    last_ping = time.ticks_ms()

    while True:
        try:
            if client is None:
                client = mqtt_connect_and_sub()
                print("MQTT: connected.")

            # 수신 처리(논블로킹)
            client.check_msg()

            # 패턴 갱신
            pattern_tick()

            # ping 유지
            if time.ticks_diff(ticks_ms(), last_ping) > (KEEPALIVE * 500):  # KEEPALIVE/2 초
                try:
                    client.ping()
                except Exception:
                    pass
                last_ping = ticks_ms()

            time.sleep_ms(5)

        except Exception as e:
            print("MQTT loop err:", e)
            try:
                if client:
                    client.disconnect()
            except:
                pass
            client = None
            stop_pattern()
            time.sleep(2000)

try:
    main()
except KeyboardInterrupt:
    stop_all()

