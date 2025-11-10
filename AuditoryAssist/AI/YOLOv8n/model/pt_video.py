#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ─────────────────────────────────────────────────────────────────────────────
#  UI 백엔드: Wayland/Xorg 지정 (반드시 cv2 임포트 "전에")
# ─────────────────────────────────────────────────────────────────────────────
import os
os.environ["QT_QPA_PLATFORM"] = "xcb"  # Xorg 사용 시
# os.environ["QT_QPA_PLATFORM"] = "wayland"  # GNOME Wayland에서 창 출력할 때

import argparse
import threading
import time
import json
import sys
import socket
import numpy as np
import paho.mqtt.client as mqtt
import cv2

# 🔁 PyTorch/Ultralytics (Hailo 제거)
import torch
from ultralytics import YOLO

# ─────────────────────────────────────────────────────────────────────────────
#  MJPEG 서버 (Flask) - 비디오(/video)만 송출
# ─────────────────────────────────────────────────────────────────────────────
from flask import Flask, Response
app = Flask(__name__)
_latest_jpeg = None
_frame_lock = threading.Lock()

def _encode_jpeg(frame, quality=80):
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    return buf.tobytes() if ok else None

def update_stream_frame(frame, quality=80):
    """
    매 프레임마다 호출해서 /video 스트림에 뿌릴 최신 JPEG를 갱신
    """
    global _latest_jpeg
    jpg = _encode_jpeg(frame, quality=quality)
    if jpg is None:
        return
    with _frame_lock:
        _latest_jpeg = jpg

@app.route("/video")
def video_mjpeg():
    """
    multipart/x-mixed-replace 로 계속 JPEG를 흘려보내는 MJPEG 엔드포인트
    """
    def gen():
        boundary = b"--frame\r\n"
        while True:
            with _frame_lock:
                jpg = _latest_jpeg
            if jpg is None:
                time.sleep(0.02)
                continue

            # frame boundary
            yield boundary
            # headers
            yield b"Content-Type: image/jpeg\r\n"
            yield b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
            # data
            yield jpg
            yield b"\r\n"

            # 약 33 FPS 정도
            time.sleep(0.03)

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

def start_mjpeg_server(host="0.0.0.0", port=5055):
    """
    Flask 앱을 백그라운드 스레드로 띄워서 /video 스트림 제공
    """
    th = threading.Thread(
        target=lambda: app.run(host=host, port=port, threaded=True, use_reloader=False),
        daemon=True
    )
    th.start()
    print(f"🌐 MJPEG 서버 시작: http://{host}:{port}/video")

# ────────────────────────────────
# ✅ MQTT 설정
# ────────────────────────────────
MQTT_BROKER = "192.168.0.24"
MQTT_PORT = 1883
TEMP_TOPIC = "system/temperature/pi5"
TEMP_CLIENT_ID = f"raspi_temp_{socket.gethostname()}"

MQTT_CONFIG = {
    "smoke": {
        "topic": "AI_smoke_alert",
        "payload": {"sensor_id": "AI_D_smoke", "event": "smoke_detected"}
    },
    "fire":  {
        "topic": "AI_fire_alert",
        "payload": {"sensor_id": "AI_D_fire",  "event": "fire_detected"}
    }
}

last_sent = {"fire": 0, "smoke": 0}
COOLDOWN = 10  # 초 단위로 쿨다운

# ────────────────────────────────
# MQTT 유틸 함수
# ────────────────────────────────
def try_connect(client_id=None):
    """
    MQTT 브로커에 연결 시도하고 client 객체(또는 None) 반환
    """
    try:
        client = mqtt.Client(
            client_id=client_id or f"ai_fire_{socket.gethostname()}_{os.getpid()}",
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=15)
        client.loop_start()
        print(f"✅ MQTT 연결 성공 (client_id={client._client_id.decode()})")
        return client
    except Exception as e:
        print(f"⚠️ MQTT 연결 실패 (client_id={client_id}):", e)
        return None

def get_cpu_temp():
    """
    라즈베리파이 CPU 온도 읽기 (°C)
    """
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read()) / 1000.0
    except FileNotFoundError:
        print("⚠️ CPU 온도 센서를 찾을 수 없습니다. 기본값 0 사용.")
        return 0.0

