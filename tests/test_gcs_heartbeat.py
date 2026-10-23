# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Own ground-station heartbeat tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The bridge's own HEARTBEAT as a ground station: shape, rate configuration, failure handling and the thread."""

import threading
import unittest

from hydra_umc_bridge_uav import GcsHeartbeat, rate_from_environment
from hydra_umc_bridge_uav.gcs_heartbeat import (
    DEFAULT_RATE_HZ,
    MAV_AUTOPILOT_INVALID,
    MAV_STATE_ACTIVE,
    MAV_TYPE_GCS,
    parse_rate_hz,
)
from hydra_umc_bridge_uav.mavlink_transport import _PymavlinkConnectionSink


class Sink:
    def __init__(self, fail_first=0):
        self.calls = []
        self.fail_first = fail_first
        self.seen = threading.Event()

    def heartbeat_send(self, *args):
        self.calls.append(args)
        self.seen.set()
        if len(self.calls) <= self.fail_first:
            raise OSError("link down")


class RateTests(unittest.TestCase):
    def test_default_is_one_hertz_and_unset_or_empty_uses_it(self):
        self.assertEqual(DEFAULT_RATE_HZ, 1.0)
        self.assertEqual(parse_rate_hz(None), 1.0)
        self.assertEqual(parse_rate_hz("  "), 1.0)
        self.assertEqual(rate_from_environment({}), 1.0)

    def test_a_configured_rate_and_off(self):
        self.assertEqual(parse_rate_hz("2.5"), 2.5)
        self.assertEqual(rate_from_environment({"HYDRA_UAV_GCS_HEARTBEAT_HZ": "0"}), 0.0)

    def test_out_of_range_or_not_a_number_is_refused(self):
        for bad in ("abc", "-1", "0.05", "11", "nan", "inf"):
            with self.subTest(bad), self.assertRaises(ValueError):
                parse_rate_hz(bad)


class BeatTests(unittest.TestCase):
    def test_the_heartbeat_is_a_ground_station_one(self):
        sink = Sink()
        self.assertTrue(GcsHeartbeat(sink).beat())
        self.assertEqual(sink.calls, [(MAV_TYPE_GCS, MAV_AUTOPILOT_INVALID, 0, 0, MAV_STATE_ACTIVE)])
        self.assertEqual(MAV_TYPE_GCS, 6)

    def test_a_failing_link_is_counted_and_never_raises(self):
        beat = GcsHeartbeat(Sink(fail_first=1))
        self.assertFalse(beat.beat())
        self.assertTrue(beat.beat())
        status = beat.status()
        self.assertEqual((status.sent, status.failed), (1, 1))
        self.assertIn("link down", status.last_error)

    def test_off_never_starts_a_thread(self):
        beat = GcsHeartbeat(Sink(), rate_hz=0)
        self.assertFalse(beat.enabled)
        self.assertFalse(beat.start())
        self.assertFalse(beat.status().running)


class ThreadTests(unittest.TestCase):
    def test_the_thread_beats_at_the_configured_period_and_stops(self):
        sink = Sink()
        waits = []

        def wait(stop, seconds):
            waits.append(seconds)
            return len(waits) >= 3  # stop after the third beat

        beat = GcsHeartbeat(sink, rate_hz=2.0, wait=wait)
        self.assertTrue(beat.start())
        beat._thread.join(2)
        self.assertEqual(len(sink.calls), 3)
        self.assertEqual(waits, [0.5, 0.5, 0.5])
        beat.stop()
        self.assertFalse(beat.status().running)

    def test_a_real_thread_sends_the_first_beat_at_once_and_stop_ends_it(self):
        sink = Sink()
        with GcsHeartbeat(sink, rate_hz=10.0) as beat:
            self.assertTrue(sink.seen.wait(2))
            self.assertTrue(beat.status().running)
            self.assertFalse(beat.start())  # already running
        self.assertFalse(beat.status().running)


class PymavlinkAdapterTests(unittest.TestCase):
    def test_the_adapter_sends_through_the_mav_object(self):
        sent = []

        class Mav:
            def heartbeat_send(self, *args):
                sent.append(args)

        class Connection:
            mav = Mav()

        _PymavlinkConnectionSink(Connection()).heartbeat_send(6, 8, 0, 0, 4)
        self.assertEqual(sent, [(6, 8, 0, 0, 4)])


if __name__ == "__main__":
    unittest.main()
