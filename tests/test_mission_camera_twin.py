# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Mission upload, geotagged capture and twin feed tests (emulated autopilot)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The mission upload with its geofence, the capture event and the TwinState feed against the MAVLink emulator."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hydra_umc_sdk.twin_state import validate_twin_state

from hydra_umc_bridge_uav import UavDispatch
from hydra_umc_bridge_uav.camera_capture import capture_event, datalake_sample, wait_for_capture
from hydra_umc_bridge_uav.mavlink_mission import MissionUploader, check_items, parse_items
from hydra_umc_bridge_uav.mavlink_transport import FlightEnvelope, MavlinkFlightControl
from hydra_umc_bridge_uav.settings import CameraSettings, MissionSettings, TwinSettings, UavSettings, load_settings
from hydra_umc_bridge_uav.twin_telemetry import TwinFeed

from mavlink_emulator import MavlinkFlightControllerEmulator

HOME = (38.0, -1.0)
ENVELOPE = FlightEnvelope(max_altitude_m=60.0, max_radius_m=500.0, home_lat=HOME[0], home_lon=HOME[1])
ON = MissionSettings(upload_enabled=True, request_timeout_s=0.05, total_timeout_s=2.0)


def plan(*extra):
    return [
        {"kind": "takeoff", "alt_m": 20},
        {"kind": "waypoint", "lat": 38.001, "lon": -1.0, "alt_m": 25, "hold_s": 3},
        *extra,
        {"kind": "return_to_launch"},
    ]


def items_of(raw):
    items, problem = parse_items(raw)
    assert problem is None, problem
    return items


