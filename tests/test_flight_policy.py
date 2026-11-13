# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Flight policy tests (required envelope, signed human confirmation, flight phases)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The policy of the job entry against the autopilot emulator. Simulator evidence only: nothing has flown."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict

from hydra_umc_bridge_uav import cli
from hydra_umc_bridge_uav.coordinator import UavCoordinator, UavDispatch
from hydra_umc_bridge_uav.flight_policy import CONFIRMATION_FIELD, FlightPolicy
from hydra_umc_bridge_uav.human_confirmation import ConfirmationError, ConfirmationVerifier, load_key, make_token
from hydra_umc_bridge_uav.job_entry import JOB_TOPIC, UavJobEntry
from hydra_umc_bridge_uav.mavlink_mission import MissionUploader
from hydra_umc_bridge_uav.mavlink_transport import FlightEnvelope, MavlinkFlightControl, MavlinkSendResult
from hydra_umc_bridge_uav.settings import ConfirmationSettings, EnvelopeSettings, MissionSettings, PhaseSettings, UavSettings, load_settings

from mavlink_emulator import MavlinkFlightControllerEmulator

KEY = b"k" * 40
NOW = [1_000_000.0]
HOME = (38.0, -1.0)
ENVELOPE = EnvelopeSettings(home_lat=HOME[0], home_lon=HOME[1])


def clock() -> float:
    return NOW[0]


def token(action: str, job_id: str = "job-1", jti: str = "t1", ttl: float = 60.0, key: bytes = KEY, role: str = "operator") -> str:
    return make_token(key, operator="ana", role=role, action=action, job_id=job_id, token_id=jti, issued_at=NOW[0], ttl_s=ttl)


class Client:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str) -> object:
        self.sent.append((topic, payload))
        return object()

    def last(self) -> dict:
        return json.loads(self.sent[-1][1])


def payload(phase: JobPhase, key: str, tok: str | None = None, parameters: dict | None = None, job_id: str = "job-1") -> bytes:
    raw = job_to_dict(BridgeJob(job_id, key, "uav-1", phase, MachineState.IDLE, parameters or {}))
    if tok is not None:
        raw[CONFIRMATION_FIELD] = tok
    return json.dumps(raw).encode()


def policy(**kw) -> FlightPolicy:
    verifier = kw.pop("verifier", ConfirmationVerifier(KEY, clock=clock))
    return FlightPolicy(kw.pop("envelope", ENVELOPE), kw.pop("confirmation", ConfirmationSettings()), kw.pop("phases", PhaseSettings()), verifier, clock=clock, **kw)


def entry(pol: FlightPolicy | None, machine=MachineState.IDLE, sender=None) -> UavJobEntry:
    return UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: machine, sender, enabled=True, policy=pol)


