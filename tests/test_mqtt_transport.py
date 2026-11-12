# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - MQTT connection tests (a stand-in for paho-mqtt)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The MQTT connection of the job entry, run against a stand-in for paho-mqtt: the subscription after every connect, the messages handed on, a refused login, a broker that
does not answer, a missing paho and a connection that never opens. No broker is involved; evidence=simulator."""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from hydra_umc_bridge_uav import mqtt_transport


class Reason:
    def __init__(self, failure: bool, text: str = "ok") -> None:
        self.is_failure = failure
        self._text = text

    def __str__(self) -> str:
        return self._text


class Message:
    def __init__(self, topic: str, payload: bytes, retain: bool = False) -> None:
        self.topic = topic
        self.payload = payload
        self.retain = retain


class FakeClient:
    """Answers the connection from `reason` (or never, when `reason` is None) as soon as the loop starts."""

    last: "FakeClient | None" = None
    reason: "Reason | None" = Reason(False)
    connect_error: "Exception | None" = None

    def __init__(self, api: object, client_id: str = "") -> None:
        self.client_id = client_id
        self.subscribed: list[object] = []
        self.delays: tuple[int, int] | None = None
        self.connects: list[tuple[str, int, int]] = []
        self.loop_started = False
        self.loop_stopped = False
        FakeClient.last = self

    def reconnect_delay_set(self, min_delay: int, max_delay: int) -> None:
        self.delays = (min_delay, max_delay)

    def connect(self, host: str, port: int, keepalive: int = 60) -> None:
        if FakeClient.connect_error is not None:
            raise FakeClient.connect_error
        self.connects.append((host, port, keepalive))

    def loop_start(self) -> None:
        self.loop_started = True
        if FakeClient.reason is not None:
            self.on_connect(self, None, {}, FakeClient.reason, None)

    def loop_stop(self) -> None:
        self.loop_stopped = True

    def subscribe(self, topics: object) -> None:
        self.subscribed.append(topics)

    def username_pw_set(self, *args: object, **kwargs: object) -> None:
        pass

    def tls_set(self, *args: object, **kwargs: object) -> None:
        pass


class Entry:
    def __init__(self, subscriptions: list[tuple[str, int]], handled: str = "hydra/bridges/uav/cmd/job") -> None:
        self._subscriptions = subscriptions
        self._handled = handled
        self.messages: list[tuple[str, bytes, bool]] = []

    def subscriptions(self) -> list[tuple[str, int]]:
        return self._subscriptions

    def handles(self, topic: str) -> bool:
        return topic == self._handled

    def on_message(self, client: object, topic: str, payload: bytes, *, retained: bool = False) -> None:
        self.messages.append((topic, payload, retained))


def fake_paho() -> dict[str, types.ModuleType]:
    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = FakeClient  # type: ignore[attr-defined]
    client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)  # type: ignore[attr-defined]
    mqtt_package = types.ModuleType("paho.mqtt")
    mqtt_package.client = client_module  # type: ignore[attr-defined]
    paho = types.ModuleType("paho")
    paho.mqtt = mqtt_package  # type: ignore[attr-defined]
    return {"paho": paho, "paho.mqtt": mqtt_package, "paho.mqtt.client": client_module}


class MqttTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.last = None
        FakeClient.reason = Reason(False)
        FakeClient.connect_error = None
        patcher = mock.patch.dict(sys.modules, fake_paho())
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict("os.environ", {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def open(self, entry: Entry, **kwargs: object) -> FakeClient:
        mqtt_transport.open_mqtt_client("broker.local", 1884, job_entry=entry, connect_retry_delay_seconds=0.0, **kwargs)  # type: ignore[arg-type]
        assert FakeClient.last is not None
        return FakeClient.last

    def test_the_client_connects_with_its_own_id_and_subscribes_after_the_connect(self) -> None:
        client = self.open(Entry([("hydra/bridges/uav/cmd/job", 1)]), keepalive_seconds=45)
        self.assertEqual(client.client_id, mqtt_transport.DEFAULT_CLIENT_ID)
        self.assertEqual(client.connects, [("broker.local", 1884, 45)])
        self.assertEqual(client.delays, (1, 30))
        self.assertTrue(client.loop_started)
        self.assertEqual(client.subscribed, [[("hydra/bridges/uav/cmd/job", 1)]])

    def test_the_subscription_is_made_again_after_every_reconnect(self) -> None:
        client = self.open(Entry([("hydra/bridges/uav/cmd/job", 1)]))
        client.on_connect(client, None, {}, Reason(False), None)
        client.on_connect(client, None, {}, Reason(False), None)
        self.assertEqual(len(client.subscribed), 3)

    def test_an_entry_with_nothing_to_subscribe_subscribes_to_nothing(self) -> None:
        client = self.open(Entry([]))
        self.assertEqual(client.subscribed, [])

    def test_only_the_messages_the_entry_handles_reach_it_and_the_retain_flag_is_kept(self) -> None:
        entry = Entry([("hydra/bridges/uav/cmd/job", 1)])
        client = self.open(entry)
        client.on_message(client, None, Message("hydra/bridges/uav/cmd/job", b"{}", retain=True))
        client.on_message(client, None, Message("hydra/bridges/uav/cmd/job", b"{}"))
        client.on_message(client, None, Message("some/other/topic", b"x"))
        self.assertEqual(entry.messages, [("hydra/bridges/uav/cmd/job", b"{}", True), ("hydra/bridges/uav/cmd/job", b"{}", False)])

    def test_a_refused_login_is_an_error_and_the_loop_is_stopped(self) -> None:
        FakeClient.reason = Reason(True, "Not authorized")
        with self.assertRaises(RuntimeError) as caught:
            self.open(Entry([("t", 1)]))
        self.assertIn("refused", str(caught.exception))
        self.assertIn("Not authorized", str(caught.exception))
        assert FakeClient.last is not None
        self.assertTrue(FakeClient.last.loop_stopped)
        self.assertEqual(FakeClient.last.subscribed, [])

    def test_a_broker_that_does_not_answer_is_an_error_after_the_given_time(self) -> None:
        FakeClient.reason = None
        with self.assertRaises(RuntimeError) as caught:
            self.open(Entry([("t", 1)]), connack_timeout_seconds=0.05)
        self.assertIn("did not answer", str(caught.exception))
        assert FakeClient.last is not None
        self.assertTrue(FakeClient.last.loop_stopped)

    def test_a_connection_that_never_opens_is_an_error_not_a_silent_client(self) -> None:
        FakeClient.connect_error = ConnectionRefusedError("no route")
        with self.assertRaises(Exception) as caught:
            self.open(Entry([("t", 1)]), max_connect_attempts=2)
        self.assertNotIsInstance(caught.exception, AssertionError)
        assert FakeClient.last is not None
        self.assertFalse(FakeClient.last.loop_started)

    def test_a_missing_paho_says_how_to_get_it(self) -> None:
        with mock.patch.dict(sys.modules, {"paho": None, "paho.mqtt": None, "paho.mqtt.client": None}):
            with self.assertRaises(RuntimeError) as caught:
                mqtt_transport.open_mqtt_client("broker.local", job_entry=Entry([]))  # type: ignore[arg-type]
        self.assertIn("paho-mqtt is not installed", str(caught.exception))

    def test_losing_the_connection_is_only_logged(self) -> None:
        client = self.open(Entry([]))
        with self.assertLogs(mqtt_transport._LOG, level="WARNING"):
            client.on_disconnect(client, None, 0)


if __name__ == "__main__":
    unittest.main()
