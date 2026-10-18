#!/usr/bin/env python3
"""Local phase-2 registry administration. Never provisions MQTT credentials."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

# Also support python3 -I -B /absolute/path/device_admin.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from device_registry import RegistryError, add_set, stage_device, activate_device, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path,
                        default=Path(__file__).resolve().parent / "data/carecall_events.db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    add = sub.add_parser("add-set")
    add.add_argument("--set-id", required=True)
    add.add_argument("--label", required=True)
    device = sub.add_parser("stage-device")
    device.add_argument("--device-id", required=True)
    device.add_argument("--set-id", required=True)
    activate = sub.add_parser("activate-device", help="DB admission only; MQTT credentials and ACL must be provisioned separately")
    activate.add_argument("--device-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "add-set":
            print("SET_RESULT=" + add_set(args.db, args.set_id, args.label))
        elif args.command == "stage-device":
            print("DEVICE_RESULT=" + stage_device(args.db, args.device_id, args.set_id))
        elif args.command == "activate-device":
            print("DEVICE_RESULT=" + activate_device(args.db, args.device_id))
        print(json.dumps(status(args.db), ensure_ascii=True, indent=2))
        return 0
    except RegistryError as exc:
        print("REGISTRY_STATUS=FAILED CODE=" + str(exc))
    except (sqlite3.Error, OSError) as exc:
        print("REGISTRY_STATUS=FAILED TYPE=" + type(exc).__name__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
