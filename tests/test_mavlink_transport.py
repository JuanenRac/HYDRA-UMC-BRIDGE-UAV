# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Real MAVLink command transport tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Tests the real MAVLink flight control against an in-memory fake sink.

No real flight controller, SITL or pymavlink install is needed:
MavlinkFlightControl is written against the small MavlinkCommandSink
protocol (matching pymavlink's own real command_long_send() signature), so
a plain fake proves the real MAV_CMD mapping/gating is correct independent
of pymavlink - only open_mavlink_connection() itself needs it, and it
isn't exercised here.
"""

import unittest

from hydra_umc_bridge_uav import BridgeJob, CellState, JobPhase, MachineState, UavCoordinator, UavDispatch
from hydra_umc_bridge_uav.mavlink_transport import MavlinkFlightControl


class FakeMavlinkSink:
    def __init__(self):
        self.sent: list[tuple] = []
        self.raise_on_send: OSError | None = None

    def command_long_send(self, target_system, target_component, command, confirmation, *params):
        if self.raise_on_send:
            raise self.raise_on_send
        self.sent.append((target_system, target_component, command, confirmation, params))


class MavlinkFlightControlTests(unittest.TestCase):
    def setUp(self):
        self.sink = FakeMavlinkSink()
        self.control = MavlinkFlightControl()

    def test_a_rejected_dispatch_is_never_sent(self):
        rejected = UavDispatch(False, "TAKEOFF", "cell is FAULT, not READY")
        result = self.control.send(self.sink, 1, 1, rejected)
        self.assertFalse(result.sent)
        self.assertEqual(self.sink.sent, [])

    def test_pre_flight_check_sends_the_real_arm_command(self):
        # MAV_CMD_COMPONENT_ARM_DISARM = 400, param1=1 (arm) - researched
        # against mavlink/mavlink's own common.xml.
        dispatch = UavDispatch(True, "PRE_FLIGHT_CHECK", "cell and external machine are ready")
        result = self.control.send(self.sink, 1, 1, dispatch)
        self.assertTrue(result.sent)
        self.assertEqual(result.commands, (400,))
        _, _, command, _, params = self.sink.sent[0]
        self.assertEqual(command, 400)
        self.assertEqual(params[0], 1)

    def test_takeoff_sends_the_real_nav_takeoff_command_with_altitude(self):
        # MAV_CMD_NAV_TAKEOFF = 22, param7=altitude.
        dispatch = UavDispatch(True, "TAKEOFF", "cell and external machine are ready")
        result = self.control.send(self.sink, 1, 1, dispatch, takeoff_altitude_m=25.0)
        self.assertTrue(result.sent)
        _, _, command, _, params = self.sink.sent[0]
        self.assertEqual(command, 22)
        self.assertEqual(params[6], 25.0)  # param7 is the 7th of 7 params, index 6

    def test_goto_waypoint_sends_the_real_do_reposition_command(self):
        # MAV_CMD_DO_REPOSITION = 192 - the real, documented command for an
        # immediate GUIDED-mode "go here now", not the mission-item
        # MAV_CMD_NAV_WAYPOINT.
        dispatch = UavDispatch(True, "GOTO_WAYPOINT", "cell and external machine are ready")
        result = self.control.send(
            self.sink, 1, 1, dispatch, waypoint_lat=47.3977, waypoint_lon=8.5456, waypoint_alt_m=30.0
        )
        self.assertTrue(result.sent)
        _, _, command, _, params = self.sink.sent[0]
        self.assertEqual(command, 192)
        self.assertEqual(params[4], 47.3977)  # param5 = lat
        self.assertEqual(params[5], 8.5456)  # param6 = lon
        self.assertEqual(params[6], 30.0)  # param7 = alt

    def test_goto_waypoint_without_coordinates_is_rejected_before_any_send(self):
        dispatch = UavDispatch(True, "GOTO_WAYPOINT", "cell and external machine are ready")
        result = self.control.send(self.sink, 1, 1, dispatch)
        self.assertFalse(result.sent)
        self.assertEqual(self.sink.sent, [])

    def test_hover_and_capture_sends_both_real_commands_in_order(self):
        # MAV_CMD_NAV_LOITER_UNLIM = 17, then MAV_CMD_IMAGE_START_CAPTURE = 2000.
        dispatch = UavDispatch(True, "HOVER_AND_CAPTURE", "cell and external machine are ready")
        result = self.control.send(self.sink, 1, 1, dispatch)
        self.assertTrue(result.sent)
        self.assertEqual(result.commands, (17, 2000))
        self.assertEqual([entry[2] for entry in self.sink.sent], [17, 2000])

    def test_return_to_launch_sends_the_real_rtl_command(self):
        dispatch = UavDispatch(True, "RETURN_TO_LAUNCH", "cell and external machine are ready")
        result = self.control.send(self.sink, 1, 1, dispatch)
        self.assertEqual(result.commands, (20,))

    def test_land_sends_the_real_land_command_distinct_from_rtl(self):
        dispatch = UavDispatch(True, "LAND", "emergency land requested")
        result = self.control.send(self.sink, 1, 1, dispatch)
        self.assertEqual(result.commands, (21,))
        self.assertNotEqual(result.commands, (20,))

    def test_a_transport_failure_is_reported_not_swallowed(self):
        self.sink.raise_on_send = OSError("link disconnected")
        dispatch = UavDispatch(True, "LAND", "emergency land requested")
        result = self.control.send(self.sink, 1, 1, dispatch)
        self.assertFalse(result.sent)
        self.assertIn("link disconnected", result.reason)

    def test_end_to_end_through_the_real_coordinator_gate_before_sending(self):
        job = BridgeJob("job-1", "idempotency-1", "uav-1", JobPhase.LOAD, MachineState.IDLE, {})
        dispatch = UavCoordinator().dispatch(job, CellState.READY)
        result = self.control.send(self.sink, 1, 1, dispatch, takeoff_altitude_m=10.0)
        self.assertTrue(result.sent)
        self.assertEqual(result.commands, (22,))


class OpenMavlinkConnectionTests(unittest.TestCase):
    def test_missing_pymavlink_raises_a_clear_runtime_error_not_an_import_error(self):
        from hydra_umc_bridge_uav import open_mavlink_connection

        try:
            import pymavlink  # noqa: F401

            self.skipTest("pymavlink is installed in this environment - nothing to prove here")
        except ImportError:
            pass
        with self.assertRaises(RuntimeError) as context:
            open_mavlink_connection("udp:127.0.0.1:14550")
        self.assertIn("pymavlink is not installed", str(context.exception))


if __name__ == "__main__":
    unittest.main()
