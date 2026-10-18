"""Retry Pi-to-device confirmations until the device reports applying/rejecting them."""
import json
import logging
from confirmation_store import ConfirmationStore

LOGGER = logging.getLogger('carecall_receiver')


def pump_confirmations(client, runtime, stopped):
    store = ConfirmationStore(runtime.database.database_path)
    while not stopped.wait(1):
        try:
            if not client.is_connected():
                continue
            job = store.claim_command()
            if job is None:
                continue
            payload = json.dumps({'event_id':job['event_id'], 'device_id':job['device_id'],
                                  'status':'confirmed'}, separators=(',', ':'))
            # Broker PUBACK never completes this job. Only the device's report
            # can do that; no cross-thread MID bookkeeping or callback lock.
            info = client.publish(f"carecall/v1/devices/{job['device_id']}/ack",
                                  payload, qos=1, retain=False)
            if info.rc == 0:
                LOGGER.info('Guardian confirmation queued event_id=%s', job['event_id'])
            else:
                LOGGER.warning('Guardian confirmation transport unavailable')
        except Exception as exc:
            LOGGER.error('Guardian confirmation deferred error_type=%s', type(exc).__name__)
