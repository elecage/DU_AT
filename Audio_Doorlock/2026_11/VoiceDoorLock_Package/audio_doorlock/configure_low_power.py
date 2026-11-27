#!/usr/bin/env python3
"""Patch only the low-power settings in an existing ESP32-C3 sdkconfig.

Run from the existing ESP-IDF project, then run idf.py reconfigure and --check.
No firmware, NVS, source files, or managed components are erased by this script.
"""

import argparse
from datetime import datetime
from pathlib import Path
import re
import sys


REQUIRED = {
    "CONFIG_PM_ENABLE": "y",
    "CONFIG_FREERTOS_USE_TICKLESS_IDLE": "y",
    "CONFIG_ESP_PHY_MAX_WIFI_TX_POWER": "10",
    "CONFIG_USE_BLE_ONLY_FOR_COMMISSIONING": "y",
}
USB_GUARD = "CONFIG_USJ_NO_AUTO_LS_ON_CONNECTION"


def settings_in(text):
    values = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Z0-9_]+)=(.*)", line)
        if match:
            values[match[1]] = match[2]
            continue
        match = re.fullmatch(r"# (CONFIG_[A-Z0-9_]+) is not set", line)
        if match:
            values[match[1]] = "n"
    return values


def desired_settings(current):
    desired = dict(REQUIRED)
    # Leave the USB module's existing on/off setting alone. When enabled (or
    # using the IDF default), protect native USB monitoring from Light-sleep.
    if current.get("CONFIG_USJ_ENABLE_USB_SERIAL_JTAG") != "n":
        desired[USB_GUARD] = "y"
    return desired


def patch_settings(text, desired, newline):
    remaining = dict(desired)
    result = []
    for line in text.splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Z0-9_]+)=.*", line)
        if not match:
            match = re.fullmatch(r"# (CONFIG_[A-Z0-9_]+) is not set", line)
        key = match[1] if match else None
        if key in desired:
            if key in remaining:
                result.append(f"{key}={remaining.pop(key)}")
            # Drop duplicate declarations of the settings being changed.
        else:
            result.append(line)
    result.extend(f"{key}={value}" for key, value in remaining.items())
    return newline.join(result) + newline


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true", help="Verify only; do not edit")
    args = parser.parse_args()
    project = args.project.resolve()
    config = project / "sdkconfig"
    if not config.is_file() or not (project / "CMakeLists.txt").is_file():
        raise ValueError("Run in the existing project containing sdkconfig and CMakeLists.txt.")
    original = config.read_bytes()
    text = original.decode("utf-8-sig")
    current = settings_in(text)
    if current.get("CONFIG_IDF_TARGET") != '"esp32c3"':
        raise ValueError("Expected CONFIG_IDF_TARGET=\"esp32c3\". No files changed.")
    desired = desired_settings(current)
    if args.check:
        mismatches = [key for key, value in desired.items() if current.get(key) != value]
        if mismatches:
            for key in mismatches:
                print(f"FAIL: {key}: expected {desired[key]}, found {current.get(key, 'missing')}")
            print("Recheck these options in idf.py menuconfig before flashing.")
            return 1
        print("PASS: low-power build settings are enabled.")
        print("Hardware sleep still depends on Wi-Fi/BLE/USB driver locks.")
        return 0

    newline = "\r\n" if b"\r\n" in original else "\n"
    updated = patch_settings(text, desired, newline).encode("utf-8")
    if original.startswith(b"\xef\xbb\xbf"):
        updated = b"\xef\xbb\xbf" + updated
    if updated != original:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = project / f"sdkconfig.before-low-power-{timestamp}"
        # Exclusive creation protects any previous backup.
        with backup.open("xb") as stream:
            stream.write(original)
        config.write_bytes(updated)
        print(f"Original sdkconfig backed up to: {backup}")
    else:
        print("Settings already match; no files changed.")
    for key, value in desired.items():
        print(f"{key}={value}")
    print("Next: idf.py reconfigure")
    print("Then: python configure_low_power.py --check")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
