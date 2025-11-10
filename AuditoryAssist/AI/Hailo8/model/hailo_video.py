#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
# 라즈베리파이에서 Xorg 쓸 때 창이 안 뜨는 문제 방지 (Wayland에서도 xcb로 강제)
os.environ["QT_QPA_PLATFORM"] = "xcb"

import argparse
import threading
import time
import json
import sys
import socket
import signal
import numpy as np
import paho.mqtt.client as mqtt
import cv2
from flask import Flask, Response  # 🔥 MJPEG 스트리밍용

from hailo_platform import (
    VDevice, HEF, InferVStreams,
    InputVStreamParams, OutputVStreamParams,
    HailoStreamInterface, ConfigureParams,
)

# ────────────────────────────────
# 글로벌 종료 이벤트 (백그라운드 안전 종료용)
# ────────────────────────────────
stop_event = threading.Event()

def _request_shutdown(reason=""):
    print(f"🛑 종료 요청: {reason}")
    stop_event.set()

def _setup_signal_handlers():
    def _handler(sig, frame):
        _request_shutdown(f"signal={sig}")
    signal.signal(signal.SIGINT, _handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, _handler)  # systemd stop 등

# ────────────────────────────────
# MJPEG 스트리밍
# ────────────────────────────────
app = Flask(__name__)
latest_jpeg = None  # 가장 최근 프레임을 JPEG로 저장해둔다 (바이트)

def mjpeg_generator():
    """
    최신 프레임을 multipart/x-mixed-replace 형태로 계속 내보내는 제너레이터
    """
    global latest_jpeg
    boundary = b"--frame\r\n"
    while not stop_event.is_set():
        if latest_jpeg is not None:
            yield (
                boundary +
                b"Content-Type: image/jpeg\r\n\r\n" +
                latest_jpeg +
                b"\r\n"
            )
        else:
            time.sleep(0.05)

@app.route("/video")
def video_feed():
    """
    브라우저에서 http://<host>:<port>/video 로 접속하면
    M-JPEG 스트림을 볼 수 있음
    """
    return Response(
        mjpeg_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

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
    return th


# ────────────────────────────────
# MQTT 설정
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
    "fire": {
        "topic": "AI_fire_alert",
        "payload": {"sensor_id": "AI_D_fire", "event": "fire_detected"}
    }
}

last_sent = {"fire": 0, "smoke": 0}
COOLDOWN = 20  # 초 단위로 알림 쿨다운


# ────────────────────────────────
# MQTT 유틸
# ────────────────────────────────
def try_connect(client_id=None):
    """MQTT 브로커에 연결 시도하고 client (또는 None) 리턴."""
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
    """라즈베리파이 CPU 온도(°C). 없는 환경이면 0 리턴."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read()) / 1000.0
    except FileNotFoundError:
        print("⚠️ CPU 온도 센서를 찾을 수 없습니다. 기본값 0 사용.")
        return 0.0


def cpu_temp_publisher():
    """
    백그라운드로 돌아가면서 CPU 온도 주기적으로 MQTT publish.
    연결 끊기면 10초마다 재시도.
    stop_event 세트되면 안전 종료.
    """
    client = try_connect(TEMP_CLIENT_ID)
    reconnect_timer = time.time()
    try:
        while not stop_event.is_set():
            try:
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

                # 짧게 나눠서 sleep → 종료 신호 반응성 향상
                for _ in range(30):
                    if stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception as e:
                print("⚠️ CPU 온도 루프 오류:", e)
                for _ in range(30):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
    finally:
        if client:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
        print("✅ 온도 퍼블리셔 종료")


def send_detection_mqtt(client, cls_name):
    """
    fire/smoke 감지 시 MQTT 알림 전송 (쿨다운 적용)
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
# 카메라 열기
# ────────────────────────────────
def open_capture(args):
    """
    args.api:    auto|v4l2|gst
    args.fourcc: MJPG|YUYV|NONE
    args.cap_width/height: 원하는 해상도
    """
    if args.api == "gst":
        # GStreamer 파이프라인으로 열기
        dev = f"/dev/video{args.camera_index}"
        w, h = args.cap_width, args.cap_height

        if args.fourcc == "MJPG":
            pipe = (
                f"v4l2src device={dev} ! "
                f"image/jpeg,framerate=30/1,width={w},height={h} ! "
                f"jpegdec ! videoconvert ! video/x-raw,format=BGR ! "
                f"appsink drop=true"
            )
        elif args.fourcc == "YUYV":
            pipe = (
                f"v4l2src device={dev} io-mode=2 ! "
                f"video/x-raw,format=YUY2,framerate=30/1,width={w},height={h} ! "
                f"videoconvert ! video/x-raw,format=BGR ! "
                f"appsink drop=true"
            )
        else:
            pipe = (
                f"v4l2src device={dev} ! "
                f"videoconvert ! video/x-raw,format=BGR ! "
                f"appsink drop=true"
            )

        cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
        return cap

    # 일반 V4L2 경로
    api_flag = cv2.CAP_V4L2 if args.api in ("auto", "v4l2") else cv2.CAP_ANY
    cap = cv2.VideoCapture(args.camera_index, api_flag)

    # fourcc 설정 (예: MJPG)
    if args.fourcc != "NONE":
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))

    # 해상도 힌트
    if args.cap_width > 0 and args.cap_height > 0:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.cap_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.cap_height)

    return cap


