# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Heartbeat failsafe monitor tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Real, deterministic tests of the link-loss failsafe state machine.

Every test drives HeartbeatMonitor with an explicit `now` - no real time.sleep
anywhere, so this suite is fast and fully reproducible while still exercising
genuine timeout-boundary behavior.
"""

import unittest

from hydra_umc_bridge_uav import HeartbeatMonitor, LinkStatus


class HeartbeatMonitorTests(unittest.TestCase):
    def test_never_observed_is_lost_from_the_first_check_not_a_false_ok(self):
        monitor = HeartbeatMonitor(timeout_s=5.0)
        state = monitor.state(now=0.0)
        self.assertEqual(state.status, LinkStatus.LOST)
        self.assertEqual(state.failsafe_action, "RETURN_TO_LAUNCH")

    def test_fresh_heartbeat_reports_ok_with_no_failsafe_action(self):
        monitor = HeartbeatMonitor(timeout_s=5.0)
        monitor.observe(now=100.0)
        state = monitor.state(now=101.0)
        self.assertEqual(state.status, LinkStatus.OK)
        self.assertAlmostEqual(state.seconds_since_last_heartbeat, 1.0)
        self.assertIsNone(state.failsafe_action)

    def test_exactly_at_the_timeout_boundary_is_still_ok(self):
        # A real, deliberate boundary choice: elapsed == timeout_s does not
        # yet trigger the failsafe - only genuinely exceeding it does.
        monitor = HeartbeatMonitor(timeout_s=5.0)
        monitor.observe(now=0.0)
        state = monitor.state(now=5.0)
        self.assertEqual(state.status, LinkStatus.OK)

    def test_one_instant_past_the_timeout_is_lost(self):
        monitor = HeartbeatMonitor(timeout_s=5.0)
        monitor.observe(now=0.0)
        state = monitor.state(now=5.001)
        self.assertEqual(state.status, LinkStatus.LOST)
        self.assertAlmostEqual(state.seconds_since_last_heartbeat, 5.001)

    def test_a_later_heartbeat_recovers_the_link(self):
        monitor = HeartbeatMonitor(timeout_s=5.0)
        monitor.observe(now=0.0)
        self.assertEqual(monitor.state(now=10.0).status, LinkStatus.LOST)
        monitor.observe(now=10.5)
        self.assertEqual(monitor.state(now=11.0).status, LinkStatus.OK)

    def test_custom_failsafe_action_is_honored(self):
        monitor = HeartbeatMonitor(timeout_s=2.0, failsafe_action="HOVER")
        state = monitor.state(now=0.0)
        self.assertEqual(state.failsafe_action, "HOVER")

    def test_checking_state_never_mutates_the_monitor(self):
        # A real, easy-to-get-wrong bug class for a stateful watchdog: a
        # read-only status check must never itself count as a heartbeat.
        monitor = HeartbeatMonitor(timeout_s=5.0)
        monitor.observe(now=0.0)
        monitor.state(now=100.0)  # a real check, well past timeout
        monitor.state(now=200.0)  # checking again must not have reset anything
        self.assertEqual(monitor.state(now=200.0).status, LinkStatus.LOST)

    def test_rejects_a_non_positive_timeout(self):
        with self.assertRaises(ValueError):
            HeartbeatMonitor(timeout_s=0.0)
        with self.assertRaises(ValueError):
            HeartbeatMonitor(timeout_s=-1.0)

    def test_rejects_non_finite_timeout(self):
        for timeout in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                HeartbeatMonitor(timeout_s=timeout)

    def test_rejects_non_finite_heartbeat_or_check_timestamp(self):
        monitor = HeartbeatMonitor(timeout_s=5.0)
        for timestamp in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                monitor.observe(timestamp)
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                monitor.state(timestamp)

    def test_rejects_a_now_earlier_than_the_last_heartbeat(self):
        monitor = HeartbeatMonitor(timeout_s=5.0)
        monitor.observe(now=100.0)
        with self.assertRaises(ValueError):
            monitor.state(now=99.0)


if __name__ == "__main__":
    unittest.main()