def cpu_temp_publisher():
    """
    주기적으로 TEMP_TOPIC 에 현재 CPU 온도 publish
    """
    client = try_connect(TEMP_CLIENT_ID)
    reconnect_timer = time.time()

    while True:
        try:
            # 연결 끊겼으면 10초마다 재시도
            if client is None and time.time() - reconnect_timer > 10:
                client = try_connect(TEMP_CLIENT_ID)
                reconnect_timer = time.time()

            payload = {
                "sensor_id": "raspi_temp_pi5",
                "value": get_cpu_temp(),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            if client:
                try:
                    client.publish(TEMP_TOPIC, json.dumps(payload), qos=0)
                    print("📤 온도 전송:", payload)
                except Exception as e:
                    print("⚠️ 온도 MQTT 전송 실패:", e)
                    client = None
            else:
                print("⚠️ 온도 MQTT 미연결 상태, 데이터 전송 생략")

            time.sleep(5)

        except Exception as e:
            print("⚠️ CPU 온도 루프 오류:", e)
            time.sleep(5)

def send_detection_mqtt(client, cls_name):
    """
    fire/smoke 감지 시 MQTT 알림 보내기 (쿨다운 있음)
    """
    global last_sent
    if client is None:
        return None

    now = time.time()
    if now - last_sent.get(cls_name, 0) < COOLDOWN:
        print(f"⏳ {cls_name} MQTT 쿨다운 중, 전송 생략")
        return client

    cfg = MQTT_CONFIG.get(cls_name)
    if cfg:
        try:
            client.publish(cfg["topic"], json.dumps(cfg["payload"]), qos=0)
            last_sent[cls_name] = now
            print(f"📤 MQTT 전송 → topic: {cfg['topic']}, payload: {cfg['payload']}")
        except Exception as e:
            print(f"⚠️ MQTT 전송 실패 ({cls_name}):", e)
            return None
    else:
        print(f"⚠️ 알 수 없는 클래스 '{cls_name}'")
    return client

# ────────────────────────────────
# 카메라 열기 유틸
# ────────────────────────────────
def open_capture(args):
    """
    카메라 캡처 오브젝트 열기
    args.api: auto|v4l2|gst
    args.fourcc: MJPG|YUYV|NONE
    args.cap_width/height: 원하는 해상도
    """
    if args.api == "gst":
        # GStreamer 파이프라인 직접 구성
        dev = f"/dev/video{args.camera_index}"
        w, h = args.cap_width, args.cap_height
        if args.fourcc == "MJPG":
            pipe = (
                f"v4l2src device={dev} ! image/jpeg,framerate=30/1,width={w},height={h} "
                f"! jpegdec ! videoconvert ! video/x-raw,format=BGR ! appsink drop=true"
            )
        elif args.fourcc == "YUYV":
            pipe = (
                f"v4l2src device={dev} io-mode=2 ! video/x-raw,format=YUY2,framerate=30/1,width={w},height={h} "
                f"! videoconvert ! video/x-raw,format=BGR ! appsink drop=true"
            )
        else:  # NONE → 자동 포맷
            pipe = (
                f"v4l2src device={dev} ! videoconvert ! video/x-raw,format=BGR "
                f"! appsink drop=true"
            )
        cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
        return cap

    # 기본: V4L2 경로 (라즈베리파이/USB캠에서 안정적)
    api = cv2.CAP_V4L2 if args.api in ("auto", "v4l2") else cv2.CAP_ANY
    cap = cv2.VideoCapture(args.camera_index, api)

    # fourcc 설정
    if args.fourcc != "NONE":
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))

    # 해상도 설정
    if args.cap_width > 0 and args.cap_height > 0:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  args.cap_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.cap_height)

    return cap

# ────────────────────────────────
# 전처리 (Ultralytics가 알아서 resize/normalize 하니까 최소한으로 둠)
# ────────────────────────────────
def preprocess_bgr_for_ultralytics(frame):
    """
    현재는 그냥 BGR frame 그대로 반환.
    필요하면 여기서 색공간 변환 등 추가.
    """
    if frame is None or not hasattr(frame, "shape"):
        return None
    return frame