# ────────────────────────────────
# 전처리 (모델 입력 만들기) + 복원 매핑 계산
# ────────────────────────────────
def preprocess_for_hailo(frame_bgr, net_h=640, net_w=640):
    """
    net_h x net_w RGB uint8 준비:
    - BGR -> RGB
    - 짧은 변을 맞춰 리사이즈
    - 중앙 크롭
    - uint8 유지
    """
    h0, w0 = frame_bgr.shape[:2]

    # BGR -> RGB
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

    # 스케일 결정 (짧은 변을 net에 맞춘다)
    if h0 < w0:
        scale = net_h / float(h0)
        new_h, new_w = net_h, int(round(w0 * scale))
    else:
        scale = net_w / float(w0)
        new_w, new_h = net_w, int(round(h0 * scale))

    resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    # 중앙 크롭
    left = max(0, (new_w - net_w) // 2)
    top = max(0, (new_h - net_h) // 2)
    right = left + net_w
    bottom = top + net_h
    crop = resized[top:bottom, left:right, :]

    # 크롭된 게 부족하면 패딩
    if crop.shape[0] != net_h or crop.shape[1] != net_w:
        canvas = np.zeros((net_h, net_w, 3), dtype=np.uint8)
        y_off = (net_h - crop.shape[0]) // 2
        x_off = (net_w - crop.shape[1]) // 2
        canvas[y_off:y_off+crop.shape[0], x_off:x_off+crop.shape[1], :] = crop
        crop = canvas

    crop = np.ascontiguousarray(crop.astype(np.uint8))
    return crop, scale, left, top


def map_box_back_to_original(x1, y1, x2, y2, scale, left, top, orig_w, orig_h):
    """
    모델 좌표(640x640 crop 기준) 박스를 원본 frame 좌표로 되돌린다.
    """
    x1o = (x1 + left) / scale
    y1o = (y1 + top) / scale
    x2o = (x2 + left) / scale
    y2o = (y2 + top) / scale

    # 화면 밖 넘어가는 값 클램프
    x1o = max(0, min(orig_w - 1, x1o))
    y1o = max(0, min(orig_h - 1, y1o))
    x2o = max(0, min(orig_w - 1, x2o))
    y2o = max(0, min(orig_h - 1, y2o))

    return int(x1o), int(y1o), int(x2o), int(y2o)


# ────────────────────────────────
# 후처리: DFL decode + NMS
# ────────────────────────────────
def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))

def _softmax(x, axis=-1):
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=axis, keepdims=True)

def _squeeze_hw(arr):
    """
    InferVStreams 결과 (1,H,W,C) → (H,W,C) 로 batch 차원 제거
    """
    if arr is None:
        return None
    a = np.asarray(arr)
    while a.ndim > 3 and a.shape[0] == 1:
        a = a[0]
    return a