class SettingsTests(unittest.TestCase):
    def test_the_defaults_keep_the_upload_off(self):
        self.assertFalse(UavSettings().mission.upload_enabled)
        self.assertFalse(load_settings(None).mission.upload_enabled)

    def test_the_file_of_the_repository_loads(self):
        path = Path(__file__).resolve().parents[1] / "config" / "uav.json"
        settings = load_settings(path)
        self.assertFalse(settings.mission.upload_enabled)

    def test_unknown_keys_and_bad_values_are_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "uav.json"
            for body in ({"mission": {"upload": True}}, {"nothing": {}}, {"mission": {"max_items": 0}}, {"twin": {"source": "dream"}}, {"camera": {"wait_for_image_s": -1}}, []):
                path.write_text(json.dumps(body), encoding="utf-8")
                with self.assertRaises(ValueError, msg=str(body)):
                    load_settings(path)


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.fc = MavlinkFlightControllerEmulator()
        self.uploader = MissionUploader(ON)

    def test_a_valid_mission_is_uploaded_item_by_item_and_not_started(self):
        result = self.uploader.upload(self.fc, 1, 1, items_of(plan()), ENVELOPE)
        self.assertTrue(result.sent, result.reason)
        self.assertEqual(result.items, 3)
        self.assertEqual([item["command"] for item in self.fc.mission], [22, 16, 20])
        waypoint = self.fc.mission[1]
        self.assertEqual((waypoint["x"], waypoint["y"], waypoint["z"], waypoint["param1"]), (380010000, -10000000, 25.0, 3.0))
        self.assertFalse(self.fc.armed)  # nothing was armed or started
        self.assertEqual(self.fc.commands, [])

    def test_the_upload_is_off_by_default(self):
        result = MissionUploader().upload(self.fc, 1, 1, items_of(plan()), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("switched off", result.reason)
        self.assertEqual(self.fc.mission, [])

    def test_no_envelope_no_upload(self):
        result = self.uploader.upload(self.fc, 1, 1, items_of(plan()), None)
        self.assertFalse(result.sent)
        self.assertIn("flight envelope", result.reason)

    def test_one_point_outside_the_radius_stops_everything_before_anything_is_sent(self):
        far = {"kind": "waypoint", "lat": 38.5, "lon": -1.0, "alt_m": 20}
        result = self.uploader.upload(self.fc, 1, 1, items_of(plan(far)), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("item 2", result.reason)
        self.assertIn("beyond the flight envelope radius", result.reason)
        self.assertEqual(self.fc._mission_queue, [])  # not even a MISSION_COUNT left the bridge

    def test_one_point_above_the_ceiling_is_refused(self):
        high = {"kind": "waypoint", "lat": 38.0, "lon": -1.0, "alt_m": 90}
        self.assertIn("ceiling", self.uploader.upload(self.fc, 1, 1, items_of(plan(high)), ENVELOPE).reason)

    def test_a_landing_point_is_checked_too(self):
        land = {"kind": "land", "lat": 39.0, "lon": -1.0}
        self.assertFalse(self.uploader.upload(self.fc, 1, 1, items_of(plan(land)), ENVELOPE).sent)

    def test_too_many_items_are_refused_by_the_configured_limit(self):
        small = MissionUploader(MissionSettings(upload_enabled=True, max_items=2))
        result = small.upload(self.fc, 1, 1, items_of(plan()), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("more than the 2", result.reason)

    def test_a_vehicle_that_refuses_the_mission_is_reported_not_trusted(self):
        self.fc.mission_ack_result = 13
        result = self.uploader.upload(self.fc, 1, 1, items_of(plan()), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("denied", result.reason)

    def test_a_vehicle_without_room_refuses_at_once(self):
        self.fc.mission_max_items = 2
        result = self.uploader.upload(self.fc, 1, 1, items_of(plan()), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("no space", result.reason)

    def test_a_silent_vehicle_ends_with_a_reason(self):
        self.fc.mission_stall_after = 1
        result = self.uploader.upload(self.fc, 1, 1, items_of(plan()), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("did not ask", result.reason)

    def test_a_request_for_an_item_that_does_not_exist_is_refused(self):
        class Liar(MavlinkFlightControllerEmulator):
            def mission_count_send(self, *args, **kwargs):
                super().mission_count_send(*args, **kwargs)
                self._mission_queue[0].seq = 99

        result = self.uploader.upload(Liar(), 1, 1, items_of(plan()), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("not in the mission", result.reason)

    def test_a_sink_that_raises_is_a_reason_not_an_exception(self):
        class Dead:
            def mission_count_send(self, *a):
                raise OSError("link lost")

        result = self.uploader.upload(Dead(), 1, 1, items_of(plan()), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("link lost", result.reason)

    def test_parse_refuses_what_is_not_a_mission(self):
        for raw in ([], "x", [1], [{"kind": "dance"}], [{"kind": "waypoint", "lat": 1, "lon": 1}], [{"kind": "takeoff", "alt_m": float("nan")}], [{"kind": "takeoff", "alt_m": 5, "x": 1}], [{"kind": "takeoff", "alt_m": True}]):
            self.assertIsNotNone(parse_items(raw)[1], msg=str(raw))

    def test_a_negative_hold_and_a_negative_altitude_are_refused(self):
        self.assertIn("hold", check_items(items_of([{"kind": "waypoint", "lat": 38.0, "lon": -1.0, "alt_m": 10, "hold_s": -1}]), ENVELOPE, ON))
        self.assertIn("altitude", check_items(items_of([{"kind": "takeoff", "alt_m": -5}]), ENVELOPE, ON))


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.fc = MavlinkFlightControllerEmulator()
        self.fc.home_lat_e7, self.fc.home_lon_e7 = 380000000, -10000000

    def capture(self):
        self.fc.armed = True
        self.fc.flight_phase = "HOVERING"
        self.fc.altitude_m = 30.0
        self.fc.last_position_int = {"frame": 6, "x": 380010000, "y": -10000000, "z": 30.0, "yaw": 0.0}
        result = MavlinkFlightControl().send(self.fc, 1, 1, UavDispatch(True, "HOVER_AND_CAPTURE", "ok"), camera_component=100)
        self.assertTrue(result.sent, result.reason)

    def test_a_capture_becomes_a_geotagged_event(self):
        self.capture()
        event = wait_for_capture(self.fc, CameraSettings(wait_for_image_s=0.2))
        self.assertTrue(event["captured"])
        self.assertAlmostEqual(event["lat"], 38.001)
        self.assertAlmostEqual(event["lon"], -1.0)
        self.assertEqual(event["alt_relative_m"], 30.0)
        self.assertTrue(str(event["file_url"]).endswith("IMG_0000.JPG"))

    def test_the_datalake_sample_has_numbers_only_and_the_time_of_the_picture(self):
        self.capture()
        event = wait_for_capture(self.fc, CameraSettings(wait_for_image_s=0.2))
        sample = datalake_sample(event, "uav", 5, sequence=3)
        self.assertEqual(sample["kind"], "uav_image")
        self.assertEqual(sample["timestamp"], 1_800_000_000_000)
        self.assertTrue(all(type(v) is float for v in sample["fields"].values()))
        self.assertEqual(sample["sequence"], 3)

    def test_a_failed_capture_is_never_a_good_picture(self):
        self.fc.camera_result = 0
        self.capture()
        event = wait_for_capture(self.fc, CameraSettings(wait_for_image_s=0.2))
        self.assertFalse(event["captured"])
        self.assertIsNone(datalake_sample(event, "uav", 5))

    def test_a_silent_camera_gives_none(self):
        self.fc.camera_silent = True
        self.capture()
        self.assertIsNone(wait_for_capture(self.fc, CameraSettings(wait_for_image_s=0.1)))

    def test_a_message_without_a_readable_position_is_not_captured(self):
        class Bad:
            lat = "north"
            lon = 0
            capture_result = 1
            image_index = 0

        event = capture_event(Bad())
        self.assertFalse(event["captured"])
        self.assertIn("position", event["reason"])


class TwinTests(unittest.TestCase):
    def setUp(self):
        self.fc = MavlinkFlightControllerEmulator()
        self.now = [1000.0]
        self.sent = []
        self.feed = TwinFeed(*HOME, TwinSettings(robot_id="uav-7", min_interval_s=0.5), self.sent.append, lambda: self.now[0])

    def test_nothing_is_sent_until_both_readings_are_known(self):
        self.assertIsNone(self.feed.observe(self.fc.emit_position(380000000, -10000000, 10.0)))
        self.assertEqual(self.sent, [])
        state = self.feed.observe(self.fc.emit_attitude())
        self.assertIsNotNone(state)
        validate_twin_state(state)
        self.assertEqual(len(self.sent), 1)

    def test_the_position_is_metres_east_north_up_of_home(self):
        self.feed.observe(self.fc.emit_attitude())
        state = self.feed.observe(self.fc.emit_position(380010000, -9990000, 12.5))
        position = state["poses"][0]["pose"]["position"]
        self.assertAlmostEqual(position["y"], 111.19, delta=0.5)  # 0.001 degree of latitude
        self.assertAlmostEqual(position["x"], 87.6, delta=0.5)  # 0.001 degree of longitude at 38 degrees
        self.assertEqual(position["z"], 12.5)
        self.assertEqual(state["source"], "real")
        self.assertEqual(state["robot_id"], "uav-7")

    def test_the_attitude_is_a_unit_quaternion(self):
        self.fc.yaw = 1.5
        self.fc.roll = 0.1
        self.feed.observe(self.fc.emit_position(380000000, -10000000, 5.0))
        state = self.feed.observe(self.fc.emit_attitude())
        q = state["poses"][0]["pose"]["orientation"]
        self.assertAlmostEqual(q["w"] ** 2 + q["x"] ** 2 + q["y"] ** 2 + q["z"] ** 2, 1.0, places=9)

    def test_the_rate_is_limited_and_the_sequence_counts(self):
        self.feed.observe(self.fc.emit_attitude())
        self.assertIsNotNone(self.feed.observe(self.fc.emit_position(380000000, -10000000, 1.0)))
        self.now[0] += 0.1
        self.assertIsNone(self.feed.observe(self.fc.emit_position(380000000, -10000000, 2.0)))
        self.now[0] += 0.6
        state = self.feed.observe(self.fc.emit_position(380000000, -10000000, 3.0))
        self.assertEqual(state["sequence"], 1)

    def test_a_reading_that_cannot_be_used_is_counted_and_dropped(self):
        class Bad:
            def get_type(self):
                return "ATTITUDE"

            roll = float("nan")
            pitch = 0.0
            yaw = 0.0

        self.feed.observe(Bad())
        self.assertEqual(self.feed.refused, 1)
        self.assertIsNone(self.feed.state())

    def test_a_position_outside_the_earth_is_dropped(self):
        self.feed.observe(self.fc.emit_position(950000000, 0, 1.0))
        self.assertEqual(self.feed.refused, 1)

    def test_the_source_is_the_configured_one(self):
        feed = TwinFeed(*HOME, TwinSettings(source="simulated", min_interval_s=0.0), clock=lambda: 5.0)
        feed.observe(self.fc.emit_attitude())
        self.assertEqual(feed.observe(self.fc.emit_position(380000000, -10000000, 1.0))["source"], "simulated")

    def test_a_home_that_is_not_a_place_is_refused(self):
        with self.assertRaises(ValueError):
            TwinFeed(95.0, 0.0)


if __name__ == "__main__":
    unittest.main()
