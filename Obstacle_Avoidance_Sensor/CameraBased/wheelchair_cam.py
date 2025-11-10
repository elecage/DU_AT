#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import serial
import cv2
from ultralytics import YOLO
import time
import sys
import traceback

print("가상 환경에서 실행 중")

# ====== 구성 ======
WEIGHTS = "/home/pi/best.pt"
SERIAL_PORT = "/dev/ttyACM0"
BAUD = 115200
SERIAL_TIMEOUT = 0.05     # 비블로킹 느낌
CAM_INDEX_TRY = [0, 1]    # 0 실패 시 1 시도
IMG_SIZE = 640
CONF = 0.35
IOU = 0.45
TARGET_CLS = 0            # '사람 발' 클래스 id
FULLSCREEN = True
POST_END_HOLD_SHORT = 5   # ms<2000 일 때 유지시간(초)
POST_END_HOLD_LONG  = 10  # ms>=2000 일 때 유지시간(초)
WARMUP_DROP_FRAMES  = 2   # 활성 직후 드레인할 프레임 수

# ====== 모델 로드 ======
try:
    model = YOLO(WEIGHTS)
    # model.fuse()  # (옵션) 조금 더 빠르게
except Exception as e:
    print(f"모델 파일 로드 실패: {e}")
    sys.exit(1)

# ====== 시리얼 연결 ======
try:
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=SERIAL_TIMEOUT)
except Exception as e:
    print(f"시리얼 포트 연결 실패: {e}")
    sys.exit(1)

# ====== 카메라 열기 ======
cap = None
for idx in CAM_INDEX_TRY:
    c = cv2.VideoCapture(idx, cv2.CAP_V4L2)
    if c.isOpened():
        # RPi 지연 줄이기용 세팅
        c.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        c.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # c.set(cv2.CAP_PROP_FPS, 30)  # 드라이버에 따라 무시될 수 있음
        cap = c
        break

if cap is None or not cap.isOpened():
    print("웹캠 연결 실패")
    sys.exit(1)

# ====== 창 설정 ======
cv2.namedWindow("Wheelchair Camera", cv2.WINDOW_NORMAL)
if FULLSCREEN:
    cv2.setWindowProperty("Wheelchair Camera", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

# ====== 상태 변수 ======
camera_active = False
off_deadline = None  # 특정 시각(unixtime초)까지 유지

# FPS 측정용
last_t = time.time()
frame_count = 0
fps = 0.0

# 활성 직후 워밍업용 카운터
warmup_left = 0

print("준비 완료. 시리얼 이벤트를 기다리는 중... (q로 종료)")

def activate_camera():
    global camera_active, off_deadline, warmup_left
    camera_active = True
    off_deadline = None
    warmup_left = WARMUP_DROP_FRAMES

def schedule_off_by_duration_ms(duration_ms: int):
    global off_deadline
    now_evt = time.time()
    timeout_sec = POST_END_HOLD_SHORT if duration_ms < 2000 else POST_END_HOLD_LONG
    off_deadline = now_evt + timeout_sec
    print(f"후진 종료, 지속시간: {duration_ms} ms → 카메라 {timeout_sec}s 유지 후 종료 예약")

try:
    while True:
        now = time.time()

        # ---- 시리얼 읽기(비블로킹) ----
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    # print(f"[SERIAL] {line}")  # 필요시 디버그
                    if line == "BACKWARD_START":
                        print("후진 시작 감지 → 카메라 활성화")
                        activate_camera()

                    elif line.startswith("BACKWARD_END:"):
                        try:
                            duration_ms = int(line.split(":", 1)[1])
                            schedule_off_by_duration_ms(duration_ms)
                        except ValueError:
                            print("지속시간 파싱 실패:", line)
        except Exception as e:
            print("시리얼 읽기 오류:", e)

        # ---- 카메라 ON/OFF 상태 갱신 ----
        if camera_active and off_deadline is not None and now >= off_deadline:
            camera_active = False
            off_deadline = None
            print("카메라 종료")

        # ---- 프레임 처리 ----
        if camera_active:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            # 활성 직후 버퍼 드레인(옵션)
            if warmup_left > 0:
                warmup_left -= 1
                continue

            try:
                results = model.predict(
                    source=frame,
                    imgsz=IMG_SIZE,
                    conf=CONF,
                    iou=IOU,
                    verbose=False
                )[0]
            except Exception as e:
                # 추론 중 예외가 나도 루프 지속
                print("추론 예외 발생:", e)
                traceback.print_exc(limit=1)
                time.sleep(0.01)
                continue

            boxes = getattr(results, "boxes", None)
            if boxes is not None and len(boxes) > 0:
                cls_tensor = boxes.cls  # (N,)
                xyxy = boxes.xyxy       # (N,4)
                for i in range(len(xyxy)):
                    cls_id = int(cls_tensor[i].item())
                    if cls_id == TARGET_CLS:
                        x1, y1, x2, y2 = map(int, xyxy[i].tolist())
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)

            # FPS 표시
            frame_count += 1
            if now - last_t >= 1.0:
                fps = frame_count / (now - last_t)
                frame_count = 0
                last_t = now
            cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

            # 상태 오버레이
            if off_deadline is not None:
                remain = max(0.0, off_deadline - time.time())
                cv2.putText(frame, f"Hold: {remain:0.1f}s", (10, 65),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

            cv2.imshow("Wheelchair Camera", frame)

            # q 키 종료
            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("사용자 종료 요청(q)")
                break
        else:
            time.sleep(0.01)

except KeyboardInterrupt:
    print("키보드 인터럽트로 종료합니다.")
finally:
    try:
        cap.release()
    except Exception:
        pass
    try:
        ser.close()
    except Exception:
        pass
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass
    print("리소스 정리 완료")
