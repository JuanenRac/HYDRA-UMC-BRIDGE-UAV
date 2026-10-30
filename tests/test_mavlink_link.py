# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Single receiver thread tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""MavlinkLink: heartbeats refresh the watchdog, other messages are handed to recv_match, a dead link goes LOST."""

import queue
import threading
import unittest

from hydra_umc_bridge_uav import HeartbeatMonitor, LinkStatus, MavlinkLink


class Msg:
    def __init__(self, kind, **fields):
        self._kind = kind
        self.__dict__.update(fields)

    def get_type(self):
        return self._kind


class FakeConnection:
    """Hands out queued messages; an exception put in the queue is raised by the next read."""

    def __init__(self):
        self.inbox = queue.Queue()

    def recv_match(self, type, blocking, timeout):
        try:
            item = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, BaseException):
            raise item
        return item


class Clock:
    def __init__(self):
        self.now = 100.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now


def wait_until(predicate, seconds=2.0):
    done = threading.Event()
    end = threading.Timer(seconds, done.set)
    end.start()
    try:
        while not done.is_set():
            if predicate():
                return True
            done.wait(0.005)
        return predicate()
    finally:
        end.cancel()


class MavlinkLinkTests(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.monitor = HeartbeatMonitor(timeout_s=3.0)
        self.clock = Clock()
        self.link = MavlinkLink(self.conn, self.monitor, clock=self.clock, poll_s=0.01)
        self.addCleanup(self.link.stop)

    def test_vehicle_heartbeat_marks_the_link_ok_and_silence_marks_it_lost(self):
        self.assertEqual(self.monitor.state(100.0).status, LinkStatus.LOST)
        self.link.start()
        self.conn.inbox.put(Msg("HEARTBEAT", type=2))
        self.assertTrue(wait_until(lambda: self.monitor.state(100.0).status == LinkStatus.OK))
        self.assertEqual(self.monitor.state(103.0).status, LinkStatus.OK)
        self.assertEqual(self.monitor.state(103.5).status, LinkStatus.LOST)

    def test_a_ground_station_heartbeat_is_not_the_vehicle(self):
        self.link.start()
        self.conn.inbox.put(Msg("HEARTBEAT", type=6))
        self.conn.inbox.put(Msg("PING"))
        self.assertIsNotNone(self.link.recv_match("PING", True, 1.0))
        self.assertEqual(self.monitor.state(100.0).status, LinkStatus.LOST)

    def test_other_messages_are_kept_by_type_and_in_order(self):
        self.link.start()
        first = Msg("COMMAND_ACK", n=1)
        second = Msg("COMMAND_ACK", n=2)
        self.conn.inbox.put(first)
        self.conn.inbox.put(Msg("STATUSTEXT"))
        self.conn.inbox.put(second)
        self.assertIs(self.link.recv_match("COMMAND_ACK", True, 1.0), first)
        self.assertIs(self.link.recv_match("COMMAND_ACK", True, 1.0), second)
        self.assertIsNotNone(self.link.recv_match("STATUSTEXT", True, 1.0))

    def test_recv_match_times_out_and_can_be_non_blocking(self):
        self.link.start()
        self.assertIsNone(self.link.recv_match("COMMAND_ACK", True, 0.05))
        self.assertIsNone(self.link.recv_match("COMMAND_ACK", False, 5.0))

    def test_a_failed_connection_ends_the_thread_and_stops_refreshing(self):
        self.link.start()
        self.conn.inbox.put(OSError("radio unplugged"))
        self.assertTrue(wait_until(lambda: not self.link.running))
        self.assertIsInstance(self.link.error, OSError)
        self.clock.now = 200.0
        self.assertEqual(self.monitor.state(200.0).status, LinkStatus.LOST)
        self.assertIsNone(self.link.recv_match("COMMAND_ACK", True, 1.0))

    def test_messages_without_a_type_are_ignored(self):
        self.link.start()
        self.conn.inbox.put(object())
        self.conn.inbox.put(Msg("PONG"))
        self.assertIsNotNone(self.link.recv_match("PONG", True, 1.0))
        self.assertTrue(self.link.running)

    def test_stop_ends_the_thread_and_start_twice_is_refused(self):
        self.link.start()
        with self.assertRaises(RuntimeError):
            self.link.start()
        self.link.stop()
        self.assertFalse(self.link.running)

    def test_bad_poll_interval_is_refused(self):
        with self.assertRaises(ValueError):
            MavlinkLink(self.conn, self.monitor, poll_s=0)


if __name__ == "__main__":
    unittest.main()
