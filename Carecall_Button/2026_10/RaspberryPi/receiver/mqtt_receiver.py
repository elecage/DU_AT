"""MQTT Receiver for CaregiverCall call events."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import sys
import threading

import paho.mqtt.client as mqtt
from confirmation_store import ConfirmationStore
from confirmation_delivery import pump_confirmations

from event_database import (
    EventConflictError,
    EventDatabase,
    SaveOutcome,
)
from message_validator import (
    MessageValidationError,
    validate_call_message,
)
from receiver_config import (
    ConfigurationError,
    ReceiverConfig,
    load_receiver_config,
)


LOGGER = logging.getLogger("carecall_receiver")


class MqttProtocolAckError(RuntimeError):
    """Paho could not queue an MQTT protocol ACK."""


class ApplicationAckPublishError(RuntimeError):
    """An Application ACK could not be published."""


@dataclass(slots=True)
class ReceiverRuntime:
    config: ReceiverConfig
    database: EventDatabase
    pending_application_acks: dict[int, str] = field(
        default_factory=dict
    )


def _require_runtime(
    userdata: object,
) -> ReceiverRuntime:
    if not isinstance(
        userdata,
        ReceiverRuntime,
    ):
        raise RuntimeError(
            "MQTT callback userdata is not ReceiverRuntime"
        )

    return userdata


def _ack_message(
    client: mqtt.Client,
    message: mqtt.MQTTMessage,
) -> None:
    if message.qos == 0:
        return

    result = client.ack(
        message.mid,
        message.qos,
    )

    if result != mqtt.MQTT_ERR_SUCCESS:
        raise MqttProtocolAckError(
            "failed to queue MQTT ACK: "
            f"mid={message.mid} "
            f"qos={message.qos} "
            f"result={result}"
        )


def _envelope_rejection_reason(
    message: mqtt.MQTTMessage,
) -> str | None:
    if message.qos != 1:
        return (
            f"unexpected QoS: {message.qos}; "
            "expected QoS 1"
        )

    if bool(message.retain):
        return (
            "retained call messages are not accepted"
        )

    return None


def _queue_application_ack(
    client: mqtt.Client,
    runtime: ReceiverRuntime,
    event_id: str,
    device_id: str,
) -> int:
    topic = (
        f"carecall/v1/devices/{device_id}/ack"
    )

    payload = json.dumps(
        {
            "event_id": event_id,
            "device_id": device_id,
            "status": "stored",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    publish_info = client.publish(
        topic,
        payload=payload,
        qos=1,
        retain=False,
    )

    if (
        publish_info.rc
        != mqtt.MQTT_ERR_SUCCESS
    ):
        raise ApplicationAckPublishError(
            "failed to queue Application ACK: "
            f"event_id={event_id} "
            f"result={publish_info.rc}"
        )

    runtime.pending_application_acks[
        publish_info.mid
    ] = event_id

    LOGGER.info(
        "Application ACK queued "
        "event_id=%s topic=%s mid=%s "
        "qos=1 retain=false",
        event_id,
        topic,
        publish_info.mid,
    )

    return publish_info.mid


def process_message(
    client: mqtt.Client,
    runtime: ReceiverRuntime,
    message: mqtt.MQTTMessage,
) -> None:
    LOGGER.info(
        "MQTT message received "
        "topic=%s mid=%s qos=%s "
        "dup=%s retain=%s",
        message.topic,
        message.mid,
        message.qos,
        bool(message.dup),
        bool(message.retain),
    )

    envelope_error = (
        _envelope_rejection_reason(message)
    )

    if envelope_error is not None:
        LOGGER.warning(
            "MQTT message rejected "
            "code=invalid_envelope "
            "mid=%s reason=%s",
            message.mid,
            envelope_error,
        )

        _ack_message(
            client,
            message,
        )
        return

    try:
        handled = ConfirmationStore(runtime.config.database_path).receive_device_result(
            message.topic, message.payload)
    except ValueError:
        LOGGER.warning('Invalid guardian confirmation report')
        _ack_message(client, message)
        return
    if handled:
        _ack_message(client, message)
        LOGGER.info('Device confirmation result stored')
        return

    try:
        call = validate_call_message(
            message.topic,
            message.payload,
            registered_device_ids=runtime.database.registered_devices(),
        )

    except MessageValidationError as exc:
        LOGGER.warning(
            "MQTT message rejected "
            "code=%s mid=%s reason=%s",
            exc.code,
            message.mid,
            exc,
        )

        # 잘못된 메시지의 반복 전달을 막는다.
        _ack_message(
            client,
            message,
        )
        return

    try:
        save_result = (
            runtime.database.save_call(call)
        )

    except EventConflictError as exc:
        LOGGER.error(
            "MQTT message rejected "
            "code=event_conflict "
            "mid=%s event_id=%s reason=%s",
            message.mid,
            call.event_id,
            exc,
        )

        # 충돌 메시지에는 Application ACK를 보내지 않는다.
        _ack_message(
            client,
            message,
        )
        return

    except Exception:
        LOGGER.exception(
            "Database processing failed; "
            "MQTT message left unacknowledged "
            "mid=%s event_id=%s",
            message.mid,
            call.event_id,
        )
        raise

    try:
        # SQLite COMMIT 완료 후 Application ACK를 큐에 넣는다.
        application_ack_mid = (
            _queue_application_ack(
                client,
                runtime,
                event_id=call.event_id,
                device_id=call.device_id,
            )
        )

    except Exception:
        LOGGER.exception(
            "Application ACK queue failed; "
            "incoming MQTT message left "
            "unacknowledged "
            "mid=%s event_id=%s",
            message.mid,
            call.event_id,
        )
        raise

    # Application ACK 큐 성공 후 수신 PUBLISH를 확인한다.
    _ack_message(
        client,
        message,
    )

    log_message = (
        "Call event stored"
        if save_result.outcome
        is SaveOutcome.STORED
        else "Duplicate call event recorded"
    )

    LOGGER.info(
        "%s event_id=%s delivery_count=%s "
        "mid=%s mqtt_ack=sent "
        "application_ack_mid=%s",
        log_message,
        save_result.event_id,
        save_result.delivery_count,
        message.mid,
        application_ack_mid,
    )


def on_connect(
    client: mqtt.Client,
    userdata: object,
    connect_flags: mqtt.ConnectFlags,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties | None,
) -> None:
    del properties

    runtime = _require_runtime(userdata)

    if reason_code != 0:
        LOGGER.error(
            "MQTT connection rejected reason=%s",
            reason_code,
        )
        return

    result, message_id = client.subscribe(
        runtime.config.mqtt_topic_filter,
        qos=runtime.config.mqtt_qos,
    )

    if result != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError(
            "failed to queue MQTT subscription: "
            f"result={result}"
        )

    LOGGER.info(
        "MQTT connected broker=%s:%s "
        "session_present=%s",
        runtime.config.mqtt_broker_host,
        runtime.config.mqtt_broker_port,
        bool(connect_flags.session_present),
    )

    LOGGER.info(
        "MQTT subscription queued "
        "topic=%s qos=%s mid=%s",
        runtime.config.mqtt_topic_filter,
        runtime.config.mqtt_qos,
        message_id,
    )


def on_connect_fail(
    client: mqtt.Client,
    userdata: object,
) -> None:
    del client, userdata

    LOGGER.warning(
        "MQTT connection attempt failed; retrying"
    )


def on_disconnect(
    client: mqtt.Client,
    userdata: object,
    disconnect_flags: mqtt.DisconnectFlags,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties | None,
) -> None:
    del (
        client,
        userdata,
        disconnect_flags,
        properties,
    )

    if reason_code == 0:
        LOGGER.info(
            "MQTT disconnected cleanly"
        )
    else:
        LOGGER.warning(
            "MQTT disconnected unexpectedly "
            "reason=%s; retrying",
            reason_code,
        )


def on_subscribe(
    client: mqtt.Client,
    userdata: object,
    message_id: int,
    reason_code_list: list[mqtt.ReasonCode],
    properties: mqtt.Properties | None,
) -> None:
    del client, userdata, properties

    failed_codes = [
        code
        for code in reason_code_list
        if code >= 128
    ]

    if failed_codes:
        raise RuntimeError(
            "MQTT subscription rejected: "
            f"mid={message_id} "
            f"reason_codes={failed_codes}"
        )

    LOGGER.info(
        "MQTT SUBACK received "
        "mid=%s granted=%s",
        message_id,
        [
            str(code)
            for code in reason_code_list
        ],
    )


def on_publish(
    client: mqtt.Client,
    userdata: object,
    message_id: int,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties | None,
) -> None:
    del client, properties

    runtime = _require_runtime(userdata)

    event_id = (
        runtime.pending_application_acks.pop(
            message_id,
            None,
        )
    )

    if event_id is None:
        # Guardian confirmation publishes are tracked by a device result in DB,
        # not broker PUBACK. Avoid a cross-thread publish/MID callback race.
        LOGGER.debug(
            "PUBACK received for untracked "
            "outgoing message "
            "mid=%s reason=%s",
            message_id,
            reason_code,
        )
        return

    if reason_code != 0:
        raise ApplicationAckPublishError(
            "Application ACK publish failed: "
            f"event_id={event_id} "
            f"mid={message_id} "
            f"reason={reason_code}"
        )

    LOGGER.info(
        "Application ACK PUBACK received "
        "event_id=%s mid=%s",
        event_id,
        message_id,
    )


def on_message(
    client: mqtt.Client,
    userdata: object,
    message: mqtt.MQTTMessage,
) -> None:
    runtime = _require_runtime(userdata)

    process_message(
        client,
        runtime,
        message,
    )


def build_client(
    config: ReceiverConfig,
    database: EventDatabase,
) -> mqtt.Client:
    runtime = ReceiverRuntime(
        config=config,
        database=database,
    )

    client = mqtt.Client(
        callback_api_version=(
            mqtt.CallbackAPIVersion.VERSION2
        ),
        client_id=config.mqtt_client_id,
        clean_session=False,
        userdata=runtime,
        protocol=mqtt.MQTTv311,
        transport="tcp",
        reconnect_on_failure=True,
        manual_ack=True,
    )

    client.username_pw_set(
        config.mqtt_username,
        config.mqtt_password,
    )

    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_disconnect = on_disconnect
    client.on_subscribe = on_subscribe
    client.on_publish = on_publish
    client.on_message = on_message

    return client


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s %(message)s"
        ),
    )


def main() -> int:
    configure_logging()

    try:
        config = load_receiver_config()

        database = EventDatabase(
            config.database_path
        )

        client = build_client(
            config,
            database,
        )

        LOGGER.info(
            "Receiver starting "
            "client_id=%s database=%s",
            config.mqtt_client_id,
            config.database_path,
        )

        client.connect_async(
            config.mqtt_broker_host,
            port=config.mqtt_broker_port,
            keepalive=(
                config.mqtt_keepalive_seconds
            ),
        )

        confirmation_stopped = threading.Event()
        confirmation_thread = threading.Thread(
            target=pump_confirmations,
            args=(client, client.user_data_get(), confirmation_stopped),
            name='guardian-confirmation', daemon=True)
        confirmation_thread.start()
        try:
            result = client.loop_forever(retry_first_connection=True)
        finally:
            confirmation_stopped.set()
            confirmation_thread.join(timeout=2)

        if result != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.error(
                "MQTT network loop stopped "
                "result=%s",
                result,
            )
            return 1

        return 0

    except ConfigurationError as exc:
        LOGGER.error(
            "Receiver configuration error: %s",
            exc,
        )
        return 2

    except KeyboardInterrupt:
        LOGGER.info(
            "Receiver stopped by user"
        )
        return 0

    except Exception:
        LOGGER.exception(
            "Receiver stopped due to "
            "an unexpected error"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
