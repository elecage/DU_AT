# MQTT_decision_server.py
import json, time, socket, datetime
from collections import defaultdict, deque
import paho.mqtt.client as mqtt

from handler_registry import HANDLER_NAME_MAP
import handlers
_ = handlers.__name__  # ensure handlers loaded

from firebase.firebase_utils import save_fcm_token

# ── Broker / Topics ──────────────────────────────────────────────────────
BROKER_IP   = "192.168.0.24"
BROKER_PORT = 1883
KEEPALIVE   = 30

CONTROL_TOPIC   = "decision/control"
APP_NEOPIXEL    = "interfaceui/commands/mood"
STATUS_SERVER   = "interfaceui/status/server"
REG_REQUEST     = "interfaceui/registry/request"
HELLO_SERVER    = "interfaceui/registry/hello/server"
PUSH_REGISTER   = "interfaceui/push/register"

# logs
LOG_STREAM_PREFIX  = "interfaceui/logs"
LOG_HISTORY_REQ    = "interfaceui/logs/request"
LOG_HISTORY_PREFIX = "interfaceui/logs/history"

# Devices
VIBRATOR_TOPIC_PREFIX = "vibrator"   # vibrator/Vibrator_1
BEACON_TOPIC_PREFIX   = "beacon"     # ✅ beacon/Beacon_1  (신규)

VERBOSE_PUBLISH_LOG = False