class TokenTests(unittest.TestCase):
    def setUp(self) -> None:
        NOW[0] = 1_000_000.0
        self.verifier = ConfirmationVerifier(KEY, clock=clock)

    def check(self, tok, action="TAKEOFF", job_id="job-1"):
        return self.verifier.verify(tok, action=action, job_id=job_id)

    def test_a_valid_token_is_accepted_once(self) -> None:
        confirmation = self.check(token("TAKEOFF"))
        self.assertEqual((confirmation.operator, confirmation.role), ("ana", "operator"))
        self.assertTrue(self.verifier.consume(confirmation))
        with self.assertRaisesRegex(ConfirmationError, "already used"):
            self.check(token("TAKEOFF"))
        self.assertFalse(self.verifier.consume(confirmation))

    def test_verifying_does_not_use_the_token_up(self) -> None:
        self.check(token("TAKEOFF"))
        self.check(token("TAKEOFF"))

    def test_every_way_to_be_wrong_is_refused(self) -> None:
        cases = {
            "missing": (None, "none came"),
            "empty": ("  ", "none came"),
            "not a jwt": ("abc", "not a JWT"),
            "garbage parts": ("a.b.c", "not a JWT"),
            "other key": (token("TAKEOFF", key=b"z" * 40), "signature"),
            "other action": (token("GOTO_WAYPOINT"), "not for TAKEOFF"),
            "other job": (token("TAKEOFF", job_id="job-9"), "another job"),
            "role": (token("TAKEOFF", role="guest"), "role"),
            "too long": (token("TAKEOFF", ttl=3600), "longer than"),
            "expired": (token("TAKEOFF", ttl=-1), "expired"),
        }
        for name, (tok, why) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ConfirmationError, why):
                self.check(tok)

    def test_the_none_algorithm_and_a_token_of_another_purpose_are_refused(self) -> None:
        import base64

        def b64(value: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

        unsigned = f"{b64({'alg': 'none'})}.{b64({'purpose': 'human-confirmation'})}."
        with self.assertRaisesRegex(ConfirmationError, "HS256"):
            self.check(unsigned)
        import hashlib
        import hmac

        head, body = b64({"alg": "HS256"}), b64({"purpose": "identity", "sub": "a", "role": "operator", "act": "TAKEOFF", "job": "job-1", "jti": "x", "iat": NOW[0], "exp": NOW[0] + 5})
        signature = base64.urlsafe_b64encode(hmac.new(KEY, f"{head}.{body}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        with self.assertRaisesRegex(ConfirmationError, "not a human confirmation"):
            self.check(f"{head}.{body}.{signature}")

    def test_a_token_from_the_future_is_refused(self) -> None:
        future = make_token(KEY, operator="a", role="operator", action="TAKEOFF", job_id="job-1", token_id="f", issued_at=NOW[0] + 600, ttl_s=60)
        with self.assertRaisesRegex(ConfirmationError, "future"):
            self.check(future)

    def test_the_key_must_be_long_and_readable(self) -> None:
        with self.assertRaises(ValueError):
            ConfirmationVerifier(b"short")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "key")
            path.write_text("short", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "at least"):
                load_key(str(path))
            path.write_text("x" * 40 + "\n", encoding="utf-8")
            self.assertEqual(load_key(str(path)), b"x" * 40)
            with self.assertRaisesRegex(ValueError, "cannot be read"):
                load_key(str(Path(folder, "none")))

    def test_remembered_tokens_are_bounded_and_fail_closed(self) -> None:
        import hydra_umc_bridge_uav.human_confirmation as module

        original = module.MAX_REMEMBERED
        module.MAX_REMEMBERED = 2
        try:
            for index in range(2):
                self.assertTrue(self.verifier.consume(self.check(token("TAKEOFF", jti=f"j{index}"))))
            self.assertFalse(self.verifier.consume(self.check(token("TAKEOFF", jti="j9"))))
            NOW[0] += 1000
            self.assertTrue(self.verifier.consume(self.check(token("TAKEOFF", jti="j9"))))
        finally:
            module.MAX_REMEMBERED = original


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        NOW[0] = 1_000_000.0

    def test_without_an_envelope_a_flight_request_is_refused(self) -> None:
        client, bridge = Client(), entry(policy(envelope=EnvelopeSettings()))
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k1", token("TAKEOFF")))
        self.assertFalse(client.last()["allowed"])
        self.assertIn("no flight envelope", client.last()["reason"])
        self.assertEqual(client.last()["mode"], "refused")

    def test_without_a_confirmation_it_is_refused_and_the_key_stays_free(self) -> None:
        client, bridge = Client(), entry(policy())
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k1"))
        self.assertFalse(client.last()["allowed"])
        self.assertIn("confirmation", client.last()["reason"])
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k1", token("TAKEOFF")))
        self.assertTrue(client.last()["allowed"])
        self.assertEqual(client.last()["request"], "TAKEOFF")

    def test_a_token_works_once_and_a_refusal_for_another_reason_does_not_burn_it(self) -> None:
        client = Client()
        shared = policy()
        same = token("TAKEOFF")
        busy = entry(shared, machine=MachineState.RUNNING)  # the gate refuses: the vehicle is not idle
        busy.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k0", same))
        self.assertFalse(client.last()["allowed"])
        self.assertIn("not IDLE", client.last()["reason"])
        bridge = entry(shared)
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k1", same))
        self.assertTrue(client.last()["allowed"])
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k2", same))
        self.assertFalse(client.last()["allowed"])
        self.assertIn("already used", client.last()["reason"])

    def test_safe_state_requests_need_nothing(self) -> None:
        client, bridge = Client(), entry(policy(envelope=EnvelopeSettings()), machine=MachineState.RUNNING)
        for phase, key in ((JobPhase.COMPLETE, "k1"), (JobPhase.ABORT, "k2")):
            bridge.on_message(client, JOB_TOPIC, payload(phase, key))
            self.assertTrue(client.last()["allowed"], phase)
            self.assertEqual(client.last()["request"], "RETURN_TO_LAUNCH")

    def test_values_outside_the_envelope_are_refused(self) -> None:
        client, bridge = Client(), entry(policy())
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k1", token("TAKEOFF"), {"takeoff_altitude_m": "80"}))
        self.assertIn("ceiling", client.last()["reason"])
        far = {"waypoint_lat": "38.5", "waypoint_lon": "-1.0", "waypoint_alt_m": "10"}
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.PROCESS, "k2", token("GOTO_WAYPOINT"), far))
        self.assertIn("radius", client.last()["reason"])
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.PROCESS, "k3", token("GOTO_WAYPOINT"), {"waypoint_alt_m": "abc"}))
        self.assertIn("not a number", client.last()["reason"])
        near = {"waypoint_lat": "38.0003", "waypoint_lon": "-1.0", "waypoint_alt_m": "10"}
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.PROCESS, "k4", token("GOTO_WAYPOINT", jti="t4"), near))
        self.assertTrue(client.last()["allowed"])

    def test_a_missing_key_refuses_the_request_instead_of_skipping_the_check(self) -> None:
        client, bridge = Client(), entry(policy(verifier=None))
        bridge.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k1", token("TAKEOFF")))
        self.assertIn("no confirmation key", client.last()["reason"])

    def test_the_switches_can_be_turned_off_one_by_one(self) -> None:
        client = Client()
        loose = entry(policy(envelope=EnvelopeSettings(required=False), confirmation=ConfirmationSettings(required=False)))
        loose.on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k1"))
        self.assertTrue(client.last()["allowed"])

    def test_without_a_policy_the_entry_is_as_it_was(self) -> None:
        client = Client()
        entry(None).on_message(client, JOB_TOPIC, payload(JobPhase.LOAD, "k1"))
        self.assertTrue(client.last()["allowed"])


