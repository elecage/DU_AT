"""SQLite persistence for validated CaregiverCall events."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import sqlite3

from message_validator import ValidatedCall
from device_registry import active_devices, device_set, inspect
from notification_store import OUTBOX_SCHEMA_SQL, enqueue_notifications
from confirmation_store import SCHEMA as CONFIRMATION_SCHEMA, register_call


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS call_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    schema_version INTEGER NOT NULL
        CHECK (schema_version = 1),
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    sequence INTEGER NOT NULL
        CHECK (sequence >= 1),
    uptime_ms INTEGER NOT NULL
        CHECK (uptime_ms >= 0),
    topic TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    last_received_at TEXT NOT NULL,
    delivery_count INTEGER NOT NULL DEFAULT 1
        CHECK (delivery_count >= 1)
) STRICT;

CREATE INDEX IF NOT EXISTS
    idx_call_events_device_received_at
ON call_events (device_id, received_at);
"""


class SaveOutcome(str, Enum):
    STORED = "stored"
    DUPLICATE = "duplicate"


class EventConflictError(RuntimeError):
    """Same event_id arrived with a different payload."""

    def __init__(self, event_id: str) -> None:
        super().__init__(
            "event_id already exists with a different payload: "
            f"{event_id}"
        )
        self.event_id = event_id


@dataclass(frozen=True, slots=True)
class SaveResult:
    outcome: SaveOutcome
    event_id: str
    delivery_count: int
    received_at: str
    last_received_at: str


@dataclass(frozen=True, slots=True)
class StoredEvent:
    event_id: str
    schema_version: int
    device_id: str
    event_type: str
    sequence: int
    uptime_ms: int
    topic: str
    payload_json: str
    received_at: str
    last_received_at: str
    delivery_count: int


def _utc_timestamp(
    value: datetime | None = None,
) -> str:
    timestamp = (
        value
        if value is not None
        else datetime.now(timezone.utc)
    )

    if (
        timestamp.tzinfo is None
        or timestamp.utcoffset() is None
    ):
        raise ValueError(
            "received_at must include timezone information"
        )

    return timestamp.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    )


class EventDatabase:
    def __init__(
        self,
        database_path: str | Path,
    ) -> None:
        self.database_path = (
            Path(database_path).expanduser().resolve()
        )

        if not self.database_path.is_file():
            raise RuntimeError("EXISTING_MIGRATED_DATABASE_REQUIRED")
        with closing(self._connect()) as connection:
            inspect(connection)

    def registered_devices(self):
        with closing(self._connect()) as connection:
            return active_devices(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=5.0,
            isolation_level=None,
        )

        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA busy_timeout = 5000"
        )
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )
        connection.execute(
            "PRAGMA synchronous = FULL"
        )

        return connection

    def save_call(
        self,
        call: ValidatedCall,
        received_at: datetime | None = None,
    ) -> SaveResult:
        timestamp = _utc_timestamp(received_at)

        with closing(self._connect()) as connection:
            try:
                connection.execute(
                    "BEGIN IMMEDIATE"
                )

                device_set(connection, call.device_id)
                existing = connection.execute(
                    """
                    SELECT
                        payload_json,
                        received_at,
                        last_received_at,
                        delivery_count
                    FROM call_events
                    WHERE event_id = ?
                    """,
                    (call.event_id,),
                ).fetchone()

                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO call_events (
                            event_id,
                            schema_version,
                            device_id,
                            event_type,
                            sequence,
                            uptime_ms,
                            topic,
                            payload_json,
                            received_at,
                            last_received_at,
                            delivery_count
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, 1
                        )
                        """,
                        (
                            call.event_id,
                            call.schema_version,
                            call.device_id,
                            call.event_type,
                            call.sequence,
                            call.uptime_ms,
                            call.topic,
                            call.payload_json,
                            timestamp,
                            timestamp,
                        ),
                    )

                    # 같은 트랜잭션: 새 호출과 승인된 수신자의 발송 작업을 함께 저장.
                    # 기존 호출/중복 전달에는 소급하여 알림을 만들지 않는다.
                    register_call(connection, call)
                    enqueue_notifications(connection, call.event_id, call.device_id, timestamp)
                    connection.commit()

                    return SaveResult(
                        outcome=SaveOutcome.STORED,
                        event_id=call.event_id,
                        delivery_count=1,
                        received_at=timestamp,
                        last_received_at=timestamp,
                    )

                if (
                    existing["payload_json"]
                    != call.payload_json
                ):
                    raise EventConflictError(
                        call.event_id
                    )

                connection.execute(
                    """
                    UPDATE call_events
                    SET
                        last_received_at = ?,
                        delivery_count =
                            delivery_count + 1
                    WHERE event_id = ?
                    """,
                    (
                        timestamp,
                        call.event_id,
                    ),
                )

                updated = connection.execute(
                    """
                    SELECT
                        received_at,
                        last_received_at,
                        delivery_count
                    FROM call_events
                    WHERE event_id = ?
                    """,
                    (call.event_id,),
                ).fetchone()

                if updated is None:
                    raise RuntimeError(
                        "saved event disappeared "
                        "during transaction"
                    )

                connection.commit()

                return SaveResult(
                    outcome=SaveOutcome.DUPLICATE,
                    event_id=call.event_id,
                    delivery_count=(
                        updated["delivery_count"]
                    ),
                    received_at=(
                        updated["received_at"]
                    ),
                    last_received_at=(
                        updated["last_received_at"]
                    ),
                )

            except Exception:
                connection.rollback()
                raise

    def get_event(
        self,
        event_id: str,
    ) -> StoredEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    event_id,
                    schema_version,
                    device_id,
                    event_type,
                    sequence,
                    uptime_ms,
                    topic,
                    payload_json,
                    received_at,
                    last_received_at,
                    delivery_count
                FROM call_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()

        if row is None:
            return None

        return StoredEvent(
            event_id=row["event_id"],
            schema_version=row["schema_version"],
            device_id=row["device_id"],
            event_type=row["event_type"],
            sequence=row["sequence"],
            uptime_ms=row["uptime_ms"],
            topic=row["topic"],
            payload_json=row["payload_json"],
            received_at=row["received_at"],
            last_received_at=row["last_received_at"],
            delivery_count=row["delivery_count"],
        )

    def count_events(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS event_count
                FROM call_events
                """
            ).fetchone()

        if row is None:
            raise RuntimeError(
                "failed to count stored events"
            )

        return int(row["event_count"])
