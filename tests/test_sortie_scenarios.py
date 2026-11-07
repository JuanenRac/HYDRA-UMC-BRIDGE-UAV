# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Whole sorties with injected faults against the autopilot emulator
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A sortie from arming to touchdown, and the faults a flight meets on the way (evidence: simulator).

The bridge's real `MavlinkFlightControl` talks to the autopilot emulator of `mavlink_emulator.py` through its sink seam. The
faults are injected on the emulator side: a battery that drops, a lost GPS fix, the link-loss failsafe, a camera that stays
silent and a station whose MAVLink 2 signing key the vehicle does not know. No PX4, ArduPilot or aircraft was involved.
"""
from __future__ import annotations

import unittest

from hydra_umc_bridge_uav import MavlinkFlightControl, UavDispatch

from mavlink_emulator import (
    MavlinkFlightControllerEmulator,
    RESULT_ACCEPTED,
    RESULT_TEMPORARILY_REJECTED,
    STATE_ACTIVE,
    STATE_CRITICAL,
)

QUICK = 0.05  # seconds to wait for an acknowledgement that never comes


def accepted(request: str) -> UavDispatch:
    return UavDispatch(True, request, "cell and external machine are ready")


class SignedLinkVehicle:
    """The vehicle side of a MAVLink 2 signed link: it answers only messages carrying its own key.

    The station key is what the bridge would sign with; the vehicle ignores (no acknowledgement at all) anything signed with
    another key, and unsigned messages unless it was told to accept them.
    """

    def __init__(self, vehicle: MavlinkFlightControllerEmulator, vehicle_key: bytes, station_key: bytes | None, accept_unsigned: bool = False) -> None:
        self._vehicle, self._vehicle_key, self.station_key, self._accept_unsigned = vehicle, vehicle_key, station_key, accept_unsigned
        self.ignored = 0

    def _heard(self) -> bool:
        if self.station_key is None:
            return self._accept_unsigned
        return self.station_key == self._vehicle_key

    def command_long_send(self, *args):
        if not self._heard():
            self.ignored += 1
            return None
        return self._vehicle.command_long_send(*args)

    def command_int_send(self, *args):
        if not self._heard():
            self.ignored += 1
            return None
        return self._vehicle.command_int_send(*args)

    def recv_match(self, type, blocking, timeout):
        return self._vehicle.recv_match(type, blocking, timeout)


class SortieTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fc = MavlinkFlightControllerEmulator()
        self.control = MavlinkFlightControl()

    def send(self, request: str, sink=None, **kwargs):
        kwargs.setdefault("ack_timeout_seconds", QUICK)
        return self.control.send(sink or self.fc, 1, 1, accepted(request), **kwargs)

    def fly_to_hover(self) -> None:
        self.assertTrue(self.send("ARM", confirm_arm=True).sent)
        self.assertTrue(self.send("TAKEOFF", takeoff_altitude_m=15.0).sent)
        self.fc.step()

    def test_a_whole_sortie_arm_climb_move_capture_return_and_touch_down(self) -> None:
        self.fly_to_hover()
        self.assertEqual((self.fc.flight_phase, self.fc.altitude_m), ("FLYING", 15.0))
        self.assertTrue(self.send("GOTO_WAYPOINT", waypoint_lat=40.4167754, waypoint_lon=-3.7037902, waypoint_alt_m=15.0).sent)
        self.assertTrue(self.send("HOVER_AND_CAPTURE").sent)
        self.assertEqual((self.fc.flight_phase, self.fc.image_count), ("HOVERING", 1))
        self.assertTrue(self.send("RETURN_TO_LAUNCH").sent)
        self.fc.step()
        self.assertEqual((self.fc.flight_phase, self.fc.armed), ("ON_GROUND", False))

    def test_a_battery_that_is_too_low_refuses_the_arm_and_a_charged_one_arms(self) -> None:
        self.fc.set_battery(10.0)
        refused = self.send("ARM", confirm_arm=True)
        self.assertFalse(refused.sent)
        self.assertFalse(self.fc.armed)
        self.fc.set_battery(90.0)
        self.assertTrue(self.send("ARM", confirm_arm=True).sent)

    def test_losing_the_gps_fix_before_the_arm_is_a_temporary_refusal_that_clears(self) -> None:
        self.fc.set_gps_fix(False)
        self.assertFalse(self.send("ARM", confirm_arm=True).sent)
        self.assertEqual(self.fc.last_ack["result"], RESULT_TEMPORARILY_REJECTED)
        self.fc.set_gps_fix(True)
        self.assertTrue(self.send("ARM", confirm_arm=True).sent)

    def test_the_link_loss_failsafe_brings_the_vehicle_home_and_the_heartbeat_says_critical(self) -> None:
        self.fly_to_hover()
        self.fc.trigger_link_loss_failsafe()
        self.assertEqual(self.fc.emit_heartbeat()["system_status"], STATE_CRITICAL)
        self.assertEqual(self.fc.flight_phase, "RETURNING")
        # the bridge may still ask for the safe actions while the failsafe flies the vehicle home
        self.assertTrue(self.send("LAND").sent)
        self.fc.step()
        self.assertEqual((self.fc.flight_phase, self.fc.armed), ("ON_GROUND", False))

    def test_a_vehicle_that_stays_active_in_the_air_is_reported_active_not_critical(self) -> None:
        self.fly_to_hover()
        self.assertEqual(self.fc.emit_heartbeat()["system_status"], STATE_ACTIVE)

    def test_a_second_takeoff_in_the_air_is_not_accepted_twice(self) -> None:
        self.fly_to_hover()
        again = self.send("TAKEOFF")
        self.assertFalse(again.sent)
        self.assertEqual(self.fc.flight_phase, "FLYING")

    def test_land_on_the_ground_is_refused_by_the_vehicle_and_not_reported_as_sent(self) -> None:
        self.assertFalse(self.send("LAND").sent)

    def test_a_camera_that_says_nothing_still_lets_the_loiter_through_and_reports_no_picture(self) -> None:
        self.fly_to_hover()
        self.fc.camera_silent = True
        self.send("HOVER_AND_CAPTURE")
        self.assertEqual(self.fc.image_count, 0)
        self.assertEqual(self.fc.flight_phase, "HOVERING")

    def test_rtl_is_still_sent_after_the_cell_stops_but_a_new_takeoff_is_not(self) -> None:
        self.fly_to_hover()
        stopped = UavDispatch(False, "GOTO_WAYPOINT", "cell is SAFE_STOP, not READY")
        before = len(self.fc.commands)
        self.assertFalse(self.control.send(self.fc, 1, 1, stopped, waypoint_lat=1.0, waypoint_lon=1.0, waypoint_alt_m=10.0, ack_timeout_seconds=QUICK).sent)
        self.assertEqual(len(self.fc.commands), before)
        rtl = UavDispatch(False, "RETURN_TO_LAUNCH", "cell is SAFE_STOP, not READY")
        self.assertTrue(self.control.send(self.fc, 1, 1, rtl, ack_timeout_seconds=QUICK).sent)
        self.assertEqual(self.fc.flight_phase, "RETURNING")


class SigningScenarioTests(unittest.TestCase):
    KEY = bytes(range(32))

    def setUp(self) -> None:
        self.fc = MavlinkFlightControllerEmulator()
        self.control = MavlinkFlightControl()

    def arm_through(self, link) -> object:
        return self.control.send(link, 1, 1, accepted("ARM"), confirm_arm=True, ack_timeout_seconds=QUICK)

    def test_a_station_with_the_vehicles_key_is_obeyed(self) -> None:
        link = SignedLinkVehicle(self.fc, self.KEY, self.KEY)
        self.assertTrue(self.arm_through(link).sent)
        self.assertTrue(self.fc.armed)

    def test_a_station_with_the_wrong_key_is_ignored_and_never_reported_as_sent(self) -> None:
        link = SignedLinkVehicle(self.fc, self.KEY, bytes(32))
        result = self.arm_through(link)
        self.assertFalse(result.sent)
        self.assertIn("COMMAND_ACK", result.reason)
        self.assertFalse(self.fc.armed)
        self.assertEqual(link.ignored, 1)

    def test_an_unsigned_station_is_ignored_unless_the_vehicle_accepts_unsigned_messages(self) -> None:
        strict = SignedLinkVehicle(self.fc, self.KEY, None, accept_unsigned=False)
        self.assertFalse(self.arm_through(strict).sent)
        self.assertFalse(self.fc.armed)
        lenient = SignedLinkVehicle(self.fc, self.KEY, None, accept_unsigned=True)
        self.assertTrue(self.arm_through(lenient).sent)

    def test_the_safe_actions_get_no_exception_for_a_wrong_key(self) -> None:
        fc = self.fc
        self.assertTrue(self.control.send(fc, 1, 1, accepted("ARM"), confirm_arm=True, ack_timeout_seconds=QUICK).sent)
        self.assertTrue(self.control.send(fc, 1, 1, accepted("TAKEOFF"), ack_timeout_seconds=QUICK).sent)
        fc.step()
        wrong = SignedLinkVehicle(fc, self.KEY, bytes(32))
        rtl = self.control.send(wrong, 1, 1, accepted("RETURN_TO_LAUNCH"), ack_timeout_seconds=QUICK)
        self.assertFalse(rtl.sent)
        self.assertEqual(fc.flight_phase, "FLYING")  # the vehicle did not hear it: the failsafe is what remains


if __name__ == "__main__":
    unittest.main()
