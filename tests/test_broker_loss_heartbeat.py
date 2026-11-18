# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - heartbeat that can stop with the MQTT broker: tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The station heartbeat is withheld only when the settings ask for it and the broker has been gone for longer than the configured time."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from hydra_umc_bridge_uav import GcsHeartbeat, cli, mqtt_transport
from hydra_umc_bridge_uav.broker_watch import BrokerWatch
from hydra_umc_bridge_uav.settings import JobEntrySettings, load_settings

import test_mqtt_transport as transport_tests  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Sink:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def heartbeat_send(self, *args):
        self.calls.append(args)


class BrokerWatchTests(unittest.TestCase):
    def test_off_by_default_never_unhealthy(self):
        clock = Clock()
        watch = BrokerWatch(clock=clock)
        watch.lost()
        clock.now += 10_000
        self.assertTrue(watch.healthy())

    def test_unhealthy_only_after_the_configured_time(self):
        clock = Clock()
        watch = BrokerWatch(5.0, clock=clock)
        self.assertTrue(watch.healthy())
        watch.lost()
        clock.now += 5.0
        self.assertTrue(watch.healthy())
        clock.now += 0.1
        self.assertFalse(watch.healthy())

    def test_a_second_loss_does_not_restart_the_count_and_a_reconnect_clears_it(self):
        clock = Clock()
        watch = BrokerWatch(5.0, clock=clock)
        watch.lost()
        clock.now += 4
        watch.lost()
        clock.now += 2
        self.assertFalse(watch.healthy())
        watch.connected()
        self.assertTrue(watch.healthy())


class HeartbeatWithholdingTests(unittest.TestCase):
    def run_beats(self, healthy_answers):
        sink = Sink()
        answers = iter(healthy_answers)
        done = threading.Event()

        def healthy():
            try:
                return next(answers)
            except StopIteration:
                done.set()
                return True

        def wait(stop, seconds):
            if done.is_set():
                stop.set()
            return stop.is_set()

        beat = GcsHeartbeat(sink, 1.0, wait=wait, healthy=healthy)
        beat.start()
        self.assertTrue(done.wait(5))
        beat.stop()
        return sink, beat.status()

    def test_beats_are_withheld_and_counted_while_unhealthy(self):
        sink, status = self.run_beats([True, False, False, True])
        self.assertEqual(status.withheld, 2)
        self.assertGreaterEqual(len(sink.calls), 2)

    def test_without_a_health_check_every_beat_is_sent(self):
        sink = Sink()
        beat = GcsHeartbeat(sink)
        self.assertTrue(beat.beat())
        self.assertEqual(beat.status().withheld, 0)

    def test_a_broken_health_check_keeps_the_beats_going(self):
        sink = Sink()

        def broken():
            raise RuntimeError("boom")

        beat = GcsHeartbeat(sink, 1.0, wait=lambda stop, seconds: True, healthy=broken)
        beat._run()
        self.assertEqual(len(sink.calls), 1)
        self.assertEqual(beat.status().withheld, 0)


class SettingsTests(unittest.TestCase):
    def test_default_is_off(self):
        self.assertEqual(JobEntrySettings().broker_loss_stops_heartbeat_s, 0)
        self.assertEqual(load_settings(None).job_entry.broker_loss_stops_heartbeat_s, 0)

    def test_the_shipped_configuration_keeps_it_off(self):
        shipped = Path(__file__).resolve().parents[1] / "config" / "uav.json"
        self.assertEqual(load_settings(shipped).job_entry.broker_loss_stops_heartbeat_s, 0)

    def test_bad_values_are_refused(self):
        for bad in ("-1", '"soon"', "true", "null"):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "uav.json"
                path.write_text('{"job_entry": {"broker_loss_stops_heartbeat_s": %s}}' % bad, encoding="utf-8")
                with self.subTest(bad), self.assertRaises(ValueError):
                    load_settings(path)

    def test_a_value_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "uav.json"
            path.write_text(json.dumps({"job_entry": {"enabled": True, "broker_loss_stops_heartbeat_s": 15}}), encoding="utf-8")
            self.assertEqual(load_settings(path).job_entry.broker_loss_stops_heartbeat_s, 15)


class TransportWiringTests(unittest.TestCase):
    """Uses the fake paho client of the transport tests: the callbacks of the client feed the watch."""

    def setUp(self):
        transport_tests.FakeClient.last = None
        transport_tests.FakeClient.reason = transport_tests.Reason(False)
        transport_tests.FakeClient.connect_error = None
        for patcher in (mock.patch.dict(sys.modules, transport_tests.fake_paho()), mock.patch.dict("os.environ", {}, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_connect_and_disconnect_callbacks_feed_the_watch(self):
        clock = Clock()
        watch = BrokerWatch(3.0, clock=clock)
        mqtt_transport.open_mqtt_client("broker.local", job_entry=transport_tests.Entry([]), connect_retry_delay_seconds=0.0, broker_watch=watch)  # type: ignore[arg-type]
        client = transport_tests.FakeClient.last
        self.assertTrue(watch.healthy())
        client.on_disconnect(client, None, 0, None)
        clock.now += 4
        self.assertFalse(watch.healthy())
        client.on_connect(client, None, {}, transport_tests.Reason(False), None)
        self.assertTrue(watch.healthy())


class RunWiringTests(unittest.TestCase):
    def go(self, settings: dict, opened: list):
        class FakeSink:
            def heartbeat_send(self, *args):
                pass

        def mqtt_opener(host, port, *, job_entry, **extra):
            opened.append(extra)
            return None

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "uav.json"
            path.write_text(json.dumps(settings), encoding="utf-8")
            args = cli.build_parser().parse_args(["run", "--connection", "x", "--duration", "0.2", "--status-every", "0", "--settings", str(path), "--mqtt-host", "broker.local"])
            import io

            out = io.StringIO()
            code = cli.run(args, opener=lambda *a, **k: FakeSink(), mqtt_opener=mqtt_opener, out=out, err=io.StringIO(), install_signals=False)
        return code, out.getvalue()

    def test_default_settings_call_the_opener_without_a_watch(self):
        opened: list = []
        code, out = self.go({"job_entry": {"enabled": True}}, opened)
        self.assertEqual(code, 0)
        self.assertEqual(opened, [{}])
        self.assertIn('"withheld": 0', out)

    def test_a_configured_time_hands_a_watch_to_the_opener(self):
        opened: list = []
        code, _ = self.go({"job_entry": {"enabled": True, "broker_loss_stops_heartbeat_s": 20}}, opened)
        self.assertEqual(code, 0)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["broker_watch"].stop_after_s, 20.0)


if __name__ == "__main__":
    unittest.main()
