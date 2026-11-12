# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Mission upload edge cases
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The corners of the mission upload: items that are not a mission, checks that refuse before anything is sent, and a vehicle that answers badly. A scripted sink stands in
for the autopilot (evidence=simulator); a fixed seed drives the random part."""
from __future__ import annotations

import math
import random
import unittest

from hydra_umc_bridge_uav.mavlink_mission import (
    MAV_CMD_NAV_LAND,
    MAV_CMD_NAV_RETURN_TO_LAUNCH,
    MAV_CMD_NAV_TAKEOFF,
    MAV_CMD_NAV_WAYPOINT,
    MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
    MAV_FRAME_MISSION,
    MissionItem,
    MissionUploader,
    _item_message,
    check_items,
    parse_items,
)
from hydra_umc_bridge_uav.mavlink_transport import FlightEnvelope
from hydra_umc_bridge_uav.settings import MissionSettings

ENVELOPE = FlightEnvelope(max_altitude_m=60.0, max_radius_m=500.0, home_lat=38.0, home_lon=-1.0)
ON = MissionSettings(upload_enabled=True, request_timeout_s=0.01, total_timeout_s=2.0)


class Msg:
    def __init__(self, **fields: object) -> None:
        self.__dict__.update(fields)


class Sink:
    """Answers recv_match from a script in order: the next (name, message or None) is given only to the call that asks for that name; the rest of the calls get nothing."""

    def __init__(self, script: list[tuple[str, object]] | None = None, fail_on_send: bool = False) -> None:
        self.script = list(script or [])
        self.sent: list[tuple] = []
        self.counts: list[int] = []
        self.fail_on_send = fail_on_send

    def mission_count_send(self, system: int, component: int, count: int, kind: int) -> None:
        if self.fail_on_send:
            raise OSError("link lost")
        self.counts.append(count)

    def mission_item_int_send(self, *args: object) -> None:
        self.sent.append(args)

    def recv_match(self, name: "str | list[str]", blocking: bool, timeout: float) -> object:
        names = [name] if isinstance(name, str) else list(name)
        if self.script and self.script[0][0] in names:
            kind, message = self.script.pop(0)
            if message is not None and not hasattr(message, "get_type"):
                message.get_type = lambda kind=kind: kind
            return message
        return None


def good_items() -> list[MissionItem]:
    items, problem = parse_items([{"kind": "takeoff", "alt_m": 20}, {"kind": "waypoint", "lat": 38.001, "lon": -1.0, "alt_m": 25}, {"kind": "return_to_launch"}])
    assert problem is None
    return items


class ParseTests(unittest.TestCase):
    def test_what_is_not_a_list_of_objects_is_not_a_mission(self) -> None:
        for raw in (None, "takeoff", b"x", [], {}, 5, [1], ["takeoff"], [None]):
            items, problem = parse_items(raw)
            self.assertEqual(items, [], msg=repr(raw))
            self.assertTrue(problem, msg=repr(raw))

    def test_an_unknown_kind_or_key_is_named(self) -> None:
        self.assertIn("kind must be one of", parse_items([{"kind": "orbit"}])[1])
        self.assertIn("unknown keys speed", parse_items([{"kind": "return_to_launch", "speed": 3}])[1])

    def test_each_kind_needs_its_own_numbers(self) -> None:
        for entry, name in (
            ({"kind": "takeoff"}, "alt_m"),
            ({"kind": "waypoint", "lat": 1, "lon": 2}, "alt_m"),
            ({"kind": "land", "lat": 1}, "lon"),
            ({"kind": "waypoint", "lat": "1", "lon": 2, "alt_m": 3}, "lat"),
            ({"kind": "waypoint", "lat": True, "lon": 2, "alt_m": 3}, "lat"),
            ({"kind": "waypoint", "lat": 1, "lon": 2, "alt_m": math.nan}, "alt_m"),
            ({"kind": "waypoint", "lat": math.inf, "lon": 2, "alt_m": 3}, "lat"),
        ):
            items, problem = parse_items([entry])
            self.assertEqual(items, [])
            self.assertIn(f"{name} must be a finite number", problem or "", msg=str(entry))

    def test_an_optional_number_that_is_present_must_be_finite_too(self) -> None:
        self.assertIn("hold_s must be a finite number", parse_items([{"kind": "return_to_launch", "hold_s": "3"}])[1])
        self.assertIn("alt_m must be a finite number", parse_items([{"kind": "land", "lat": 1, "lon": 2, "alt_m": math.nan}])[1])

    def test_a_hold_time_defaults_to_zero_and_is_a_float(self) -> None:
        items, problem = parse_items([{"kind": "takeoff", "alt_m": 5}, {"kind": "waypoint", "lat": 1, "lon": 2, "alt_m": 3, "hold_s": 4}])
        self.assertIsNone(problem)
        self.assertEqual([i.hold_s for i in items], [0.0, 4.0])
        self.assertIsInstance(items[1].hold_s, float)


class CheckTests(unittest.TestCase):
    def test_nothing_is_uploaded_unless_the_switch_the_envelope_and_the_items_are_all_there(self) -> None:
        items = good_items()
        self.assertIn("switched off", check_items(items, ENVELOPE, MissionSettings()))
        self.assertIn("flight envelope", check_items(items, None, ON))
        self.assertIn("no items", check_items([], ENVELOPE, ON))
        self.assertIn("more than the 2", check_items(items, ENVELOPE, MissionSettings(upload_enabled=True, max_items=2)))
        self.assertIsNone(check_items(items, ENVELOPE, ON))

    def test_a_position_that_is_missing_or_not_on_earth_is_refused(self) -> None:
        self.assertIn("position is missing", check_items([MissionItem("waypoint", None, 1.0, 10.0)], ENVELOPE, ON))
        self.assertIn("position is missing", check_items([MissionItem("land", 1.0, None)], ENVELOPE, ON))
        for lat, lon in ((91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)):
            self.assertIn("outside the range", check_items([MissionItem("land", lat, lon)], ENVELOPE, ON), msg=f"{lat},{lon}")

    def test_a_point_outside_the_envelope_or_too_high_or_not_above_the_ground_is_refused_with_its_index(self) -> None:
        far = MissionItem("waypoint", 39.0, -1.0, 20.0)
        self.assertTrue(check_items([MissionItem("takeoff", None, None, 10.0), far], ENVELOPE, ON).startswith("item 1:"))
        self.assertTrue(check_items([MissionItem("takeoff", None, None, 500.0)], ENVELOPE, ON).startswith("item 0:"))
        for altitude in (0.0, -5.0):
            self.assertIn("above the takeoff point", check_items([MissionItem("waypoint", 38.0, -1.0, altitude)], ENVELOPE, ON))
        self.assertIn("hold time", check_items([MissionItem("waypoint", 38.0, -1.0, 10.0, hold_s=-1.0)], ENVELOPE, ON))

    def test_the_edge_of_the_range_is_inside(self) -> None:
        wide = FlightEnvelope(max_altitude_m=60.0, max_radius_m=20_000_000.0, home_lat=0.0, home_lon=0.0)
        self.assertIsNone(check_items([MissionItem("land", 90.0, 180.0), MissionItem("land", -90.0, -180.0)], wide, ON))


class MessageTests(unittest.TestCase):
    def test_each_kind_becomes_its_own_message(self) -> None:
        self.assertEqual(_item_message(MissionItem("takeoff", None, None, 20.0)), (MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, MAV_CMD_NAV_TAKEOFF, 0.0, 0.0, 0, 0, 20.0))
        self.assertEqual(_item_message(MissionItem("waypoint", 38.5, -1.25, 25.0, 3.0)), (MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, MAV_CMD_NAV_WAYPOINT, 3.0, 0.0, 385000000, -12500000, 25.0))
        self.assertEqual(_item_message(MissionItem("land", 38.5, -1.25)), (MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, MAV_CMD_NAV_LAND, 0.0, 0.0, 385000000, -12500000, 0.0))
        self.assertEqual(_item_message(MissionItem("return_to_launch")), (MAV_FRAME_MISSION, MAV_CMD_NAV_RETURN_TO_LAUNCH, 0.0, 0.0, 0, 0, 0.0))

    def test_a_takeoff_without_an_altitude_is_sent_at_zero(self) -> None:
        self.assertEqual(_item_message(MissionItem("takeoff"))[6], 0.0)


def request(seq: object) -> tuple[str, object]:
    return ("MISSION_REQUEST_INT", Msg(seq=seq))


def ack(kind: int) -> tuple[str, object]:
    return ("MISSION_ACK", Msg(type=kind))


class UploadTests(unittest.TestCase):
    def run_upload(self, sink: Sink, items: list[MissionItem] | None = None, **settings: object):
        merged = MissionSettings(upload_enabled=True, request_timeout_s=0.01, total_timeout_s=settings.pop("total", 2.0), **settings)  # type: ignore[arg-type]
        return MissionUploader(merged).upload(sink, 1, 1, items or good_items(), ENVELOPE)

    def test_a_refused_mission_sends_nothing_at_all(self) -> None:
        sink = Sink()
        result = MissionUploader(MissionSettings()).upload(sink, 1, 1, good_items(), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertEqual((sink.counts, sink.sent), ([], []))

    def test_the_whole_exchange_succeeds_only_with_every_item_served_and_an_accepted_ack(self) -> None:
        sink = Sink([request(0), request(1), request(2), ack(0)])
        result = self.run_upload(sink)
        self.assertTrue(result.sent, result.reason)
        self.assertEqual((result.items, sink.counts, [m[2] for m in sink.sent]), (3, [3], [0, 1, 2]))

    def test_an_accepted_ack_before_every_item_was_asked_for_is_not_a_success(self) -> None:
        result = self.run_upload(Sink([request(0), ack(0)]))
        self.assertFalse(result.sent)
        self.assertIn("before it had every item", result.reason)
        self.assertEqual(result.items, 1)

    def test_a_refusal_is_named_and_an_unknown_code_is_shown_as_it_is(self) -> None:
        self.assertIn("no space", self.run_upload(Sink([ack(4)])).reason)
        self.assertIn("result 99", self.run_upload(Sink([ack(99)])).reason)
        unreadable = self.run_upload(Sink([("MISSION_ACK", Msg())]))
        self.assertIn("unreadable result", unreadable.reason)

    def test_a_request_for_an_item_that_does_not_exist_ends_the_upload(self) -> None:
        for seq in (3, -1, "1", 1.0, None, True):
            result = self.run_upload(Sink([request(seq)]))
            self.assertFalse(result.sent, msg=repr(seq))
            self.assertTrue("not in the mission" in result.reason or "accepted" not in result.reason, msg=repr(seq))
        self.assertIn("not in the mission", self.run_upload(Sink([request(7)])).reason)

    def test_a_silent_vehicle_ends_the_upload_with_what_was_served(self) -> None:
        result = self.run_upload(Sink([request(0)]))
        self.assertFalse(result.sent)
        self.assertIn("did not ask for the next item", result.reason)
        self.assertEqual(result.items, 1)

    def test_an_ack_that_arrives_right_after_the_last_request_is_taken(self) -> None:
        sink = Sink([request(0), request(1), request(2), ack(0)])
        result = self.run_upload(sink)
        self.assertTrue(result.sent, result.reason)

    def test_the_older_request_of_a_vehicle_that_has_not_seen_an_integer_item_is_served_like_the_new_one(self) -> None:
        sink = Sink([("MISSION_REQUEST", Msg(seq=0)), ("MISSION_REQUEST", Msg(seq=1)), ("MISSION_REQUEST_INT", Msg(seq=2)), ack(0)])
        result = self.run_upload(sink)
        self.assertTrue(result.sent, result.reason)
        self.assertEqual([call[2] for call in sink.sent], [0, 1, 2])

    def test_neither_answer_is_dropped_while_waiting_for_the_other(self) -> None:
        """A real link keeps only the kind it is asked for and drops the rest: asking for the acknowledgement before the next request lost that request."""

        class DroppingSink(Sink):
            def __init__(self, queue):
                super().__init__()
                self.queue = list(queue)
                self.asked = []

            def recv_match(self, name, blocking, timeout):
                self.asked.append(name)
                wanted = [name] if isinstance(name, str) else list(name)
                while self.queue:
                    kind, message = self.queue.pop(0)
                    if kind in wanted:
                        message.get_type = lambda kind=kind: kind
                        return message
                return None

        sink = DroppingSink([("MISSION_REQUEST_INT", request(0)[1]), ("MISSION_REQUEST_INT", request(1)[1]), ("MISSION_REQUEST_INT", request(2)[1]), ("MISSION_ACK", ack(0)[1])])
        result = self.run_upload(sink)
        self.assertTrue(result.sent, result.reason)
        self.assertEqual(result.items, 3)
        self.assertTrue(all(isinstance(asked, list) and set(asked) == {"MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"} for asked in sink.asked))

    def test_the_total_time_is_a_limit_even_when_the_vehicle_keeps_asking(self) -> None:
        now = [0.0]

        def clock() -> float:
            now[0] += 1.0
            return now[0]

        class Endless(Sink):
            def recv_match(self, name: str, blocking: bool, timeout: float) -> object:
                return Msg(seq=0) if name == "MISSION_REQUEST_INT" else None

        uploader = MissionUploader(MissionSettings(upload_enabled=True, request_timeout_s=0.01, total_timeout_s=5.0), clock=clock)
        result = uploader.upload(Endless(), 1, 1, good_items(), ENVELOPE)
        self.assertFalse(result.sent)
        self.assertIn("in time", result.reason)

    def test_a_link_that_fails_is_a_reason_not_an_exception(self) -> None:
        result = self.run_upload(Sink(fail_on_send=True))
        self.assertFalse(result.sent)
        self.assertIn("OSError: link lost", result.reason)

    def test_whatever_the_vehicle_says_the_upload_ends_with_a_verdict_and_never_raises(self) -> None:
        rng = random.Random(20260)
        choices: list[tuple[str, object]] = [request(0), request(1), request(2), request(9), request(None), ack(0), ack(1), ack(13), ("MISSION_ACK", None), ("MISSION_REQUEST_INT", Msg())]
        for _ in range(200):
            script = [rng.choice(choices) for _ in range(rng.randint(0, 8))]
            result = self.run_upload(Sink(script))
            self.assertIsInstance(result.sent, bool)
            self.assertTrue(result.reason)
            if result.sent:
                self.assertEqual(result.items, 3)


if __name__ == "__main__":
    unittest.main()
