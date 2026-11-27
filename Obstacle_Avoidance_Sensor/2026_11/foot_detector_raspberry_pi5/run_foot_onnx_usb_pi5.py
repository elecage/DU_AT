#!/usr/bin/env python3
"""Raspberry Pi 5 + USB camera + ONNX Runtime foot detector.

Put this file next to ``best.onnx`` and run it with Python 3.
The model is a one-class Ultralytics detector whose output does not include NMS.
The annotated feed is shown full-screen on an 800x480 attached display.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

# When started from SSH or a service, target the local Raspberry Pi desktop.
# An existing DISPLAY value always takes priority.
os.environ.setdefault("DISPLAY", ":0")

import cv2
import numpy as np

try:
    import onnxruntime as ort
except ImportError as exc:
    raise SystemExit(
        "onnxruntime is not installed. Run: python3 -m pip install onnxruntime"
    ) from exc

MODEL_PATH = Path(__file__).resolve().parent / "best.onnx"
CLASS_NAME = "Foot"
WINDOW_NAME = "Foot detector"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Foot detection with best.onnx")
    parser.add_argument("--conf", type=float, default=0.50, help="confidence threshold")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU threshold")
    parser.add_argument("--width", type=int, default=1280, help="camera width")
    parser.add_argument("--height", type=int, default=720, help="camera height")
    parser.add_argument("--fps", type=float, default=30.0, help="requested camera FPS")
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="ONNX CPU threads; 0 lets ONNX Runtime use all physical cores",
    )
    parser.add_argument(
        "--camera",
        default="/dev/video0",
        help="USB camera device such as /dev/video0, or a numeric camera index",
    )
    parser.add_argument(
        "--camera-format",
        choices=("MJPG", "YUYV"),
        default="MJPG",
        help="requested V4L2 pixel format; MJPG usually reduces USB bandwidth",
    )
    parser.add_argument("--display-width", type=int, default=800)
    parser.add_argument("--display-height", type=int, default=480)
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="use an 800x480 window instead of full-screen output",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="disable the attached display (benchmark/debug only)",
    )
    parser.add_argument(
        "--log-interval",
        type=float,
        default=0.0,
        help="terminal log interval in seconds; 0 disables detection logs",
    )
    parser.add_argument("--save", type=Path, help="optional annotated AVI output path")
    return parser.parse_args()


class LatestFrameCamera:
    """Capture continuously and expose only the newest frame.

    A one-frame latest-value buffer prevents camera frames from queueing while
    inference is busy. Camera capture can overlap CPU preprocessing/inference.
    """

    def __init__(self, camera: cv2.VideoCapture):
        self.camera = camera
        self._condition = threading.Condition()
        self._frame = None
        self._sequence = 0
        self._error: BaseException | None = None
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="usb-camera-capture",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _capture_loop(self) -> None:
        try:
            consecutive_failures = 0
            while not self._stop_event.is_set():
                ok, frame = self.camera.read()
                if not ok or frame is None:
                    consecutive_failures += 1
                    if consecutive_failures >= 20:
                        raise RuntimeError("USB camera returned 20 invalid frames")
                    time.sleep(0.05)
                    continue
                consecutive_failures = 0
                with self._condition:
                    self._frame = frame
                    self._sequence += 1
                    self._condition.notify_all()
        except BaseException as exc:
            with self._condition:
                self._error = exc
                self._condition.notify_all()

    def read(self, after_sequence: int, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            # The first call used to return immediately with ``None`` because
            # sequence 0 was considered newer than the caller's initial -1.
            # Always wait until the capture thread has stored a real frame.
            while (
                (self._frame is None or self._sequence <= after_sequence)
                and self._error is None
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out while waiting for a camera frame")
                self._condition.wait(remaining)
            if self._error is not None:
                raise RuntimeError("Camera capture thread failed") from self._error
            if self._frame is None:
                raise RuntimeError("Camera capture returned no frame")
            return self._frame, self._sequence

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self.camera.release()


def parse_camera_source(value: str):
    """Accept /dev/videoN paths and plain numeric OpenCV camera indices."""
    return int(value) if value.isdecimal() else value


def open_usb_camera(args: argparse.Namespace) -> cv2.VideoCapture:
    source = parse_camera_source(args.camera)
    camera = cv2.VideoCapture(source, cv2.CAP_V4L2)
    if not camera.isOpened():
        camera.release()
        raise SystemExit(
            f"Could not open USB camera {args.camera}. Check v4l2-ctl --list-devices "
            "and the user's video group membership."
        )

    fourcc_name = "MJPG" if args.camera_format == "MJPG" else "YUYV"
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc_name))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    camera.set(cv2.CAP_PROP_FPS, args.fps)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # Read one frame now so unsupported modes fail before the inference loop.
    ok, frame = camera.read()
    if not ok or frame is None:
        camera.release()
        raise SystemExit(
            f"USB camera {args.camera} opened but did not return a frame. "
            "Choose a supported size/format shown by v4l2-ctl."
        )
    return camera


def letterbox(image: np.ndarray, width: int, height: int):
    """Resize without distortion and pad with gray pixels."""
    src_h, src_w = image.shape[:2]
    ratio = min(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * ratio)))
    new_h = max(1, int(round(src_h * ratio)))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    pad_x = (width - new_w) // 2
    pad_y = (height - new_h) // 2
    canvas = np.full((height, width, 3), 114, dtype=np.uint8)
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, ratio, pad_x, pad_y


def decode_yolo(
    output: np.ndarray,
    original_shape: tuple[int, int],
    input_size: tuple[int, int],
    ratio: float,
    pad_x: int,
    pad_y: int,
    conf_threshold: float,
    iou_threshold: float,
):
    """Decode [1, 5, 2100] YOLO output and apply confidence filtering + NMS."""
    predictions = np.asarray(output).squeeze(0)
    if predictions.ndim != 2:
        raise RuntimeError(f"Unexpected model output shape: {output.shape}")

    # The supplied model is [5, 2100]. Also accept [2100, 5].
    if predictions.shape[0] < predictions.shape[1]:
        predictions = predictions.T
    if predictions.shape[1] < 5:
        raise RuntimeError(f"Expected at least 5 output values, got {predictions.shape}")

    boxes_xywh = predictions[:, :4].astype(np.float32, copy=True)
    scores = predictions[:, 4].astype(np.float32, copy=False)
    keep = scores >= conf_threshold
    boxes_xywh = boxes_xywh[keep]
    scores = scores[keep]
    if scores.size == 0:
        return []

    input_w, input_h = input_size
    # This exported model returns normalized xywh. The fallback also supports
    # a future export that returns input-pixel coordinates.
    if float(np.nanmax(np.abs(boxes_xywh))) <= 2.5:
        boxes_xywh[:, [0, 2]] *= input_w
        boxes_xywh[:, [1, 3]] *= input_h

    boxes_xyxy = np.empty_like(boxes_xywh)
    boxes_xyxy[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2
    boxes_xyxy[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2
    boxes_xyxy[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2
    boxes_xyxy[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2

    nms_boxes = np.column_stack(
        (
            boxes_xyxy[:, 0],
            boxes_xyxy[:, 1],
            boxes_xyxy[:, 2] - boxes_xyxy[:, 0],
            boxes_xyxy[:, 3] - boxes_xyxy[:, 1],
        )
    ).tolist()
    indices = cv2.dnn.NMSBoxes(
        nms_boxes, scores.tolist(), conf_threshold, iou_threshold
    )
    indices = np.asarray(indices).reshape(-1)
    if indices.size == 0:
        return []

    frame_h, frame_w = original_shape
    detections = []
    for index in indices:
        box = boxes_xyxy[int(index)].copy()
        box[[0, 2]] = (box[[0, 2]] - pad_x) / ratio
        box[[1, 3]] = (box[[1, 3]] - pad_y) / ratio
        box[[0, 2]] = np.clip(box[[0, 2]], 0, frame_w - 1)
        box[[1, 3]] = np.clip(box[[1, 3]], 0, frame_h - 1)
        x1, y1, x2, y2 = np.rint(box).astype(int)
        if x2 > x1 and y2 > y1:
            detections.append((x1, y1, x2, y2, float(scores[int(index)])))
    return detections


def draw_detections(
    frame: np.ndarray,
    detections,
) -> None:
    """Draw detection boxes and confidence labels without a status overlay."""
    for x1, y1, x2, y2, score in detections:
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"{CLASS_NAME} {score:.2f}"
        (text_w, text_h), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2
        )
        label_y = max(text_h + 8, y1)
        cv2.rectangle(
            frame,
            (x1, label_y - text_h - 8),
            (x1 + text_w + 8, label_y),
            (0, 255, 0),
            -1,
        )
        cv2.putText(
            frame,
            label,
            (x1 + 4, label_y - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )


def fit_to_display(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """Fit a frame to the display without stretching its aspect ratio."""
    frame_h, frame_w = frame.shape[:2]
    ratio = min(width / frame_w, height / frame_h)
    resized_w = max(1, int(round(frame_w * ratio)))
    resized_h = max(1, int(round(frame_h * ratio)))
    resized = cv2.resize(
        frame,
        (resized_w, resized_h),
        interpolation=cv2.INTER_AREA,
    )
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    left = (width - resized_w) // 2
    top = (height - resized_h) // 2
    canvas[top : top + resized_h, left : left + resized_w] = resized
    return canvas


def initialize_display(width: int, height: int, windowed: bool) -> None:
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.moveWindow(WINDOW_NAME, 0, 0)
    cv2.resizeWindow(WINDOW_NAME, width, height)
    if not windowed:
        cv2.setWindowProperty(
            WINDOW_NAME,
            cv2.WND_PROP_FULLSCREEN,
            cv2.WINDOW_FULLSCREEN,
        )


def main() -> None:
    args = parse_args()
    if not 0.0 <= args.conf <= 1.0:
        raise SystemExit("--conf must be between 0 and 1")
    if args.threads < 0:
        raise SystemExit("--threads must be 0 or a positive integer")
    if args.display_width <= 0 or args.display_height <= 0:
        raise SystemExit("Display width and height must be positive")
    if not MODEL_PATH.is_file():
        raise SystemExit(f"Model not found: {MODEL_PATH}")

    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "1")
    session = ort.InferenceSession(
        str(MODEL_PATH), sess_options=options, providers=["CPUExecutionProvider"]
    )
    model_input = session.get_inputs()[0]
    input_name = model_input.name
    input_shape = model_input.shape
    if len(input_shape) != 4 or not isinstance(input_shape[2], int):
        raise RuntimeError(f"Unexpected ONNX input shape: {input_shape}")
    input_h, input_w = int(input_shape[2]), int(input_shape[3])

    camera = open_usb_camera(args)

    if not args.headless:
        try:
            initialize_display(
                args.display_width,
                args.display_height,
                args.windowed,
            )
        except cv2.error as exc:
            camera.release()
            raise SystemExit(
                "Could not open the attached display. Start the Raspberry Pi "
                "desktop and run with DISPLAY=:0, or use --headless."
            ) from exc

    capture = LatestFrameCamera(camera)
    capture.start()
    writer = None
    last_sequence = -1
    last_log_time = 0.0
    try:
        while True:
            # OpenCV VideoCapture returns BGR frames.
            frame, last_sequence = capture.read(last_sequence)
            prepared, ratio, pad_x, pad_y = letterbox(frame, input_w, input_h)
            tensor = cv2.dnn.blobFromImage(
                prepared,
                scalefactor=1.0 / 255.0,
                size=(input_w, input_h),
                mean=(0.0, 0.0, 0.0),
                swapRB=True,
                crop=False,
            )

            output = session.run(None, {input_name: tensor})[0]

            detections = decode_yolo(
                output,
                frame.shape[:2],
                (input_w, input_h),
                ratio,
                pad_x,
                pad_y,
                args.conf,
                args.iou,
            )
            now = time.perf_counter()
            draw_detections(frame, detections)

            if (
                args.log_interval > 0.0
                and detections
                and now - last_log_time >= args.log_interval
            ):
                print(
                    " | ".join(
                        f"{CLASS_NAME} conf={d[4]:.3f} box={d[:4]}" for d in detections
                    ),
                    flush=False,
                )
                last_log_time = now

            if args.save and writer is None:
                args.save.parent.mkdir(parents=True, exist_ok=True)
                codec = cv2.VideoWriter_fourcc(*"MJPG")
                writer = cv2.VideoWriter(
                    str(args.save), codec, args.fps, (frame.shape[1], frame.shape[0])
                )
                if not writer.isOpened():
                    raise RuntimeError(f"Could not open video output: {args.save}")
            if writer is not None:
                writer.write(frame)

            if not args.headless:
                display_frame = fit_to_display(
                    frame,
                    args.display_width,
                    args.display_height,
                )
                cv2.imshow(WINDOW_NAME, display_frame)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
