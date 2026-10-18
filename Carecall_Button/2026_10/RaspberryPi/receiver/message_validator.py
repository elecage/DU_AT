"""Validation for CaregiverCall MQTT call messages."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import json
import re
from typing import Any


EXPECTED_SCHEMA_VERSION = 1
EXPECTED_EVENT_TYPE = "care_call"
REGISTERED_DEVICE_IDS = frozenset({"button01"})

REQUIRED_FIELDS = (
    "schema_version",
    "event_id",
    "device_id",
    "event_type",
    "sequence",
    "uptime_ms",
)

TOPIC_PATTERN = re.compile(
    r"\Acarecall/v1/devices/([^/+#]+)/call\Z"
)


class MessageValidationError(ValueError):
    """Raised when an MQTT topic or payload is not a valid call event."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _DuplicateJsonKeyError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True, slots=True)
class ValidatedCall:
    schema_version: int
    event_id: str
    device_id: str
    event_type: str
    sequence: int
    uptime_ms: int
    topic: str
    payload_json: str


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)

        result[key] = value

    return result


def _reject_non_finite_number(value: str) -> None:
    raise ValueError(
        f"non-finite JSON number is not allowed: {value}"
    )


def _require_non_empty_string(
    document: dict[str, Any],
    field: str,
) -> str:
    value = document[field]

    if not isinstance(value, str):
        raise MessageValidationError(
            "invalid_field_type",
            f"{field} must be a string",
        )

    if not value.strip():
        raise MessageValidationError(
            "invalid_field_value",
            f"{field} must not be empty",
        )

    return value


def _require_integer(
    document: dict[str, Any],
    field: str,
) -> int:
    value = document[field]

    # Python에서는 bool이 int의 하위 타입이므로
    # isinstance(True, int)가 True가 된다.
    # 따라서 정확히 int 타입인 경우만 허용한다.
    if type(value) is not int:
        raise MessageValidationError(
            "invalid_field_type",
            f"{field} must be an integer and must not be boolean",
        )

    return value


def validate_call_message(
    topic: str,
    payload: bytes | bytearray,
    registered_device_ids: Collection[str] = REGISTERED_DEVICE_IDS,
) -> ValidatedCall:
    """Validate one MQTT call message and return normalized data."""

    if not isinstance(topic, str):
        raise MessageValidationError(
            "invalid_topic",
            "topic must be a string",
        )

    topic_match = TOPIC_PATTERN.fullmatch(topic)

    if topic_match is None:
        raise MessageValidationError(
            "invalid_topic",
            (
                "topic must match "
                "carecall/v1/devices/{device_id}/call"
            ),
        )

    topic_device_id = topic_match.group(1)

    if not isinstance(payload, (bytes, bytearray)):
        raise MessageValidationError(
            "invalid_payload_type",
            "payload must be bytes or bytearray",
        )

    try:
        payload_text = bytes(payload).decode(
            "utf-8",
            errors="strict",
        )
    except UnicodeDecodeError as exc:
        raise MessageValidationError(
            "invalid_utf8",
            f"payload is not valid UTF-8 at byte {exc.start}",
        ) from exc

    try:
        document = json.loads(
            payload_text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_number,
        )
    except _DuplicateJsonKeyError as exc:
        raise MessageValidationError(
            "duplicate_json_key",
            f"payload contains duplicate JSON key: {exc.key}",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise MessageValidationError(
            "invalid_json",
            f"payload is not valid JSON: {exc}",
        ) from exc

    if not isinstance(document, dict):
        raise MessageValidationError(
            "invalid_json_root",
            "payload JSON root must be an object",
        )

    missing_fields = [
        field
        for field in REQUIRED_FIELDS
        if field not in document
    ]

    if missing_fields:
        raise MessageValidationError(
            "missing_field",
            (
                "missing required field(s): "
                + ", ".join(missing_fields)
            ),
        )

    schema_version = _require_integer(
        document,
        "schema_version",
    )
    event_id = _require_non_empty_string(
        document,
        "event_id",
    )
    device_id = _require_non_empty_string(
        document,
        "device_id",
    )
    event_type = _require_non_empty_string(
        document,
        "event_type",
    )
    sequence = _require_integer(
        document,
        "sequence",
    )
    uptime_ms = _require_integer(
        document,
        "uptime_ms",
    )

    if schema_version != EXPECTED_SCHEMA_VERSION:
        raise MessageValidationError(
            "unsupported_schema_version",
            (
                "schema_version must be "
                f"{EXPECTED_SCHEMA_VERSION}"
            ),
        )

    if event_type != EXPECTED_EVENT_TYPE:
        raise MessageValidationError(
            "invalid_event_type",
            f"event_type must be {EXPECTED_EVENT_TYPE}",
        )

    if sequence < 1:
        raise MessageValidationError(
            "invalid_field_value",
            "sequence must be at least 1",
        )

    if uptime_ms < 0:
        raise MessageValidationError(
            "invalid_field_value",
            "uptime_ms must be at least 0",
        )

    if topic_device_id != device_id:
        raise MessageValidationError(
            "device_id_mismatch",
            (
                "topic device_id does not match "
                "payload device_id"
            ),
        )

    if device_id not in registered_device_ids:
        raise MessageValidationError(
            "unregistered_device",
            f"device_id is not registered: {device_id}",
        )

    canonical_payload_json = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    return ValidatedCall(
        schema_version=schema_version,
        event_id=event_id,
        device_id=device_id,
        event_type=event_type,
        sequence=sequence,
        uptime_ms=uptime_ms,
        topic=topic,
        payload_json=canonical_payload_json,
    )
