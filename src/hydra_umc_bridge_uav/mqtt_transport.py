# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - MQTT connection for the job entry
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Open the MQTT connection that carries `hydra/bridges/uav/cmd/job` to `UavJobEntry`.

The only place this package imports paho-mqtt, and only when a broker address was given. The credentials and TLS of the service (`HYDRA_MQTT_*`) come from the shared
SDK helper; the client id is explicit because the broker only accepts a credential whose client id starts with the credential's prefix. The first connect is retried;
paho's own loop reconnects later and the subscription is renewed after every reconnect. A refused login, or no answer from the broker in time, is an error here, not a
client whose every publish would fail. Not run against a real broker.
"""

from __future__ import annotations

import logging
import threading

from hydra_umc_sdk.bridge_transport import connect_with_retry
from hydra_umc_sdk.mqtt_connection import apply_connection_settings, settings_from_env

from .job_entry import MqttPublisher, UavJobEntry

_LOG = logging.getLogger(__name__)

DEFAULT_CLIENT_ID = "hydra-umc-bridge-uav"


def open_mqtt_client(
    host: str,
    port: int = 1883,
    *,
    job_entry: UavJobEntry,
    keepalive_seconds: int = 60,
    max_connect_attempts: int = 5,
    connect_retry_delay_seconds: float = 2.0,
    client_id: str = DEFAULT_CLIENT_ID,
    connack_timeout_seconds: float = 10.0,
) -> MqttPublisher:
    """Connect, subscribe `job_entry` after every (re)connect and hand each message to it. Raises RuntimeError when paho-mqtt is missing or the broker refuses."""
    try:
        import paho.mqtt.client as mqtt  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError("paho-mqtt is not installed - install it to receive jobs over MQTT (the rest of the bridge works without it)") from error
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    apply_connection_settings(client, settings_from_env())
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    answered = threading.Event()
    refusal: list[str] = []

    def on_connect(client: object, userdata: object, flags: object, reason_code: object, properties: object = None) -> None:
        if getattr(reason_code, "is_failure", False):
            refusal.append(str(reason_code))
        else:
            subscriptions = job_entry.subscriptions()
            if subscriptions:
                client.subscribe(subscriptions)  # type: ignore[attr-defined]
        answered.set()

    def on_disconnect(client: object, userdata: object, *args: object) -> None:
        _LOG.warning("the MQTT connection to the broker was lost; the client reconnects on its own")

    def on_message(_client: object, _userdata: object, message: object) -> None:
        if job_entry.handles(message.topic):  # type: ignore[attr-defined]
            job_entry.on_message(client, message.topic, bytes(message.payload), retained=bool(getattr(message, "retain", False)))  # type: ignore[attr-defined]

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    connect_with_retry(
        lambda: client.connect(host, port, keepalive=keepalive_seconds),
        max_attempts=max_connect_attempts,
        retry_delay_seconds=connect_retry_delay_seconds,
    )
    client.loop_start()
    if not answered.wait(connack_timeout_seconds):
        client.loop_stop()
        raise RuntimeError(f"the MQTT broker did not answer the connection within {connack_timeout_seconds}s")
    if refusal:
        client.loop_stop()
        raise RuntimeError(f"the MQTT broker refused the connection: {refusal[0]}")
    return client