def decode_head_dfl(reg_map, cls_map, stride, num_bins=16, score_thr=0.5):
    """
    DFL 기반 회귀 결과를 bbox로 복원
    """
    if reg_map is None or cls_map is None:
        return (np.zeros((0, 4), dtype=np.float32),
                np.zeros((0,), dtype=np.float32))

    Hs, Ws, C = reg_map.shape
    expected_c = 4 * num_bins
    if C != expected_c:
        print(f"⚠️ 예기치 않은 reg_map 채널수 {C}, 기대 {expected_c}")
        return (np.zeros((0, 4), dtype=np.float32),
                np.zeros((0,), dtype=np.float32))

    # class score (sigmoid)
    cls_score = _sigmoid(cls_map[..., 0])  # (Hs,Ws)

    # DFL 분포 -> 기대값
    reg4 = reg_map.reshape(Hs, Ws, 4, num_bins)
    prob = _softmax(reg4, axis=3)  # (Hs,Ws,4,num_bins)
    bins = np.arange(num_bins, dtype=np.float32)
    dist = np.sum(prob * bins[None, None, None, :], axis=3)  # (Hs,Ws,4)

    l = dist[..., 0]
    t = dist[..., 1]
    r = dist[..., 2]
    b = dist[..., 3]

    # grid 좌표 (셀 센터 → stride 반영)
    gy, gx = np.meshgrid(
        np.arange(Hs, dtype=np.float32),
        np.arange(Ws, dtype=np.float32),
        indexing='ij'
    )
    cx = (gx + 0.5) * stride
    cy = (gy + 0.5) * stride

    # box 복원 xyxy
    x1 = cx - l * stride
    y1 = cy - t * stride
    x2 = cx + r * stride
    y2 = cy + b * stride

    # flatten
    x1 = x1.reshape(-1)
    y1 = y1.reshape(-1)
    x2 = x2.reshape(-1)
    y2 = y2.reshape(-1)
    sc = cls_score.reshape(-1)

    # confidence 필터
    keep = sc >= float(score_thr)
    if not np.any(keep):
        return (np.zeros((0, 4), dtype=np.float32),
                np.zeros((0,), dtype=np.float32))

    boxes = np.stack([x1[keep], y1[keep], x2[keep], y2[keep]], axis=1).astype(np.float32)
    scores = sc[keep].astype(np.float32)
    return boxes, scores

def nms_numpy(boxes, scores, iou_th=0.5, max_dets=100):
    """
    간단한 greedy NMS.
    """
    if boxes.shape[0] == 0:
        return []

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]

    areas = (x2 - x1) * (y2 - y1)
    order = np.argsort(-scores)  # high -> low

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        if len(keep) >= max_dets:
            break

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)

        inds = np.where(iou <= iou_th)[0]
        order = order[inds + 1]

    return keep

def resolve_decoder_layers_from_cfg(decoders_cfg, out_vstream_names):
    """
    cfg에 적힌 헤드 레이어 이름(prefix 다를 수 있음)을
    실제 HEF 출력 vstream 이름으로 suffix 기준 매핑
    """
    suffix_map = {}
    for full in out_vstream_names:
        suf = full.split("/")[-1]
        suffix_map[suf] = full

    resolved = []
    for d in decoders_cfg:
        stride = d["stride"]
        reg_suffix = d["reg_layer"].split("/")[-1]
        cls_suffix = d["cls_layer"].split("/")[-1]

        reg_full = suffix_map.get(reg_suffix, d["reg_layer"])
        cls_full = suffix_map.get(cls_suffix, d["cls_layer"])

        resolved.append({
            "stride": stride,
            "reg_layer": reg_full,
            "cls_layer": cls_full
        })
    return resolved

def postprocess_all_scales(results_dict,
                           decoders_resolved,
                           num_bins,
                           score_thr,
                           iou_th,
                           max_det):
    """
    여러 스케일 헤드들에서 나온 bbox 후보들을 합치고 NMS 적용
    return dets (N,6): [x1,y1,x2,y2,score,cls_id]
    """
    all_boxes = []
    all_scores = []

    for d in decoders_resolved:
        stride = d["stride"]
        reg_name = d["reg_layer"]
        cls_name = d["cls_layer"]

        reg_map = _squeeze_hw(results_dict.get(reg_name))
        cls_map = _squeeze_hw(results_dict.get(cls_name))

        boxes, scores = decode_head_dfl(
            reg_map,
            cls_map,
            stride=stride,
            num_bins=num_bins,
            score_thr=score_thr
        )

        if boxes.shape[0] > 0:
            all_boxes.append(boxes)
            all_scores.append(scores)

    if not all_boxes:
        return np.zeros((0, 6), dtype=np.float32)

    all_boxes = np.concatenate(all_boxes, axis=0)
    all_scores = np.concatenate(all_scores, axis=0)

    keep_idx = nms_numpy(all_boxes, all_scores, iou_th=iou_th, max_dets=max_det)
    if not keep_idx:
        return np.zeros((0, 6), dtype=np.float32)

    final_boxes = all_boxes[keep_idx]
    final_scores = all_scores[keep_idx]

    # 단일 클래스 가정 → cls_id = 0
    cls_col = np.zeros((final_boxes.shape[0], 1), dtype=np.float32)

    dets = np.concatenate(
        [
            final_boxes.astype(np.float32),
            final_scores.reshape(-1, 1).astype(np.float32),
            cls_col
        ],
        axis=1
    )
    return dets