class ChainTests(unittest.TestCase):
    """C16: a go-to or a hover with the vehicle in the air."""

    def setUp(self) -> None:
        NOW[0] = 1_000_000.0
        self.client = Client()
        self.sent: list[UavDispatch] = []
        self.ok = True

    def sender(self, dispatch: UavDispatch) -> object:
        self.sent.append(dispatch)
        return MavlinkSendResult(self.ok, "ok" if self.ok else "the vehicle said no")

    def flying(self, **phases) -> UavJobEntry:
        return entry(policy(phases=PhaseSettings(**phases)), machine=MachineState.RUNNING, sender=self.sender)

    def start_flight(self, bridge: UavJobEntry) -> None:
        # the takeoff itself starts from the ground: an entry that sees IDLE accepts it and notes the flight
        ground = [MachineState.IDLE]
        bridge._machine_state = lambda: ground[0]
        bridge.on_message(self.client, JOB_TOPIC, payload(JobPhase.LOAD, "t", token("TAKEOFF", jti="a")))
        self.assertTrue(self.client.last()["allowed"], self.client.last())
        bridge._machine_state = lambda: MachineState.RUNNING

    def goto(self, bridge: UavJobEntry, key: str, jti: str, tok: str | None = "") -> dict:
        bridge.on_message(self.client, JOB_TOPIC, payload(JobPhase.PROCESS, key, token("GOTO_WAYPOINT", jti=jti) if tok == "" else tok))
        return self.client.last()

    def test_off_by_default_a_flying_vehicle_takes_no_productive_request(self) -> None:
        bridge = self.flying()
        self.start_flight(bridge)
        answer = self.goto(bridge, "g1", "b")
        self.assertFalse(answer["allowed"])
        self.assertIn("chaining requests in flight is switched off", answer["reason"])

    def test_chained_when_on_after_an_accepted_request_with_a_confirmation(self) -> None:
        bridge = self.flying(chain_in_flight=True)
        self.start_flight(bridge)
        self.assertTrue(self.goto(bridge, "g1", "b")["allowed"])
        self.assertTrue(self.goto(bridge, "g2", "c")["allowed"])
        self.assertEqual([d.request for d in self.sent], ["TAKEOFF", "GOTO_WAYPOINT", "GOTO_WAYPOINT"])

    def test_chaining_needs_a_confirmation_and_never_chains_a_takeoff(self) -> None:
        bridge = self.flying(chain_in_flight=True)
        self.start_flight(bridge)
        self.assertFalse(self.goto(bridge, "g1", "b", None)["allowed"])
        bridge.on_message(self.client, JOB_TOPIC, payload(JobPhase.LOAD, "t2", token("TAKEOFF", jti="z")))
        self.assertFalse(self.client.last()["allowed"])

    def test_chaining_needs_the_envelope_and_the_confirmation_switches_on(self) -> None:
        for kw in ({"envelope": EnvelopeSettings(required=False)}, {"confirmation": ConfirmationSettings(required=False)}):
            bridge = entry(policy(phases=PhaseSettings(chain_in_flight=True), **kw), machine=MachineState.RUNNING, sender=self.sender)
            self.start_flight(bridge)
            self.assertFalse(self.goto(bridge, "g1", "b")["allowed"], kw)

    def test_a_request_the_vehicle_did_not_take_ends_the_chain(self) -> None:
        bridge = self.flying(chain_in_flight=True)
        self.start_flight(bridge)
        self.ok = False
        self.assertFalse(self.goto(bridge, "g1", "b")["allowed"])
        self.ok = True
        answer = self.goto(bridge, "g2", "c")
        self.assertFalse(answer["allowed"])
        self.assertIn("not accepted and sent", answer["reason"])

    def test_coming_home_ends_the_chain_and_an_old_flight_is_not_chained(self) -> None:
        bridge = self.flying(chain_in_flight=True, chain_max_gap_s=100)
        self.start_flight(bridge)
        NOW[0] += 500
        self.assertIn("too old", self.goto(bridge, "g1", "b")["reason"])
        bridge2 = self.flying(chain_in_flight=True)
        self.start_flight(bridge2)
        bridge2.on_message(self.client, JOB_TOPIC, payload(JobPhase.COMPLETE, "home"))
        self.assertTrue(self.client.last()["allowed"])
        self.assertIn("not accepted and sent", self.goto(bridge2, "g9", "q")["reason"])


