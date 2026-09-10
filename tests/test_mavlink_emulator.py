# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Bridge <-> realistic MAVLink autopilot emulator tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Run this bridge's REAL MavlinkFlightControl.send() end to end against a
PX4/ArduPilot-shaped autopilot emulator that answers every COMMAND_LONG
with a real MAV_RESULT, runs its own pre-arm gate, and moves through a
real armed/flying/RTL/landed state machine - not a record-only FakeMavlinkSink.
"""
from __future__ import annotations

import unittest

from hydra_umc_bridge_uav import MavlinkFlightControl, UavDispatch

from mavlink_emulator import (
    MavlinkFlightControllerEmulator,
    RESULT_ACCEPTED,
    RESULT_DENIED,
    RESULT_TEMPORARILY_REJECTED,
    RESULT_UNSUPPORTED,
    STATE_ACTIVE,
    STATE_CRITICAL,
    MODE_FLAG_SAFETY_ARMED,
)


def _accepted(request: str) -> UavDispatch:
    return UavDispatch(True, request, "cell and external machine are ready")


class BridgeAgainstMavlinkAutopilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fc = MavlinkFlightControllerEmulator(system_id=1, component_id=1)
        self.control = MavlinkFlightControl()

    def _send(self, request: str, **kwargs) -> object:
        return self.control.send(self.fc, 1, 1, _accepted(request), **kwargs)

    def test_arm_is_accepted_only_after_pre_arm_passes_and_actually_arms_the_vehicle(self) -> None:
        result = self._send("ARM", confirm_arm=True)
        self.assertTrue(result.sent, result.reason)
        self.assertEqual(self.fc.last_ack["result"], RESULT_ACCEPTED)
        self.assertTrue(self.fc.armed)
        self.assertTrue(self.fc.emit_heartbeat()["base_mode"] & MODE_FLAG_SAFETY_ARMED)

    def test_a_hard_pre_arm_failure_makes_the_autopilot_deny_the_arm(self) -> None:
        self.fc.fail_prearm()  # e.g. compass/accel calibration not done
        self._send("ARM", confirm_arm=True)
        self.assertEqual(self.fc.last_ack["result"], RESULT_DENIED)
        self.assertFalse(self.fc.armed)

    def test_no_gps_fix_makes_the_autopilot_temporarily_reject_the_arm(self) -> None:
        self.fc.set_gps_fix(False)
        self._send("ARM", confirm_arm=True)
        self.assertEqual(self.fc.last_ack["result"], RESULT_TEMPORARILY_REJECTED)
        self.assertFalse(self.fc.armed)

    def test_takeoff_is_denied_while_disarmed_and_accepted_once_armed(self) -> None:
        self._send("TAKEOFF", takeoff_altitude_m=20.0)
        self.assertEqual(self.fc.last_ack["result"], RESULT_DENIED)

        self._send("ARM", confirm_arm=True)
        self._send("TAKEOFF", takeoff_altitude_m=20.0)
        self.assertEqual(self.fc.last_ack["result"], RESULT_ACCEPTED)
        self.assertEqual(self.fc.flight_phase, "CLIMBING")
        self.fc.step()
        self.assertEqual(self.fc.flight_phase, "FLYING")
        self.assertEqual(self.fc.altitude_m, 20.0)
        self.assertEqual(self.fc.emit_heartbeat()["system_status"], STATE_ACTIVE)

    def test_reposition_is_temporarily_rejected_on_the_ground_and_accepted_in_flight(self) -> None:
        self._send("ARM", confirm_arm=True)
        self._send("GOTO_WAYPOINT", waypoint_lat=40.0, waypoint_lon=-3.0, waypoint_alt_m=15.0)
        self.assertEqual(self.fc.last_ack["result"], RESULT_TEMPORARILY_REJECTED)

        self._send("TAKEOFF")
        self.fc.step()  # now FLYING
        self._send("GOTO_WAYPOINT", waypoint_lat=40.0, waypoint_lon=-3.0, waypoint_alt_m=15.0)
        self.assertEqual(self.fc.last_ack["result"], RESULT_ACCEPTED)

    def test_hover_and_capture_sends_loiter_then_image_start_capture_both_accepted(self) -> None:
        self._send("ARM", confirm_arm=True)
        self._send("TAKEOFF")
        self.fc.step()  # FLYING
        result = self._send("HOVER_AND_CAPTURE")
        self.assertTrue(result.sent, result.reason)
        # The bridge sends two real, separate MAV_CMDs in order.
        self.assertEqual([cmd for cmd, _ in self.fc.commands[-2:]], [17, 2000])
        self.assertEqual([a["result"] for a in self.fc.acks[-2:]], [RESULT_ACCEPTED, RESULT_ACCEPTED])
        self.assertEqual(self.fc.flight_phase, "HOVERING")

    def test_return_to_launch_flies_home_and_the_autopilot_auto_disarms_on_the_ground(self) -> None:
        self._send("ARM", confirm_arm=True)
        self._send("TAKEOFF")
        self.fc.step()  # FLYING
        self._send("RETURN_TO_LAUNCH")
        self.assertEqual(self.fc.last_ack["result"], RESULT_ACCEPTED)
        self.assertEqual(self.fc.flight_phase, "RETURNING")
        self.fc.step()  # touchdown
        self.assertEqual(self.fc.flight_phase, "ON_GROUND")
        self.assertFalse(self.fc.armed)

    def test_a_disarm_request_in_flight_is_denied_by_the_autopilot(self) -> None:
        self._send("ARM", confirm_arm=True)
        self._send("TAKEOFF")
        self.fc.step()  # FLYING
        # Drive a raw disarm the way the transport would (COMMAND_ARM_DISARM param1=0).
        self.fc.command_long_send(1, 1, 400, 0, 0, 0, 0, 0, 0, 0, 0)
        self.assertEqual(self.fc.last_ack["result"], RESULT_DENIED)
        self.assertTrue(self.fc.armed)

    def test_an_unknown_command_id_is_answered_unsupported(self) -> None:
        self.fc.command_long_send(1, 1, 999999, 0, 0, 0, 0, 0, 0, 0, 0)
        self.assertEqual(self.fc.last_ack["result"], RESULT_UNSUPPORTED)

    def test_a_link_loss_failsafe_switches_to_rtl_and_flags_critical(self) -> None:
        self._send("ARM", confirm_arm=True)
        self._send("TAKEOFF")
        self.fc.step()  # FLYING
        self.fc.trigger_link_loss_failsafe()
        self.assertEqual(self.fc.flight_phase, "RETURNING")
        self.assertEqual(self.fc.emit_heartbeat()["system_status"], STATE_CRITICAL)

    def test_a_dispatch_the_shared_gate_already_rejected_never_reaches_the_autopilot(self) -> None:
        rejected = UavDispatch(False, "TAKEOFF", "cell is FAULT, not READY")
        result = self.control.send(self.fc, 1, 1, rejected)
        self.assertFalse(result.sent)
        self.assertEqual(self.fc.commands, [])


if __name__ == "__main__":
    unittest.main()