# ────────────────────────────────
# 메인 루프
# ────────────────────────────────
def main(args):
    global latest_jpeg

    # 라벨 로딩 (labels.json → {"labels": ["fire","smoke", ...]})
    labels = None
    try:
        with open(args.labels_path) as f:
            labels = json.load(f).get("labels", None)
    except Exception:
        labels = None

    # NMS/decoder config 로딩
    with open(args.config_path, "r") as f:
        nms_cfg = json.load(f)

    cfg_num_bins = int(nms_cfg.get("regression_length", 16))
    cfg_score_thr = float(nms_cfg.get("nms_scores_th", 0.5))
    cfg_iou_thr = float(nms_cfg.get("nms_iou_th", 0.5))
    cfg_max_det = int(nms_cfg.get("max_proposals_per_class", 100))
    cfg_decoders = nms_cfg.get("bbox_decoders", [])

    # 커맨드라인에서 덮어쓰기 가능
    score_thr = args.score_thr if args.score_thr is not None else cfg_score_thr
    iou_thr = args.iou_thr if args.iou_thr is not None else cfg_iou_thr
    max_det = args.max_det if args.max_det is not None else cfg_max_det

    # MQTT 스타트
    mqtt_client = try_connect()
    temp_thread = threading.Thread(target=cpu_temp_publisher, daemon=True)
    temp_thread.start()

    # Hailo 장치/네트워크 준비
    with VDevice() as device:
        hef = HEF(args.hef_path)

        cfg_params = ConfigureParams.create_from_hef(
            hef,
            interface=HailoStreamInterface.PCIe
        )
        ng_list = device.configure(hef, cfg_params)
        network_group = ng_list[0] if isinstance(ng_list, (list, tuple)) else ng_list

        # vstream 정보
        in_infos = hef.get_input_vstream_infos()
        out_infos = hef.get_output_vstream_infos()
        assert len(in_infos) >= 1, "입력 vstream 없음?"
        assert len(out_infos) >= 1, "출력 vstream 없음?"

        in_info = in_infos[0]
        in_shape = tuple(in_info.shape)  # (H,W,3) NHWC
        assert len(in_shape) == 3 and in_shape[2] == 3, f"예상과 다른 입력 shape: {in_shape}"
        net_h, net_w = int(in_shape[0]), int(in_shape[1])

        print("🔎 입력 vstream 목록:")
        for ii in in_infos:
            print(f"   - {ii.name} : shape={ii.shape}")
        print("🔎 출력 vstream 목록:")
        for oi in out_infos:
            print(f"   - {oi.name} : shape={oi.shape}")

        print(f"📋 HEF 입력: shape={in_shape}, layout=NHWC, size={net_w}x{net_h}")

        # decoder layer suffix 매핑
        out_names = [oi.name for oi in out_infos]
        decoders_resolved = resolve_decoder_layers_from_cfg(cfg_decoders, out_names)

        # vstream params (기본 설정 사용)
        in_params  = InputVStreamParams.make_from_network_group(network_group)
        out_params = OutputVStreamParams.make_from_network_group(network_group)

        ng_params = network_group.create_params()

        with network_group.activate(ng_params):
            with InferVStreams(network_group, in_params, out_params) as infer_pipeline:
                in_name = in_info.name
                print(f"🔎 사용 중인 입력 vstream 이름: {in_name}")
                print("🔎 디코더 after resolve:")
                for d in decoders_resolved:
                    print(f"    stride={d['stride']}, reg={d['reg_layer']}, cls={d['cls_layer']}")

                # 카메라 열기
                cap = open_capture(args)
                if not cap.isOpened():
                    print("❌ 카메라 열기 실패")
                    sys.exit(1)

                # MJPEG 서버 시작 (백그라운드에서 /video 제공)
                http_thread = start_mjpeg_server(host="0.0.0.0", port=5055)

                # 로컬 창 옵션 처리 (기본 headless: 창 X)
                if args.window:
                    cv2.namedWindow("🔥 Hailo YOLOv8 Detection (MQTT)", cv2.WINDOW_NORMAL)
                    cv2.resizeWindow("🔥 Hailo YOLOv8 Detection (MQTT)", 960, 540)
                    print("📸 실시간 추론 시작 — 'q' 누르면 종료 (윈도우 모드)")
                else:
                    print("📸 실시간 추론 시작 (헤드리스 모드)")

                printed_probe = False

                while not stop_event.is_set():
                    loop_start = time.time()

                    # 1) 프레임 캡처
                    t0 = time.time()
                    ret, frame_bgr = cap.read()
                    t1 = time.time()
                    if not ret or frame_bgr is None:
                        print("❗ 프레임 읽기 실패")
                        break

                    if not printed_probe:
                        try:
                            print(f"[PROBE] ret={ret}, shape={frame_bgr.shape}, mean={float(frame_bgr.mean()):.2f}")
                        except Exception:
                            print(f"[PROBE] ret={ret}, frame=None")
                        printed_probe = True

                    orig_h, orig_w = frame_bgr.shape[:2]

                    # 2) 전처리 → RGB uint8 (net_h x net_w)
                    t2_prep_start = time.time()
                    img_rgb_crop, scale, left, top = preprocess_for_hailo(
                        frame_bgr,
                        net_h=net_h,
                        net_w=net_w
                    )
                    t2 = time.time()

                    # 3) Hailo 추론
                    t3_infer_start = time.time()

                    # Hailo는 (배치, H, W, C) = (1,640,640,3) uint8, NHWC를 기대
                    hailo_input = np.expand_dims(img_rgb_crop, axis=0).astype(np.uint8, copy=False)
                    hailo_input = np.ascontiguousarray(hailo_input)

                    # 디버그
                    print("[DEBUG] hailo_input shape:", hailo_input.shape)
                    print("[DEBUG] hailo_input dtype:", hailo_input.dtype)
                    print("[DEBUG] hailo_input nbytes:", hailo_input.nbytes)
                    print("[DEBUG] hailo_input C_CONTIGUOUS?:", hailo_input.flags['C_CONTIGUOUS'])

                    # 실제 추론
                    results = infer_pipeline.infer({in_name: hailo_input})
                    t3 = time.time()

                    # 4) 후처리 (DFL decode + NMS)
                    t4_post_start = time.time()
                    det = postprocess_all_scales(
                        results_dict=results,
                        decoders_resolved=decoders_resolved,
                        num_bins=cfg_num_bins,
                        score_thr=score_thr,
                        iou_th=iou_thr,
                        max_det=max_det
                    )
                    t4 = time.time()

                    # 5) 박스 그리기 & MQTT
                    t5_draw_start = time.time()
                    if det is not None and det.size > 0:
                        # det: [x1,y1,x2,y2,score,cls_id] 모델 좌표(640x640 crop)
                        for (x1m, y1m, x2m, y2m, conf, cls_id) in det:
                            # 모델 좌표 -> 원본 frame 좌표
                            x1_px, y1_px, x2_px, y2_px = map_box_back_to_original(
                                x1m, y1m, x2m, y2m,
                                scale, left, top,
                                orig_w, orig_h
                            )

                            # 항상 좌표를 (왼쪽위, 오른쪽아래) 순서로 정렬
                            x1_draw = int(min(x1_px, x2_px))
                            y1_draw = int(min(y1_px, y2_px))
                            x2_draw = int(max(x1_px, x2_px))
                            y2_draw = int(max(y1_px, y2_px))

                            # 라벨 결정
                            if labels and 0 <= int(cls_id) < len(labels):
                                label_str = labels[int(cls_id)]
                            else:
                                label_str = f"id:{int(cls_id)}"

                            # 디버그 출력
                            print(
                                f"[BOX] {label_str} conf={conf:.2f} "
                                f"box=({x1_draw},{y1_draw})-({x2_draw},{y2_draw}) "
                                f"frame_size={orig_w}x{orig_h}"
                            )

                            cv2.rectangle(
                                frame_bgr,
                                (x1_draw, y1_draw),
                                (x2_draw, y2_draw),
                                (0, 0, 255),
                                4
                            )

                            cx = int((x1_draw + x2_draw) / 2)
                            cy = int((y1_draw + y2_draw) / 2)
                            cv2.circle(frame_bgr, (cx, cy), 5, (0, 255, 0), -1)

                            cv2.putText(
                                frame_bgr,
                                f"{label_str} {conf:.2f}",
                                (x1_draw, max(y1_draw - 8, 10)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 0, 255),
                                2
                            )

                            # fire / smoke 감지 시 MQTT 전송
                            low_label = str(label_str).lower().strip()
                            if low_label in ("fire", "smoke"):
                                mqtt_client = send_detection_mqtt(mqtt_client, low_label)

                    # FPS 표시
                    loop_end_now = time.time()
                    fps_display = 1.0 / max(1e-6, (loop_end_now - loop_start))
                    cv2.putText(
                        frame_bgr,
                        f"FPS: {fps_display:.2f}",
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 0),
                        2
                    )

                    # 🔥 MJPEG용 최신 프레임 업데이트
                    ok, jpeg_buf = cv2.imencode(".jpg", frame_bgr)
                    if ok:
                        latest_jpeg = jpeg_buf.tobytes()

                    # 로컬 미리보기 창 (원하면만)
                    if args.window:
                        cv2.imshow("🔥 Hailo YOLOv8 Detection (MQTT)", frame_bgr)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            _request_shutdown("user pressed q")

                    t5 = time.time()

                    # 6) 타이밍 디버그
                    print(
                        "⏱ 성능측정 | "
                        f"캡처 {(t1 - t0)*1000:.1f}ms | "
                        f"전처리 {(t2 - t2_prep_start)*1000:.1f}ms | "
                        f"Hailo {(t3 - t3_infer_start)*1000:.1f}ms | "
                        f"후처리 {(t4 - t4_post_start)*1000:.1f}ms | "
                        f"표시/전송 {(t5 - t5_draw_start)*1000:.1f}ms | "
                        f"전체 {(t5 - t0)*1000:.1f}ms | "
                        f"FPS {fps_display:.2f}"
                    )

                # 정리
                cap.release()
                if args.window:
                    cv2.destroyAllWindows()

    # MQTT 메인 클라이언트 정리
    if mqtt_client:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass

    # 온도 스레드 종료 대기 (최대 2초)
    temp_thread.join(timeout=2)
    print("✅ 메인 종료 완료")


