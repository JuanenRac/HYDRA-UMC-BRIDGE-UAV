# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Safe-state requests, link adapter and envelope boundaries
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""What reaches a vehicle: the commands that bring it to a safe state always go out, everything else is held to the envelope, and the link adapter does not hide a missing vehicle."""

import math
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from hydra_umc_bridge_uav import MavlinkFlightControl, UavCoordinator, UavDispatch, open_mavlink_connection
from hydra_umc_bridge_uav.mavlink_transport import ALTITUDE_FRAMES, FlightEnvelope, _distance_m, load_signing_key


class Ack:
    def __init__(self, command, result=0, system=None):
        self.command, self.result = command, result
        self._system = system

    def get_srcSystem(self):  # noqa: N802 - pymavlink's name
        return 1 if self._system is None else self._system


class Sink:
    def __init__(self, acks=None):
        self.long, self.int_ = [], []
        self.acks = acks

    def command_long_send(self, *args):
        self.long.append(args)

    def command_int_send(self, *args):
        self.int_.append(args)

    def recv_match(self, type, blocking, timeout):
        if self.acks is not None:
            return self.acks.pop(0) if self.acks else None
        if self.long:
            return Ack(self.long[-1][2])
        if self.int_:
            return Ack(self.int_[-1][3])
        return None


def refused(request):
    return UavDispatch(False, request, "cell is FAULT, not READY")


class SafeStateTests(unittest.TestCase):
    def test_the_requests_that_bring_the_vehicle_to_a_safe_state_are_exactly_these_three(self):
        self.assertEqual(UavCoordinator.SAFE_STATE_REQUESTS, frozenset({"RETURN_TO_LAUNCH", "LAND", "DISARM"}))

    def test_each_safe_state_request_is_sent_even_when_the_gate_said_no(self):
        for request, command in (("RETURN_TO_LAUNCH", 20), ("LAND", 21), ("DISARM", 400)):
            sink = Sink()
            result = MavlinkFlightControl().send(sink, 1, 1, refused(request))
            self.assertTrue(result.sent, request)
            self.assertEqual(sink.long[0][2], command)

    def test_every_other_refused_request_sends_nothing(self):
        for request in ("ARM", "TAKEOFF", "GOTO_WAYPOINT", "HOVER_AND_CAPTURE", "UNKNOWN", ""):
            sink = Sink()
            result = MavlinkFlightControl().send(sink, 1, 1, refused(request), confirm_arm=True, waypoint_lat=1, waypoint_lon=1, waypoint_alt_m=5)
            self.assertFalse(result.sent, request)
            self.assertEqual((sink.long, sink.int_), ([], []))

    def test_an_accepted_request_with_no_mapped_command_sends_nothing(self):
        sink = Sink()
        result = MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "SELF_DESTRUCT", "ok"))
        self.assertFalse(result.sent)
        self.assertIn("no real MAV_CMD", result.reason)
        self.assertEqual(sink.long, [])

    def test_a_disarm_is_never_forced(self):
        sink = Sink()
        MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "DISARM", "ok"))
        params = sink.long[0][4:]
        self.assertEqual((params[0], params[1]), (0, 0))

    def test_only_the_literal_true_arms_the_vehicle(self):
        for flag in ("true", "false", 1, [True], object(), None, False, 0):
            sink = Sink()
            result = MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "ARM", "ok"), confirm_arm=flag)
            self.assertFalse(result.sent, repr(flag))
            self.assertEqual(sink.long, [])


class AckTests(unittest.TestCase):
    def test_the_ack_of_this_system_is_used_after_one_from_another_system(self):
        sink = Sink(acks=[Ack(400, 4, system=9), Ack(400, 0, system=1)])
        result = MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "ARM", "ok"), confirm_arm=True)
        self.assertTrue(result.sent)

    def test_every_result_other_than_accepted_is_a_failure_that_names_it(self):
        for code in (1, 2, 3, 4, 6, 99):
            sink = Sink(acks=[Ack(400, code, system=1)])
            result = MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "ARM", "ok"), confirm_arm=True)
            self.assertFalse(result.sent, code)
            self.assertIn(str(code), result.reason)

    def test_a_vehicle_that_never_answers_is_not_a_success(self):
        sink = Sink(acks=[])
        result = MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "LAND", "ok"), ack_timeout_seconds=0.01)
        self.assertFalse(result.sent)


class EnvelopeTests(unittest.TestCase):
    envelope = FlightEnvelope(max_altitude_m=50, max_radius_m=1000, home_lat=40.0, home_lon=-3.0)

    def waypoint(self, lat, lon, alt, **kw):
        sink = Sink()
        result = MavlinkFlightControl().send(
            sink, 1, 1, UavDispatch(True, "GOTO_WAYPOINT", "ok"),
            waypoint_lat=lat, waypoint_lon=lon, waypoint_alt_m=alt, envelope=self.envelope, **kw,
        )
        return result, sink

    def test_the_altitude_ceiling_is_inclusive(self):
        self.assertTrue(self.waypoint(40.0, -3.0, 50)[0].sent)
        result, sink = self.waypoint(40.0, -3.0, 50.01)
        self.assertFalse(result.sent)
        self.assertEqual(sink.int_, [])

    def test_the_radius_is_measured_from_home(self):
        near = 40.0 + 900 / 111_195
        far = 40.0 + 1100 / 111_195
        self.assertTrue(self.waypoint(near, -3.0, 10)[0].sent)
        result, sink = self.waypoint(far, -3.0, 10)
        self.assertFalse(result.sent)
        self.assertIn("flight envelope", result.reason)
        self.assertEqual(sink.int_, [])

    def test_an_absolute_altitude_cannot_be_checked_against_the_envelope_so_it_is_refused(self):
        result, sink = self.waypoint(40.0, -3.0, 10, altitude_frame="amsl")
        self.assertFalse(result.sent)
        self.assertEqual(sink.int_, [])

    def test_without_an_envelope_an_amsl_waypoint_uses_the_global_frame(self):
        sink = Sink()
        MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "GOTO_WAYPOINT", "ok"), waypoint_lat=40, waypoint_lon=-3, waypoint_alt_m=900, altitude_frame="amsl")
        self.assertEqual(sink.int_[0][2], ALTITUDE_FRAMES["amsl"])

    def test_nonsense_coordinates_are_refused_before_any_send(self):
        bad = [(91, 0, 5), (-91, 0, 5), (0, 181, 5), (0, -181, 5), (math.nan, 0, 5), (0, math.inf, 5), (0, 0, 0), (0, 0, -1), (0, 0, math.nan), (True, 0, 5), (0, 0, True), ("40", 0, 5)]
        for lat, lon, alt in bad:
            sink = Sink()
            result = MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "GOTO_WAYPOINT", "ok"), waypoint_lat=lat, waypoint_lon=lon, waypoint_alt_m=alt)
            self.assertFalse(result.sent, (lat, lon, alt))
            self.assertEqual(sink.int_, [])

    def test_the_poles_and_the_date_line_are_valid(self):
        for lat, lon in ((90, 180), (-90, -180), (0, 0)):
            sink = Sink()
            result = MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "GOTO_WAYPOINT", "ok"), waypoint_lat=lat, waypoint_lon=lon, waypoint_alt_m=5)
            self.assertTrue(result.sent, (lat, lon))

    def test_the_target_coordinates_are_sent_as_integers_in_1e7_degrees(self):
        sink = Sink()
        MavlinkFlightControl().send(sink, 1, 1, UavDispatch(True, "GOTO_WAYPOINT", "ok"), waypoint_lat=40.1234567, waypoint_lon=-3.7654321, waypoint_alt_m=12)
        params = sink.int_[0][6:]
        self.assertEqual((params[4], params[5], params[6]), (401234567, -37654321, 12))

    def test_an_envelope_with_bad_numbers_cannot_be_built(self):
        for kwargs in (
            dict(max_altitude_m=0, max_radius_m=1, home_lat=0, home_lon=0),
            dict(max_altitude_m=1, max_radius_m=-1, home_lat=0, home_lon=0),
            dict(max_altitude_m=math.nan, max_radius_m=1, home_lat=0, home_lon=0),
            dict(max_altitude_m=True, max_radius_m=1, home_lat=0, home_lon=0),
            dict(max_altitude_m=1, max_radius_m=1, home_lat=91, home_lon=0),
            dict(max_altitude_m=1, max_radius_m=1, home_lat=0, home_lon=math.inf),
        ):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                FlightEnvelope(**kwargs)

    def test_the_great_circle_distance_is_symmetric_and_zero_at_the_same_point(self):
        self.assertEqual(_distance_m(10, 10, 10, 10), 0)
        self.assertAlmostEqual(_distance_m(0, 0, 0, 1), _distance_m(0, 1, 0, 0), places=6)
        self.assertAlmostEqual(_distance_m(0, 0, 0, 1), 111_195, delta=200)
        self.assertGreater(_distance_m(0, 0, 0, 180), 20_000_000)


class LinkAdapterTests(unittest.TestCase):
    def fake_pymavlink(self, heartbeat):
        connection = mock.MagicMock()
        connection.target_system = 7
        connection.target_component = 3
        connection.wait_heartbeat.return_value = heartbeat
        module = types.ModuleType("pymavlink")
        module.mavutil = types.SimpleNamespace(mavlink_connection=mock.MagicMock(return_value=connection))
        return module, connection

    def test_without_a_heartbeat_the_link_is_closed_and_refused(self):
        module, connection = self.fake_pymavlink(None)
        with mock.patch.dict(sys.modules, {"pymavlink": module}):
            with self.assertRaises(RuntimeError) as context:
                open_mavlink_connection("udp:127.0.0.1:14550", heartbeat_timeout_seconds=0.5)
        self.assertIn("no MAVLink HEARTBEAT", str(context.exception))
        connection.close.assert_called_once()

    def test_with_a_heartbeat_the_adapter_reports_the_vehicle_ids_and_forwards_both_directions(self):
        module, connection = self.fake_pymavlink(object())
        with mock.patch.dict(sys.modules, {"pymavlink": module}):
            sink = open_mavlink_connection("udp:127.0.0.1:14550")
        self.assertEqual((sink.target_system, sink.target_component), (7, 3))
        sink.command_long_send(1, 2, 3)
        connection.mav.command_long_send.assert_called_once_with(1, 2, 3)
        sink.command_int_send(4)
        connection.mav.command_int_send.assert_called_once_with(4)
        sink.recv_match("COMMAND_ACK", True, 0.5)
        connection.recv_match.assert_called_once_with(type="COMMAND_ACK", blocking=True, timeout=0.5)

    def test_a_connection_without_a_close_method_is_still_refused_cleanly(self):
        module, connection = self.fake_pymavlink(None)
        del connection.close
        connection.mock_add_spec(["wait_heartbeat", "target_system", "target_component"])
        with mock.patch.dict(sys.modules, {"pymavlink": module}):
            with self.assertRaises(RuntimeError):
                open_mavlink_connection("udp:127.0.0.1:14550")


class BusyLinkTests(unittest.TestCase):
    def test_acks_from_other_systems_cannot_keep_the_wait_going_past_its_deadline(self):
        import threading

        class Flood:
            """A shared link where another vehicle never stops acknowledging its own commands."""

            def command_long_send(self, *args):
                pass

            def recv_match(self, type, blocking, timeout):
                return Ack(400, 0, system=9)

        outcome = {}

        def run():
            outcome["result"] = MavlinkFlightControl().send(Flood(), 1, 1, UavDispatch(True, "LAND", "ok"), ack_timeout_seconds=0.05)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "the wait for an acknowledgement never ended")
        self.assertFalse(outcome["result"].sent)


class SigningTests(unittest.TestCase):
    def fake_pymavlink(self, heartbeat):
        connection = mock.MagicMock()
        connection.target_system = 7
        connection.target_component = 3
        connection.wait_heartbeat.return_value = heartbeat
        module = types.ModuleType("pymavlink")
        module.mavutil = types.SimpleNamespace(mavlink_connection=mock.MagicMock(return_value=connection))
        return module, connection

    def test_a_key_makes_the_link_sign_and_refuse_unsigned_messages_by_default(self):
        module, connection = self.fake_pymavlink(object())
        key = bytes(range(32))
        with mock.patch.dict(sys.modules, {"pymavlink": module}):
            open_mavlink_connection("udp:127.0.0.1:14550", signing_key=key)
        args, kwargs = connection.setup_signing.call_args
        self.assertEqual(args[0], key)
        self.assertTrue(kwargs["sign_outgoing"])
        self.assertFalse(kwargs["allow_unsigned_callback"](object()))

    def test_allow_unsigned_accepts_a_vehicle_that_does_not_sign_yet(self):
        module, connection = self.fake_pymavlink(object())
        with mock.patch.dict(sys.modules, {"pymavlink": module}):
            open_mavlink_connection("udp:127.0.0.1:14550", signing_key=bytes(32), allow_unsigned=True)
        self.assertTrue(connection.setup_signing.call_args.kwargs["allow_unsigned_callback"](object()))

    def test_without_a_key_nothing_is_signed(self):
        module, connection = self.fake_pymavlink(object())
        with mock.patch.dict(sys.modules, {"pymavlink": module}):
            open_mavlink_connection("udp:127.0.0.1:14550")
        connection.setup_signing.assert_not_called()

    def test_a_key_of_the_wrong_length_is_refused_before_a_link_is_opened(self):
        module, connection = self.fake_pymavlink(object())
        with mock.patch.dict(sys.modules, {"pymavlink": module}):
            with self.assertRaises(ValueError):
                open_mavlink_connection("udp:127.0.0.1:14550", signing_key=b"short")
        module.mavutil.mavlink_connection.assert_not_called()

    def test_the_key_file_holds_raw_bytes_or_hexadecimal_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = os.path.join(folder, "raw.key")
            with open(raw, "wb") as handle:
                handle.write(bytes(range(32)))
            hexa = os.path.join(folder, "hex.key")
            with open(hexa, "w", encoding="ascii") as handle:
                handle.write(bytes(range(32)).hex() + chr(10))
            bad = os.path.join(folder, "bad.key")
            with open(bad, "w", encoding="ascii") as handle:
                handle.write("not a key at all")
            self.assertEqual(load_signing_key(raw), bytes(range(32)))
            self.assertEqual(load_signing_key(hexa), bytes(range(32)))
            with self.assertRaises(ValueError) as context:
                load_signing_key(bad)
            self.assertNotIn("not a key", str(context.exception))
            with self.assertRaises(ValueError):
                load_signing_key(os.path.join(folder, "missing.key"))


if __name__ == "__main__":
    unittest.main()
