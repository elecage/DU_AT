#!/usr/bin/env python3
"""Start the ONNX camera detector while Arduino reports reverse movement.

Protocol expected from the Arduino at 115200 baud:
    STATE,REVERSE,<sequence>,<acceleration>
    STATE,FORWARD,<sequence>,<acceleration>

Every REVERSE heartbeat refreshes the hold deadline. After the Arduino changes
to FORWARD, the detector remains active until the deadline expires. A new
REVERSE heartbeat during that period restarts the full hold interval.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import serial
from serial import SerialException
from serial.tools import list_ports


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DETECTOR = BASE_DIR / "run_foot_onnx_usb_pi5.py"


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Arduino reverse signal to ONNX camera controller"
    )
    parser.add_argument(
        "--port",
        default="auto",
        help="Arduino serial device, for example /dev/ttyACM0; default: auto",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--hold-seconds", type=float, default=10.0)
    parser.add_argument("--detector", type=Path, default=DEFAULT_DETECTOR)
    parser.add_argument("--conf", type=float, default=0.50)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--camera", default="/dev/video0")
    parser.add_argument(
        "--camera-format", choices=("MJPG", "YUYV"), default="MJPG"
    )
    parser.add_argument("--display-width", type=int, default=800)
    parser.add_argument("--display-height", type=int, default=480)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--restart-cooldown",
        type=float,
        default=3.0,
        help="minimum seconds between detector start attempts",
    )
    return parser.parse_args()


def port_score(port) -> int:
    text = " ".join(
        value
        for value in (
            port.device,
            port.description,
            port.manufacturer,
            port.product,
            port.hwid,
        )
        if value
    ).lower()
    score = 0
    if "arduino" in text:
        score += 100
    if "ttyacm" in text:
        score += 60
    if "ch340" in text or "wch" in text:
        score += 50
    if "usb serial" in text or "ttyusb" in text:
        score += 30
    return score


def find_arduino_port() -> str | None:
    ports = list(list_ports.comports())
    if not ports:
        return None
    ranked = sorted(ports, key=lambda item: (port_score(item), item.device), reverse=True)
    best = ranked[0]
    if port_score(best) <= 0:
        return None
    return best.device


class DetectorProcess:
    def __init__(self, command: list[str], cwd: Path, cooldown: float):
        self.command = command
        self.cwd = cwd
        self.cooldown = cooldown
        self.process: subprocess.Popen | None = None
        self.last_start_attempt = 0.0
        self.last_reported_exit_pid: int | None = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def report_exit(self) -> None:
        if self.process is None or self.process.poll() is None:
            return
        if self.last_reported_exit_pid != self.process.pid:
            log(
                f"ONNX detector exited: pid={self.process.pid}, "
                f"code={self.process.returncode}"
            )
            self.last_reported_exit_pid = self.process.pid

    def start(self, now: float) -> bool:
        self.report_exit()
        if self.is_running():
            return True
        if now - self.last_start_attempt < self.cooldown:
            return False

        self.last_start_attempt = now
        environment = os.environ.copy()
        environment.setdefault("DISPLAY", ":0")
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=environment,
                start_new_session=True,
            )
        except OSError as exc:
            log(f"Could not start ONNX detector: {exc}")
            self.process = None
            return False

        self.last_reported_exit_pid = None
        log(f"ONNX detector started: pid={self.process.pid}")
        return True

    def stop(self) -> None:
        if not self.is_running():
            self.report_exit()
            self.process = None
            return

        assert self.process is not None
        pid = self.process.pid
        log(f"Stopping ONNX detector: pid={pid}")
        try:
            os.killpg(pid, signal.SIGINT)
            self.process.wait(timeout=8.0)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            log("Detector did not stop after SIGINT; sending SIGTERM")
            try:
                os.killpg(pid, signal.SIGTERM)
                self.process.wait(timeout=3.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                log("Detector did not stop after SIGTERM; sending SIGKILL")
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2.0)
        finally:
            self.process = None


def build_detector_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(args.detector),
        "--conf",
        str(args.conf),
        "--iou",
        str(args.iou),
        "--threads",
        str(args.threads),
        "--width",
        str(args.width),
        "--height",
        str(args.height),
        "--fps",
        str(args.fps),
        "--camera",
        args.camera,
        "--camera-format",
        args.camera_format,
        "--display-width",
        str(args.display_width),
        "--display-height",
        str(args.display_height),
        "--log-interval",
        "0",
    ]
    if args.headless:
        command.append("--headless")
    return command


def open_serial(port_name: str, baud: int) -> serial.Serial:
    connection = serial.Serial(
        port=port_name,
        baudrate=baud,
        timeout=0.25,
        write_timeout=1.0,
    )
    # Opening an UNO serial port commonly resets the board. Allow setup and
    # stationary calibration to complete before treating its state as valid.
    time.sleep(3.8)
    connection.reset_input_buffer()
    return connection


def main() -> None:
    args = parse_args()
    if args.hold_seconds <= 0:
        raise SystemExit("--hold-seconds must be greater than zero")
    args.detector = args.detector.expanduser().resolve()
    if not args.detector.is_file():
        raise SystemExit(f"Detector script not found: {args.detector}")
    model_path = args.detector.parent / "best.onnx"
    if not model_path.is_file():
        raise SystemExit(f"Model must be next to the detector script: {model_path}")

    shutdown_requested = False

    def request_shutdown(signum, _frame) -> None:
        nonlocal shutdown_requested
        log(f"Shutdown requested by signal {signum}")
        shutdown_requested = True

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    detector = DetectorProcess(
        command=build_detector_command(args),
        cwd=args.detector.parent,
        cooldown=args.restart_cooldown,
    )
    serial_connection: serial.Serial | None = None
    active_port: str | None = None
    last_port_log = 0.0
    last_motion_state = "UNKNOWN"
    camera_deadline: float | None = None

    log(
        f"Controller ready: port={args.port}, hold={args.hold_seconds:.1f}s, "
        f"detector={args.detector.name}"
    )

    try:
        while not shutdown_requested:
            now = time.monotonic()
            detector.report_exit()

            if camera_deadline is not None and now >= camera_deadline:
                detector.stop()
                camera_deadline = None
                log("Camera hold timer expired")

            if serial_connection is None:
                active_port = args.port if args.port != "auto" else find_arduino_port()
                if active_port is None:
                    if now - last_port_log >= 5.0:
                        log("Arduino serial port not found; retrying")
                        last_port_log = now
                    time.sleep(0.5)
                    continue
                try:
                    log(f"Opening Arduino serial port: {active_port}")
                    serial_connection = open_serial(active_port, args.baud)
                    log(f"Arduino connected: {active_port}")
                except (OSError, SerialException) as exc:
                    log(f"Serial open failed ({active_port}): {exc}")
                    serial_connection = None
                    active_port = None
                    time.sleep(1.0)
                    continue

            try:
                raw_line = serial_connection.readline()
            except (OSError, SerialException) as exc:
                log(f"Arduino disconnected ({active_port}): {exc}")
                try:
                    serial_connection.close()
                except Exception:
                    pass
                serial_connection = None
                active_port = None
                last_motion_state = "UNKNOWN"
                continue

            if not raw_line:
                continue

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            fields = [field.strip() for field in line.split(",")]
            if fields[0] in {"INFO", "READY", "ERROR"}:
                log(f"Arduino: {line}")
                continue
            if len(fields) < 2 or fields[0] != "STATE":
                log(f"Ignored serial message: {line}")
                continue

            motion_state = fields[1].upper()
            if motion_state not in {"REVERSE", "FORWARD"}:
                log(f"Ignored motion state: {line}")
                continue

            state_changed = motion_state != last_motion_state
            last_motion_state = motion_state
            now = time.monotonic()

            if motion_state == "REVERSE":
                camera_deadline = now + args.hold_seconds
                detector.start(now)
                if state_changed:
                    log(
                        "Reverse detected: camera ON; "
                        f"hold timer refreshed to {args.hold_seconds:.1f}s"
                    )
            elif state_changed:
                remaining = (
                    max(0.0, camera_deadline - now)
                    if camera_deadline is not None
                    else 0.0
                )
                log(f"Forward detected: camera remains ON for {remaining:.1f}s")
    finally:
        if serial_connection is not None:
            serial_connection.close()
        detector.stop()
        log("Controller stopped")


if __name__ == "__main__":
    main()
