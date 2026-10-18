"""Configuration loading for the CaregiverCall Python Receiver."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path


MQTT_BROKER_HOST = "127.0.0.1"
MQTT_BROKER_PORT = 1883
MQTT_USERNAME = "carecall_receiver"
MQTT_CLIENT_ID = "carecall-python-receiver"
MQTT_TOPIC_FILTER = "carecall/v1/devices/+/call"
MQTT_QOS = 1
MQTT_KEEPALIVE_SECONDS = 60

MQTT_PASSWORD_ENV_NAME = (
    "CARECALL_RECEIVER_MQTT_PASSWORD"
)

DEFAULT_DATABASE_RELATIVE_PATH = (
    Path("data") / "carecall_events.db"
)


class ConfigurationError(RuntimeError):
    """Required Receiver configuration is unavailable."""


@dataclass(frozen=True, slots=True)
class ReceiverConfig:
    mqtt_broker_host: str
    mqtt_broker_port: int
    mqtt_username: str
    mqtt_password: str = field(repr=False)
    mqtt_client_id: str
    mqtt_topic_filter: str
    mqtt_qos: int
    mqtt_keepalive_seconds: int
    database_path: Path


def load_receiver_config(
    environ: Mapping[str, str] | None = None,
    project_directory: str | Path | None = None,
) -> ReceiverConfig:
    environment = (
        os.environ
        if environ is None
        else environ
    )

    password = environment.get(
        MQTT_PASSWORD_ENV_NAME
    )

    if password is None or password == "":
        raise ConfigurationError(
            "required environment variable is "
            "missing or empty: "
            f"{MQTT_PASSWORD_ENV_NAME}"
        )

    project_path = (
        Path(__file__).resolve().parent
        if project_directory is None
        else Path(
            project_directory
        ).expanduser().resolve()
    )

    return ReceiverConfig(
        mqtt_broker_host=MQTT_BROKER_HOST,
        mqtt_broker_port=MQTT_BROKER_PORT,
        mqtt_username=MQTT_USERNAME,
        mqtt_password=password,
        mqtt_client_id=MQTT_CLIENT_ID,
        mqtt_topic_filter=MQTT_TOPIC_FILTER,
        mqtt_qos=MQTT_QOS,
        mqtt_keepalive_seconds=(
            MQTT_KEEPALIVE_SECONDS
        ),
        database_path=(
            project_path
            / DEFAULT_DATABASE_RELATIVE_PATH
        ),
    )
