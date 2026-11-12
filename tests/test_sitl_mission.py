# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - tests for the simulator mission upload check (its judgement and its flow against the MAVLink emulator; no simulator is started here)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mavlink_emulator import MavlinkFlightControllerEmulator  # noqa: E402

from hydra_umc_bridge_uav.mavlink_mission import MissionUploadResult, parse_items  # noqa: E402

SPEC = importlib.util.spec_from_file_location("sitl_mission", Path(__file__).resolve().parent.parent / "tools" / "sitl_mission.py")
sitl_mission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sitl_mission)

HOME = (47.397742, 8.545594)


class Sink:
    """The emulated autopilot with the target ids a real connection exposes."""

    def __init__(self, emulator):
        self._emulator = emulator
        self.target_system = emulator.system_id
        self.target_component = emulator.component_id

    def __getattr__(self, name):
        return getattr(self._emulator, name)


def ok(items=4):
    return MissionUploadResult(True, "accepted", items)


class BuildTests(unittest.TestCase):
    def test_the_mission_is_valid_and_ends_at_home(self):
        items, why = parse_items(sitl_mission.build_mission(*HOME))
        self.assertIsNone(why)
        self.assertEqual([item.kind for item in items], ["takeoff", "waypoint", "waypoint", "land"])
        self.assertEqual((items[-1].lat, items[-1].lon), HOME)

    def test_the_points_are_about_the_asked_distance_from_home(self):
        items, _ = parse_items(sitl_mission.build_mission(*HOME, offset_m=40.0))
        north = (items[1].lat - HOME[0]) * sitl_mission.METRES_PER_DEGREE
        self.assertAlmostEqual(north, 40.0, delta=0.5)

    def test_the_outside_mission_is_five_radii_away(self):
        items, _ = parse_items(sitl_mission.build_outside_mission(*HOME, radius_m=200.0))
        self.assertGreater((items[1].lat - HOME[0]) * sitl_mission.METRES_PER_DEGREE, 900.0)


class JudgeTests(unittest.TestCase):
    def test_accepted_and_the_outside_one_refused_passes(self):
        passed, _ = sitl_mission.judge(ok(), MissionUploadResult(False, "outside"), 4)
        self.assertTrue(passed)

    def test_a_refused_mission_fails_with_the_reason(self):
        passed, reason = sitl_mission.judge(MissionUploadResult(False, "denied", 1), MissionUploadResult(False, "x"), 4)
        self.assertFalse(passed)
        self.assertIn("denied", reason)

    def test_a_short_serve_fails(self):
        self.assertFalse(sitl_mission.judge(ok(3), MissionUploadResult(False, "x"), 4)[0])

    def test_uploading_the_outside_mission_fails(self):
        passed, reason = sitl_mission.judge(ok(), ok(), 4)
        self.assertFalse(passed)
        self.assertIn("outside the envelope", reason)

    def test_an_outside_mission_refused_after_sending_items_fails(self):
        passed, reason = sitl_mission.judge(ok(), MissionUploadResult(False, "late", 2), 4)
        self.assertFalse(passed)
        self.assertIn("2 item", reason)


class FlowTests(unittest.TestCase):
    def test_the_flow_passes_against_the_emulator(self):
        emulator = MavlinkFlightControllerEmulator()
        passed, reason = sitl_mission.upload_both(Sink(emulator), HOME[0], HOME[1], 200.0)
        self.assertTrue(passed, reason)
        self.assertEqual(len(emulator.mission), 4)
        self.assertFalse(emulator.armed)

    def test_a_vehicle_that_refuses_the_mission_fails(self):
        emulator = MavlinkFlightControllerEmulator()
        emulator.mission_ack_result = 13
        passed, reason = sitl_mission.upload_both(Sink(emulator), HOME[0], HOME[1], 200.0)
        self.assertFalse(passed)
        self.assertIn("refused", reason)

    def test_a_vehicle_that_goes_silent_fails(self):
        emulator = MavlinkFlightControllerEmulator()
        emulator.mission_stall_after = 1
        passed, _ = sitl_mission.upload_both(Sink(emulator), HOME[0], HOME[1], 200.0)
        self.assertFalse(passed)


class Connection:
    def __init__(self, message=None):
        self._message = message

    def recv_match(self, **kwargs):
        return self._message


class ListeningConnection:
    def __init__(self, messages, target=(1, 1)):
        self.messages, self.target_system, self.target_component = list(messages), *target

    def recv_match(self, **kwargs):
        return self.messages.pop(0) if self.messages else None


class Message:
    def __init__(self, kind, text=""):
        self._kind, self.text = kind, text

    def get_type(self):
        return self._kind


class DiagnosticTests(unittest.TestCase):
    def test_the_kinds_of_message_and_the_status_texts_are_counted(self):
        connection = ListeningConnection([Message("MISSION_REQUEST"), Message("MISSION_REQUEST"), Message("STATUSTEXT", "Mission rejected")])
        text = sitl_mission.diagnostic(connection, seconds=0.2)
        self.assertIn("target=1/1", text)
        self.assertIn("'MISSION_REQUEST': 2", text)
        self.assertIn("Mission rejected", text)

    def test_a_link_that_fails_while_listening_is_reported_not_raised(self):
        class Broken:
            def recv_match(self, **kwargs):
                raise OSError("link down")

        self.assertIn("link down", sitl_mission.diagnostic(Broken(), seconds=0.2))


class HomeAndMainTests(unittest.TestCase):
    def test_the_home_is_the_reported_position_in_degrees(self):
        self.assertEqual(sitl_mission.read_home(Connection(SimpleNamespace(lat=473977420, lon=85455940)), 1.0), (47.397742, 8.545594))

    def test_no_message_or_a_zero_fix_or_odd_types_give_no_home(self):
        self.assertIsNone(sitl_mission.read_home(Connection(None), 1.0))
        self.assertIsNone(sitl_mission.read_home(Connection(SimpleNamespace(lat=0, lon=0)), 1.0))
        self.assertIsNone(sitl_mission.read_home(Connection(SimpleNamespace(lat="1", lon=2.0)), 1.0))

    def test_bad_options_are_refused(self):
        self.assertEqual(sitl_mission.main(["--connection", "x", "--radius", "0"]), 2)
        self.assertEqual(sitl_mission.main(["--connection", "x", "--open-timeout", "-1"]), 2)

    def test_a_simulator_that_is_not_there_is_a_failed_check_not_a_traceback(self):
        def refuse(url, timeout):
            raise TimeoutError("no heartbeat")

        self.assertEqual(sitl_mission.main(["--connection", "x"], connect=refuse), 1)

    def test_a_vehicle_without_a_position_fails(self):
        self.assertEqual(sitl_mission.main(["--connection", "x", "--position-timeout", "1"], connect=lambda url, timeout: Connection(None)), 1)


if __name__ == "__main__":
    unittest.main()