class SettingsAndTransportTests(unittest.TestCase):
    def test_the_defaults_are_the_conservative_ones(self) -> None:
        settings = UavSettings()
        self.assertTrue(settings.envelope.required)
        self.assertTrue(settings.confirmation.required)
        self.assertFalse(settings.phases.chain_in_flight)
        self.assertFalse(settings.envelope.upload_geofence)
        self.assertEqual((settings.envelope.max_altitude_m, settings.envelope.max_radius_m), (30.0, 100.0))
        self.assertIsNone(settings.envelope.home_lat)

    def test_the_shipped_file_loads_to_the_defaults(self) -> None:
        shipped = load_settings(Path(__file__).resolve().parent.parent / "config" / "uav.json")
        self.assertEqual(shipped, UavSettings())

    def test_bad_values_are_errors(self) -> None:
        bad = [
            {"envelope": {"max_radius_m": -1}},
            {"envelope": {"home_lat": 91.0, "home_lon": 0.0}},
            {"envelope": {"home_lat": 10.0}},
            {"envelope": {"required": "yes"}},
            {"confirmation": {"actions": ["FLY"]}},
            {"confirmation": {"max_ttl_s": 0}},
            {"phases": {"chain_in_flight": 1}},
            {"phases": {"surprise": True}},
        ]
        for body in bad:
            with self.subTest(body), tempfile.TemporaryDirectory() as folder:
                path = Path(folder, "uav.json")
                path.write_text(json.dumps(body), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_settings(str(path))

    def test_a_good_file_with_actions_loads(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "uav.json")
            path.write_text(json.dumps({"confirmation": {"actions": ["TAKEOFF"]}, "envelope": {"home_lat": 38.0, "home_lon": -1.0}}), encoding="utf-8")
            loaded = load_settings(str(path))
        self.assertEqual(loaded.confirmation.actions, ("TAKEOFF",))
        self.assertEqual(loaded.envelope.home_lat, 38.0)

    def test_the_transport_can_require_an_envelope(self) -> None:
        sim = MavlinkFlightControllerEmulator()
        control = MavlinkFlightControl()
        takeoff = UavDispatch(True, "TAKEOFF", "ok")
        refused = control.send(sim, 1, 1, takeoff, require_envelope=True)
        self.assertFalse(refused.sent)
        self.assertIn("needs a flight envelope", refused.reason)
        goto = UavDispatch(True, "GOTO_WAYPOINT", "ok")
        refused = control.send(sim, 1, 1, goto, waypoint_lat=38.0, waypoint_lon=-1.0, waypoint_alt_m=10.0, require_envelope=True)
        self.assertIn("needs a flight envelope", refused.reason)
        self.assertEqual(sim.commands, [])
        envelope = FlightEnvelope(30.0, 100.0, *HOME)
        self.assertFalse(control.send(sim, 1, 1, takeoff, takeoff_altitude_m=80.0, envelope=envelope, require_envelope=True).sent)

    def test_the_command_line_builds_the_policy_from_the_settings(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder, "key")
            key.write_text("q" * 40, encoding="utf-8")
            settings = Path(folder, "uav.json")
            settings.write_text(json.dumps({"job_entry": {"enabled": True}, "envelope": {"home_lat": 38.0, "home_lon": -1.0}, "confirmation": {"key_file": str(key)}}), encoding="utf-8")
            captured = {}

            def opener(host, port, job_entry):
                captured["entry"] = job_entry
                return object()

            args = mock.Mock(settings=str(settings), mqtt_port=1883)
            cli._open_job_entry(args, "broker", None, opener)
            policy_in_use = captured["entry"]._policy
            self.assertIsNotNone(policy_in_use)
            self.assertIsNotNone(policy_in_use.verifier)
            self.assertIsNotNone(policy_in_use.envelope)
            key.write_text("short", encoding="utf-8")
            with self.assertRaises(ValueError):
                cli._open_job_entry(args, "broker", None, opener)


class GeofenceUploadTests(unittest.TestCase):
    envelope = FlightEnvelope(30.0, 100.0, *HOME)

    def test_the_circle_goes_to_the_vehicle_as_a_fence(self) -> None:
        sim = MavlinkFlightControllerEmulator()
        result = MissionUploader(MissionSettings(request_timeout_s=0.05, total_timeout_s=2.0)).upload_geofence(sim, 1, 1, self.envelope, enabled=True)
        self.assertTrue(result.sent, result.reason)
        self.assertEqual(len(sim.fence), 1)
        self.assertEqual(sim.fence[0]["command"], 5003)
        self.assertEqual(sim.fence[0]["param1"], 100.0)
        self.assertEqual((sim.fence[0]["x"], sim.fence[0]["y"]), (380000000, -10000000))
        self.assertEqual(sim.mission, [])

    def test_off_or_without_an_envelope_nothing_is_sent(self) -> None:
        sim = MavlinkFlightControllerEmulator()
        uploader = MissionUploader(MissionSettings(request_timeout_s=0.05))
        self.assertIn("switched off", uploader.upload_geofence(sim, 1, 1, self.envelope, enabled=False).reason)
        self.assertIn("needs a flight envelope", uploader.upload_geofence(sim, 1, 1, None, enabled=True).reason)
        self.assertEqual(sim.fence, [])

    def test_a_vehicle_that_refuses_the_fence_is_reported(self) -> None:
        sim = MavlinkFlightControllerEmulator()
        sim.mission_ack_result = 13
        result = MissionUploader(MissionSettings(request_timeout_s=0.05)).upload_geofence(sim, 1, 1, self.envelope, enabled=True)
        self.assertFalse(result.sent)
        self.assertIn("refused", result.reason)


if __name__ == "__main__":
    unittest.main()