# ── Load sensor mapping ──────────────────────────────────────────────────
with open("MQTT_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)
MQTT_TOPICS = list(config.keys())

# ALL-TRUE participants
MQTT_event_status = {
    cfg["sensor_id"]: False
    for cfg in config.values()
    if cfg.get("participates_in_alltrue", True)
}

# ── Runtime context ──────────────────────────────────────────────────────
def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""

userdata = {
    "devices": ["Neopixel_1", "Neopixel_2"],
    "vib_devices": ["Vibrator_1"],
    "beacon_devices": ["Beacon_1"],     # ✅ 경광등 장치 목록
    "default_command": "fire_confirmed",
    "sensor_status": MQTT_event_status,
    "just_triggered": False,
    "server_ip": _get_local_ip(),
}

# ── Time helpers ─────────────────────────────────────────────────────────
def _now_ts_ms() -> int:
    return int(time.time() * 1000)

def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

# ── Status / Hello ───────────────────────────────────────────────────────
def _status_payload(online: bool) -> str:
    return json.dumps({
        "id": "server", "name": "중앙 관리 서버",
        "type": "server", "status": "online" if online else "offline",
        "ts": int(time.time()),
        "ts_ms": _now_ts_ms(),
        "iso": _now_iso(),
    })

def _hello_payload() -> str:
    return json.dumps({
        "id": "server", "name": "중앙 관리 서버", "type": "server",
        "ip": userdata.get("server_ip", ""),
        "ts": int(time.time()),
        "ts_ms": _now_ts_ms(),
        "iso": _now_iso(),
    })

def publish_server_status(client, online: bool):
    print(f"📣 STATUS publish → {STATUS_SERVER} : online={online}")
    client.publish(STATUS_SERVER, _status_payload(online), qos=1, retain=True)

def publish_server_hello(client):
    payload = _hello_payload()
    print(f"📣 HELLO publish → {HELLO_SERVER} : {payload}")
    client.publish(HELLO_SERVER, payload, qos=1, retain=True)

# ── Log stream ───────────────────────────────────────────────────────────
_RING_MAX = 2000
ring = defaultdict(lambda: deque(maxlen=_RING_MAX))

def _log_key(typ: str, id_: str) -> str:
    return f"{typ}|{id_}"

def log_publish(client, *, typ: str, id_: str, level: str, msg: str, **extra):
    rec = {
        "id": id_, "type": typ, "level": level, "msg": msg,
        "ts": int(time.time()), "ts_ms": _now_ts_ms(), "iso": _now_iso(),
    }
    if extra: rec.update(extra)
    topic = f"{LOG_STREAM_PREFIX}/{typ}/{id_}"
    client.publish(topic, json.dumps(rec), qos=0, retain=False)
    ring[_log_key(typ, id_)].append(rec)
    try:
        extra_view = {k: v for k, v in rec.items()
                      if k not in ("type","id","level","msg","ts","ts_ms","iso")}
        print(f"[srvlog][{level}] {msg} → {topic} {json.dumps(extra_view)}")
    except Exception:
        pass

# ── Vibrator publishers ──────────────────────────────────────────────────
def publish_vibrate_fire_alert(client, context, *, duration_ms=10000, on_ms=400, off_ms=200, intensity=0.85):
    vib_list = context.get("vib_devices") or ["Vibrator_1"]
    payload = {
        "command": "vibrate_fire_alert",
        "pattern": "fire_alert",
        "duration_ms": int(duration_ms),
        "on_ms": int(on_ms),
        "off_ms": int(off_ms),
        "intensity": float(intensity),
        "alert": True,
        "sensor_id": "all_true",
        "issuer": "decision_server",
    }
    for dev in vib_list:
        topic = f"{VIBRATOR_TOPIC_PREFIX}/{dev}"
        client.publish(topic, json.dumps(payload), qos=1, retain=False)
        log_publish(client, typ="server", id_="server", level="info",
                    msg="vibrator command sent", target=dev, payload=payload)

def publish_vibrate_stop(client, context):
    vib_list = context.get("vib_devices") or ["Vibrator_1"]
    payload = {"command": "vibrate_stop", "issuer": "decision_server"}
    for dev in vib_list:
        topic = f"{VIBRATOR_TOPIC_PREFIX}/{dev}"
        client.publish(topic, json.dumps(payload), qos=1, retain=False)
        log_publish(client, typ="server", id_="server", level="debug",
                    msg="vibrator stop sent", target=dev)

# ── Beacon publishers (신규) ─────────────────────────────────────────────
def publish_beacon_fire_alert(client, context, *, duration_ms=10000, on_ms=250, off_ms=250):
    """ALL-TRUE 시 경광등 점멸 10초"""
    beacons = context.get("beacon_devices") or ["Beacon_1"]
    payload = {
        "command": "beacon_fire_alert",
        "duration_ms": int(duration_ms),
        "on_ms": int(on_ms),
        "off_ms": int(off_ms),
        "alert": True,
        "sensor_id": "all_true",
        "issuer": "decision_server",
    }
    for dev in beacons:
        topic = f"{BEACON_TOPIC_PREFIX}/{dev}"
        client.publish(topic, json.dumps(payload), qos=1, retain=False)
        log_publish(client, typ="server", id_="server", level="info",
                    msg="beacon command sent", target=dev, payload=payload)

def publish_beacon_stop(client, context):
    beacons = context.get("beacon_devices") or ["Beacon_1"]
    payload = {"command": "beacon_stop", "issuer": "decision_server"}
    for dev in beacons:
        topic = f"{BEACON_TOPIC_PREFIX}/{dev}"
        client.publish(topic, json.dumps(payload), qos=1, retain=False)
        log_publish(client, typ="server", id_="server", level="debug",
                    msg="beacon stop sent", target=dev)

# ── ALL-TRUE broadcaster ─────────────────────────────────────────────────
def all_True_publisher(client, context):
    print(f"🧪 sensor_status: {context['sensor_status']}")
    if context["sensor_status"] and all(context["sensor_status"].values()):
        print("🚨 ALL-TRUE detected")
        log_publish(client, typ="server", id_="server", level="info",
                    msg="ALL-TRUE detected → red_blink 10s + vibrator 10s + beacon 10s",
                    sensor_status=dict(context["sensor_status"]))
        # 1) 네오픽셀: red_blink 10s
        cmd = {"command": context["default_command"], "sensor_id": "all_true", "alert": True, "issuer": "decision_server"}
        for dev in (context.get("devices") or ["Neopixel_1"]):
            client.publish(f"neopixel/{dev}", json.dumps(cmd), qos=1, retain=False)
            log_publish(client, typ="server", id_="server", level="debug",
                        msg="command sent to device", target=dev, command=context["default_command"])
        # 2) 진동 디바이스 10s
        publish_vibrate_fire_alert(client, context, duration_ms=10000, on_ms=400, off_ms=200, intensity=0.85)
        # 3) ✅ 경광등 10s 점멸
        publish_beacon_fire_alert(client, context, duration_ms=10000, on_ms=250, off_ms=250)

        # reset snapshot
        for k in context["sensor_status"]:
            context["sensor_status"][k] = False
        context["just_triggered"] = False
        log_publish(client, typ="server", id_="server", level="debug",
                    msg="ALL-TRUE flags reset", sensor_status=dict(context["sensor_status"]))

# ── App → Neopixel forwarding ────────────────────────────────────────────
def forward_mood_to_neopixel(client, raw: dict, context):
    try:
        if raw.get("command") != "set_mood":
            print("❌ not set_mood:", raw); return
        hex_color = str(raw.get("color", "#FFFFFF")).strip().upper()
        if not (hex_color.startswith("#") and len(hex_color) == 7):
            print("❌ bad color:", hex_color); return
        brightness = int(raw.get("brightness", 255))
        if not (0 <= brightness <= 255):
            print("❌ bad brightness:", brightness); return

        target = raw.get("target")
        targets = [target] if target else (context.get("devices") or ["Neopixel_1"])
        payload = {"command": "set_mood", "color": hex_color, "brightness": brightness, "issuer": "decision_server"}
        for dev in targets:
            topic = f"neopixel/{dev}"
            client.publish(topic, json.dumps(payload), qos=1, retain=False)
            print(f"📤 set_mood → {topic} : {payload}")
            log_publish(client, typ="server", id_="server",
                        level="info", msg="forward set_mood",
                        target=dev, color=hex_color, brightness=brightness)
    except Exception as e:
        print("❌ forward error:", e)
        log_publish(client, typ="server", id_="server",
                    level="error", msg="mood forward error", error=str(e))

# ── History handler (생략 없이 유지) ─────────────────────────────────────
def handle_history_request(client, payload: dict):
    req_id   = str(payload.get("id") or "server")
    req_type = str(payload.get("type") or ("server" if req_id == "server" else "subscriber"))
    limit    = int(payload.get("limit", 200))
    limit    = 50 if limit < 1 else (1000 if limit > 1000 else limit)
    before   = payload.get("before_ts")

    buf = list(ring.get(_log_key(req_type, req_id), []))
    if before:
        buf = [r for r in buf if r.get("ts", 0) < int(before)]
    items = buf[-limit:]

    resp_topic = f"{LOG_HISTORY_PREFIX}/{req_type}/{req_id}"
    print(f"📤 history resp → {resp_topic} ({len(items)} items)")
    client.publish(resp_topic, json.dumps({"id": req_id, "type": req_type, "items": items}), qos=0, retain=False)

    log_publish(client, typ="server", id_="server", level="debug",
                msg="history served", target=req_id, target_type=req_type,
                count=len(items), before_ts=before, limit=limit)

# ── Callbacks ────────────────────────────────────────────────────────────
def on_message(client, context, msg):
    try:
        topic = msg.topic
        raw = msg.payload.decode(errors="ignore") if msg.payload else ""
        payload = json.loads(raw) if raw and raw[0] in "{[" else {"text": raw}

        if not topic.startswith(f"{LOG_STREAM_PREFIX}/") and not topic.startswith(f"{LOG_HISTORY_PREFIX}/"):
            preview = raw if isinstance(raw, str) else str(payload)
            log_publish(client, typ="server", id_="server", level="debug",
                        msg="recv", topic=topic, payload=(preview[:200] if preview else ""))

        if topic.startswith(f"{LOG_STREAM_PREFIX}/"):
            parts = topic.split("/", 4)
            if len(parts) >= 4:
                typ, id_ = parts[2], parts[3]
                rec = payload if isinstance(payload, dict) else {"msg": payload}
                rec.setdefault("id", id_); rec.setdefault("type", typ)
                rec.setdefault("level", "info")
                rec.setdefault("ts", int(time.time()))
                rec.setdefault("ts_ms", _now_ts_ms())
                rec.setdefault("iso", _now_iso())
                ring[_log_key(typ, id_)].append(rec)
            return

        if topic == APP_NEOPIXEL:
            forward_mood_to_neopixel(client, payload, context); return

        if topic == REG_REQUEST:
            publish_server_hello(client)
            log_publish(client, typ="server", id_="server", level="debug", msg="hello re-published"); return

        if topic == LOG_HISTORY_REQ:
            handle_history_request(client, payload); return

        if topic == PUSH_REGISTER:
            token = None
            try:
                raw_s = msg.payload.decode() if msg.payload else ""
                if raw_s.strip().startswith("{"):
                    token = json.loads(raw_s).get("token")
                else:
                    token = raw_s.strip()
            except Exception:
                token = None
            if token:
                try:
                    save_fcm_token(token)
                    tail = token[-10:] if len(token) > 10 else token
                    log_publish(client, typ="server", id_="server", level="info",
                                msg="push token registered", token_tail=tail)
                except Exception as e:
                    print("❌ save_fcm_token error:", e)
                    log_publish(client, typ="server", id_="server", level="error",
                                msg="push token save failed", error=str(e))
            else:
                log_publish(client, typ="server", id_="server", level="warn",
                            msg="push token missing/invalid")
            return

        if topic == CONTROL_TOPIC and payload.get("command") == "reset_all":
            print("🧹 reset sensor_status")
            for k in context["sensor_status"]:
                context["sensor_status"][k] = False
            context["just_triggered"] = False
            publish_vibrate_stop(client, context)
            publish_beacon_stop(client, context)   # ✅ 리셋 시 경광등도 강제 OFF
            log_publish(client, typ="server", id_="server", level="info", msg="sensor_status reset")
            return

        cfg = config.get(topic)
        if not cfg:
            print("❗unregistered topic:", topic)
            log_publish(client, typ="server", id_="server", level="warn", msg="unregistered topic", topic=topic)
            return

        if payload.get("sensor_id") != cfg["sensor_id"] or payload.get("event") != cfg["expected_event"]:
            print("❌ unexpected sensor payload:", payload)
            log_publish(client, typ="server", id_="server", level="debug",
                        msg="unexpected sensor event",
                        got=payload, expect={"sensor_id":cfg["sensor_id"], "event":cfg["expected_event"]})
            return

        log_publish(client, typ="server", id_="server", level="info",
                    msg="sensor event accepted",
                    topic=topic, sensor_id=payload.get("sensor_id"),
                    handler=cfg.get("handler"))

        handler = HANDLER_NAME_MAP.get(cfg["handler"])
        if not handler:
            print("❗no handler:", cfg["handler"])
            log_publish(client, typ="server", id_="server", level="error", msg="missing handler", handler=cfg["handler"])
            return

        handler(payload, client, context)

        if cfg.get("participates_in_alltrue", True):
            log_publish(client, typ="server", id_="server", level="debug",
                        msg="participates_in_alltrue",
                        sensor_status=dict(context["sensor_status"]))
            all_True_publisher(client, context)

    except Exception as e:
        print("❌ on_message exception:", e)
        log_publish(client, typ="server", id_="server", level="error",
                    msg="exception in on_message", error=str(e))

def on_connect(client, context, flags, rc, _=None):
    print("✅ MQTT connected (rc=", rc, ")")
    publish_server_status(client, True)
    publish_server_hello(client)
    log_publish(client, typ="server", id_="server", level="info",
                msg="server connected", ip=userdata.get("server_ip",""))

    for t in MQTT_TOPICS:
        client.subscribe(t, qos=1); print(f"📶 구독: {t}")

    client.subscribe(CONTROL_TOPIC,   qos=1); print(f"📶 구독: {CONTROL_TOPIC}")
    client.subscribe(APP_NEOPIXEL,    qos=1); print(f"📶 구독: {APP_NEOPIXEL}")
    client.subscribe(REG_REQUEST,     qos=1); print(f"📶 구독: {REG_REQUEST}")
    client.subscribe(LOG_HISTORY_REQ, qos=1); print(f"📶 구독: {LOG_HISTORY_REQ}")
    client.subscribe(PUSH_REGISTER,   qos=1); print(f"📶 구독: {PUSH_REGISTER}")
    client.subscribe(f"{LOG_STREAM_PREFIX}/+/+", qos=0); print(f"📶 구독: {LOG_STREAM_PREFIX}/+/+")

    print("✅ MQTT 연결 완료")
    log_publish(client, typ="server", id_="server", level="debug",
                msg="subscriptions ready", sensor_topics=len(MQTT_TOPICS))

    orig_publish = client.publish
    def _pub_wrap(topic, payload=None, qos=0, retain=False):
        try:
            t = str(topic)
            if VERBOSE_PUBLISH_LOG \
               and not t.startswith(LOG_STREAM_PREFIX) \
               and not t.startswith(LOG_HISTORY_PREFIX) \
               and t not in (STATUS_SERVER, HELLO_SERVER):
                preview = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
                log_publish(client, typ="server", id_="server", level="debug",
                            msg="publish", topic=t, qos=qos, retain=retain,
                            payload=(preview[:200] if preview else ""))
        except Exception:
            pass
        return orig_publish(topic, payload, qos, retain)
    client.publish = _pub_wrap

def loop():
    while True:
        try:
            client = mqtt.Client(client_id="decision_server", userdata=userdata)
            client.will_set(STATUS_SERVER, _status_payload(False), qos=1, retain=True)
            client.on_connect = lambda c, u, f, rc: on_connect(c, u, f, rc)
            client.on_message = lambda c, u, m: on_message(c, u, m)

            print("📡 MQTT 서버 연결 시도…")
            client.connect(BROKER_IP, BROKER_PORT, keepalive=KEEPALIVE)
            print("🚀 판단 서버 실행 중")

            last_hello   = time.time()
            last_hb_log  = time.time()

            while True:
                client.loop(timeout=1.0)
                now = time.time()
                if now - last_hello >= 60:
                    publish_server_hello(client); last_hello = now
                if now - last_hb_log >= 60:
                    log_publish(client, typ="server", id_="server", level="debug", msg="heartbeat")
                    last_hb_log = now

        except Exception as e:
            print(f"❌ MQTT loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    loop()