# ────────────────────────────────
# 인자 파서
# ────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser()

    # Hailo 모델 / 라벨 / 후처리 config
    p.add_argument("--hef-path", type=str, default="./fire.hef",
                   help="compile.py에서 뽑은 HEF 경로")
    p.add_argument("--labels-path", type=str, default="./labels.json",
                   help='{"labels":["fire","smoke",...]} 이런 구조')
    p.add_argument("--config-path", type=str,
                   default="/home/dlgyals/Downloads/hailo/models/yolov8n_nms_config.json",
                   help="yolov8n_nms_config.json 경로")

    # 카메라
    p.add_argument("--camera-index", type=int, default=0)
    p.add_argument("--api", choices=["auto", "v4l2", "gst"], default="v4l2",
                   help="카메라 백엔드 (라즈파이는 v4l2 권장)")
    p.add_argument("--fourcc", choices=["MJPG", "YUYV", "NONE"], default="MJPG",
                   help="카메라 포맷 (USB캠은 MJPG가 고FPS 잘 나옴)")
    p.add_argument("--cap-width", type=int, default=640,
                   help="카메라 요청 가로 해상도")
    p.add_argument("--cap-height", type=int, default=640,
                   help="카메라 요청 세로 해상도")

    # 후처리 threshold / NMS
    p.add_argument("--score-thr", type=float, default=None,
                   help="박스로 인정할 최소 confidence (기본은 json nms_scores_th)")
    p.add_argument("--iou-thr", type=float, default=None,
                   help="NMS IoU 임계값 (기본은 json nms_iou_th)")
    p.add_argument("--max-det", type=int, default=None,
                   help="최종 NMS 후 남길 최대 박스 수 (기본은 json max_proposals_per_class)")

    # ▶︎ 헤드리스/윈도우 모드 스위치
    p.add_argument("--window", action="store_true",
                   help="로컬 미리보기 창을 띄움(기본: 헤드리스)")

    return p.parse_args()


# ────────────────────────────────
# 실행부
# ────────────────────────────────
if __name__ == "__main__":
    _setup_signal_handlers()
    try:
        args = parse_args()
        main(args)
    except KeyboardInterrupt:
        _request_shutdown("KeyboardInterrupt")
    except Exception as e:
        print("❌ 메인 실행 중 오류 발생:", e)
        sys.exit(1)
    finally:
        # 혹시 남아있으면 한 번 더 종료 신호
        stop_event.set()
        print("👋 프로세스 종료")
        sys.exit(0)