# ────────────────────────────────
# Ultralytics 결과를 (x1,y1,x2,y2,conf,cls) float32 배열로 변환
# ────────────────────────────────
def yolo_results_to_dets(yolo_result, score_thr=0.20):
    """
    yolo_result: Ultralytics YOLO 추론 결과 중 하나(results[0] 등)
    return: Nx6 [x1,y1,x2,y2,conf,cls]
    """
    if yolo_result is None or yolo_result.boxes is None or len(yolo_result.boxes) == 0:
        return np.zeros((0, 6), dtype=np.float32)

    b = yolo_result.boxes
    xyxy = b.xyxy.cpu().numpy().astype(np.float32)
    conf = b.conf.cpu().numpy().astype(np.float32)
    cls  = b.cls.cpu().numpy().astype(np.float32)

    keep = conf >= float(score_thr)
    if not np.any(keep):
        return np.zeros((0, 6), dtype=np.float32)

    xyxy, conf, cls = xyxy[keep], conf[keep], cls[keep]
    return np.concatenate(
        [xyxy, conf.reshape(-1, 1), cls.reshape(-1, 1)],
        axis=1
    ).astype(np.float32)

# ────────────────────────────────
# 메인 루프
# ────────────────────────────────
def main(args):
    # 라벨 로딩 (선택)
    labels = None
    try:
        with open(args.labels_path) as f:
            labels = json.load(f).get("labels", None)
    except Exception:
        labels = None

    # MQTT 연결 시작 + CPU 온도 퍼블리셔 스레드
    mqtt_client = try_connect()
    threading.Thread(target=cpu_temp_publisher, daemon=True).start()

    # MJPEG 서버 시작 (/video)
    start_mjpeg_server(host="0.0.0.0", port=args.http_port)

    # ── YOLO 모델 로드
    device = args.device
    if device.lower() == "cuda" and not torch.cuda.is_available():
        print("⚠️ CUDA를 사용할 수 없습니다. CPU로 전환합니다.")
        device = "cpu"

    print(f"📦 모델 로드 중: {args.pt_path} (device={device})")
    model = YOLO(args.pt_path)

    # 모델 내 클래스 이름들 (fallback용)
    try:
        model_names = model.names
    except Exception:
        model_names = None

    # 카메라 열기
    cap = open_capture(args)
    if not cap.isOpened():
        print("❌ 카메라 열기 실패")
        sys.exit(1)

    # 미리보기 창 준비 (headless 환경이면 나중에 주석 처리 가능)
    cv2.namedWindow("🔥 YOLO(.pt) Detection (MQTT)", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("🔥 YOLO(.pt) Detection (MQTT)", 960, 540)

    print("📸 실시간 추론 시작 — 'q' 누르면 종료")
    printed_probe = False

    # 추론 옵션
    imgsz = args.imgsz
    half  = args.half and (device == "cuda")
    if half:
        try:
            model.model.half()
            print("🧮 FP16(half) 활성화")
        except Exception:
            print("⚠️ half 변환 실패 → FP32로 진행")
            half = False

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            print("❗ 프레임 읽기 실패")
            break

        # 첫 프레임 디버그 정보 한 번만 출력
        if not printed_probe:
            try:
                print(f"[PROBE] ret={ret}, shape={frame.shape}, mean={float(frame.mean()):.2f}")
            except Exception:
                print(f"[PROBE] ret={ret}, frame=None")
            printed_probe = True

        t0 = time.time()

        # 전처리 (필요 시 추가 가능)
        input_img = preprocess_bgr_for_ultralytics(frame)

        # YOLO 추론
        results = model.predict(
            source=input_img,
            imgsz=imgsz,
            device=device,
            verbose=False
        )
        det = yolo_results_to_dets(
            results[0] if results else None,
            score_thr=args.score_thr
        )

        # 시각화 + MQTT 알림
        if det is not None and det.size > 0:
            for (x1, y1, x2, y2, conf, cls_id) in det:
                x1d, y1d, x2d, y2d = int(x1), int(y1), int(x2), int(y2)

                # 라벨 이름 우선순위: labels.json > model.names > fallback
                if labels and 0 <= int(cls_id) < len(labels):
                    label = labels[int(cls_id)]
                elif model_names:
                    try:
                        if isinstance(model_names, dict):
                            label = model_names.get(int(cls_id), f"id:{int(cls_id)}")
                        else:
                            label = model_names[int(cls_id)]
                    except Exception:
                        label = f"id:{int(cls_id)}"
                else:
                    label = f"id:{int(cls_id)}"

                # 박스 & 텍스트
                cv2.rectangle(frame, (x1d, y1d), (x2d, y2d), (0, 0, 255), 2)
                cv2.putText(
                    frame,
                    f"{label} {conf:.2f}",
                    (x1d, y1d - 8),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 0, 255),
                    2
                )

                # fire / smoke 라벨 감지 시 MQTT 전송
                lower = str(label).lower().strip()
                if lower in ("fire", "smoke"):
                    mqtt_client = send_detection_mqtt(mqtt_client, lower)

        # FPS 계산
        fps = 1.0 / max(1e-6, (time.time() - t0))
        cv2.putText(
            frame,
            f"FPS: {fps:.2f}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2
        )

        # MJPEG 송출 프레임 갱신
        update_stream_frame(frame, quality=args.jpg_quality)

        # 로컬 미리보기 (모니터 없으면 여기부터 imshow 부분 날려도 됨)
        cv2.imshow("🔥 YOLO(.pt) Detection (MQTT)", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("🛑 종료 중...")
            break

    cap.release()
    cv2.destroyAllWindows()

# ────────────────────────────────
# 인자 파서
# ────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser()

    # 모델/라벨
    p.add_argument(
        "--pt-path",
        type=str,
        default="./fire.pt",
        help="Ultralytics YOLO .pt 가중치 경로"
    )
    p.add_argument(
        "--labels-path",
        type=str,
        default="./labels.json",
        help='{"labels": ["fire","smoke",...]} 형태 JSON (없으면 모델 내 names 사용)'
    )

    # 카메라
    p.add_argument("--camera-index", type=int, default=0)
    p.add_argument(
        "--api",
        choices=["auto", "v4l2", "gst"],
        default="v4l2",
        help="카메라 캡처 백엔드 (라즈파이/USB캠은 v4l2 권장, 필요시 gst)"
    )
    p.add_argument(
        "--fourcc",
        choices=["MJPG", "YUYV", "NONE"],
        default="MJPG",
        help="카메라 입력 포맷 (USB 캠이면 MJPG가 보통 가장 안정적으로 고해상도/고FPS)"
    )
    p.add_argument("--cap-width",  type=int, default=640, help="카메라 폭 (장치 지원 해상도)")
    p.add_argument("--cap-height", type=int, default=480, help="카메라 높이 (장치 지원 해상도)")

    # 검출/추론 파라미터
    p.add_argument("--score-thr", type=float, default=0.20, help="신뢰도 임계값")
    p.add_argument("--imgsz",     type=int,   default=640,  help="YOLO 추론 입력 크기")
    p.add_argument(
        "--device",
        type=str,
        default="cpu",
        choices=["cpu", "cuda"],
        help="추론 디바이스"
    )
    p.add_argument(
        "--half",
        action="store_true",
        help="FP16 추론 (CUDA 전용, 지원 안 되면 자동 fallback)"
    )

    # MJPEG 스트림 옵션
    p.add_argument(
        "--http-port",
        type=int,
        default=5055,
        help="MJPEG 스트림 HTTP 포트 (/video)"
    )
    p.add_argument(
        "--jpg-quality",
        type=int,
        default=80,
        help="스트림 JPEG 품질(1-100)"
    )

    return p.parse_args()

# ────────────────────────────────
# 실행
# ────────────────────────────────
if __name__ == "__main__":
    try:
        args = parse_args()
        main(args)
    except KeyboardInterrupt:
        print("\n🛑 사용자에 의해 종료됨")
        sys.exit(0)
    except Exception as e:
        print("❌ 메인 실행 중 오류 발생:", e)
        sys.exit(1)
