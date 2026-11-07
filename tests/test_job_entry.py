# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Job entry tests (cmd/job over MQTT)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The jobs of cmd/job: off by default, retained and malformed messages stopped, a repeated key not run twice, the gate decides with the state the autopilot reported."""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict

from hydra_umc_bridge_uav import cli
from hydra_umc_bridge_uav.coordinator import UavCoordinator
from hydra_umc_bridge_uav.job_entry import ENABLE_ENVIRONMENT, JOB_TOPIC, MAX_PAYLOAD_BYTES, RESULT_TOPIC, UavJobEntry
from hydra_umc_bridge_uav.mavlink_transport import MavlinkSendResult
from hydra_umc_bridge_uav.settings import JobEntrySettings, load_settings


class Client:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def publish(self, topic: str, payload: str) -> object:
        if self.fail:
            raise OSError("broker gone")
        self.sent.append((topic, payload))
        return object()

    def results(self) -> list[dict]:
        return [json.loads(p) for t, p in self.sent if t == RESULT_TOPIC]


def job(phase: JobPhase = JobPhase.PROCESS, key: str = "key-1", state: MachineState = MachineState.IDLE) -> bytes:
    return json.dumps(job_to_dict(BridgeJob("job-1", key, "uav-1", phase, state, {}))).encode()


def entry(cell=CellState.READY, machine=MachineState.IDLE, enabled=True, sender=None) -> UavJobEntry:
    return UavJobEntry(UavCoordinator(), lambda: cell, lambda: machine, sender, enabled=enabled)


class SwitchTests(unittest.TestCase):
    def test_off_by_default_subscribes_to_nothing_and_answers_nothing(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop(ENABLE_ENVIRONMENT, None)
            off = UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: MachineState.IDLE)
        client = Client()
        self.assertFalse(off.enabled)
        self.assertEqual(off.subscriptions(), [])
        self.assertFalse(off.handles(JOB_TOPIC))
        self.assertIsNone(off.on_message(client, JOB_TOPIC, job()))
        self.assertEqual(client.sent, [])

    def test_the_environment_switches_it_on(self) -> None:
        with mock.patch.dict(os.environ, {ENABLE_ENVIRONMENT: "1"}):
            on = UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: MachineState.IDLE)
        self.assertTrue(on.enabled)
        self.assertEqual(on.subscriptions(), [(JOB_TOPIC, 1)])

    def test_any_other_value_of_the_environment_leaves_it_off(self) -> None:
        for value in ("0", "true", "yes", ""):
            with mock.patch.dict(os.environ, {ENABLE_ENVIRONMENT: value}):
                self.assertFalse(UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: None).enabled, value)

    def test_another_topic_is_not_handled(self) -> None:
        client = Client()
        self.assertIsNone(entry().on_message(client, "hydra/bridges/uav/other", job()))
        self.assertEqual(client.sent, [])


class IntakeTests(unittest.TestCase):
    def test_a_good_job_is_decided_plan_only_and_nothing_is_sent_without_a_sender(self) -> None:
        client = Client()
        answer = entry().on_message(client, JOB_TOPIC, job())
        self.assertTrue(answer["allowed"])
        self.assertEqual((answer["request"], answer["mode"], answer["sent"], answer["job_id"]), ("GOTO_WAYPOINT", "plan-only", False, "job-1"))
        self.assertEqual(client.results(), [answer])

    def test_a_retained_message_is_ignored(self) -> None:
        client = Client()
        self.assertIsNone(entry().on_message(client, JOB_TOPIC, job(), retained=True))
        self.assertEqual(client.sent, [])

    def test_unreadable_oversized_and_non_object_payloads_are_refused(self) -> None:
        for payload in (b"{not json", b"\xff\xfe", b"[1, 2]", b"7", b"x" * (MAX_PAYLOAD_BYTES + 1)):
            client = Client()
            answer = entry().on_message(client, JOB_TOPIC, payload)
            self.assertFalse(answer["allowed"], payload[:10])
            self.assertFalse(answer["sent"])
            self.assertEqual(len(client.results()), 1)

    def test_a_malformed_job_is_refused(self) -> None:
        answer = entry().on_message(Client(), JOB_TOPIC, json.dumps({"job_id": "x"}).encode())
        self.assertFalse(answer["allowed"])
        self.assertIn("malformed", answer["reason"])

    def test_a_repeated_key_is_not_run_twice(self) -> None:
        sub = entry()
        client = Client()
        self.assertTrue(sub.on_message(client, JOB_TOPIC, job())["allowed"])
        second = sub.on_message(client, JOB_TOPIC, job())
        self.assertFalse(second["allowed"])
        self.assertIn("already handled", second["reason"])

    def test_a_repeated_return_to_launch_is_forwarded_again(self) -> None:
        sub = entry(CellState.FAULT, MachineState.FAULT)
        client = Client()
        first = sub.on_message(client, JOB_TOPIC, job(JobPhase.ABORT, "abort", MachineState.FAULT))
        second = sub.on_message(client, JOB_TOPIC, job(JobPhase.ABORT, "abort", MachineState.FAULT))
        self.assertEqual((first["allowed"], second["allowed"]), (True, True))
        self.assertEqual(second["request"], "RETURN_TO_LAUNCH")

    def test_flight_is_refused_when_the_autopilot_state_is_unknown_or_the_cell_is_not_ready(self) -> None:
        self.assertFalse(entry(machine=None).on_message(Client(), JOB_TOPIC, job())["allowed"])
        self.assertFalse(entry(cell=CellState.INHIBITED).on_message(Client(), JOB_TOPIC, job())["allowed"])
        self.assertFalse(entry(machine=MachineState.RUNNING).on_message(Client(), JOB_TOPIC, job())["allowed"])

    def test_coming_home_is_answered_even_when_the_state_is_unknown(self) -> None:
        answer = entry(CellState.FAULT, None).on_message(Client(), JOB_TOPIC, job(JobPhase.ABORT, "k", MachineState.FAULT))
        self.assertTrue(answer["allowed"])
        self.assertEqual(answer["request"], "RETURN_TO_LAUNCH")

    def test_a_broker_that_is_gone_does_not_end_the_loop(self) -> None:
        answer = entry().on_message(Client(fail=True), JOB_TOPIC, job())
        self.assertTrue(answer["allowed"])

    def test_a_coordinator_that_raises_is_answered_as_a_refusal(self) -> None:
        def boom() -> CellState:
            raise RuntimeError("no state")

        client = Client()
        answer = UavJobEntry(UavCoordinator(), boom, lambda: MachineState.IDLE, enabled=True).on_message(client, JOB_TOPIC, job())
        self.assertFalse(answer["allowed"])
        self.assertEqual(answer["reason"], "the job could not be handled: RuntimeError")
        self.assertEqual(len(client.results()), 1)


class SenderTests(unittest.TestCase):
    def test_an_accepted_request_goes_to_the_sender_and_the_answer_says_it_was_sent(self) -> None:
        seen = []

        def sender(dispatch):
            seen.append(dispatch.request)
            return MavlinkSendResult(True, "ok")

        answer = entry(sender=sender).on_message(Client(), JOB_TOPIC, job())
        self.assertEqual(seen, ["GOTO_WAYPOINT"])
        self.assertEqual((answer["allowed"], answer["sent"], answer["mode"]), (True, True, "send"))

    def test_a_refused_job_never_reaches_the_sender(self) -> None:
        seen = []
        entry(cell=CellState.INHIBITED, sender=lambda d: seen.append(d) or MavlinkSendResult(True, "")).on_message(Client(), JOB_TOPIC, job())
        self.assertEqual(seen, [])

    def test_a_vehicle_that_refuses_makes_the_answer_not_allowed_with_its_reason(self) -> None:
        answer = entry(sender=lambda d: MavlinkSendResult(False, "arm needs confirmation")).on_message(Client(), JOB_TOPIC, job())
        self.assertEqual((answer["allowed"], answer["sent"], answer["reason"]), (False, False, "arm needs confirmation"))


class SettingsAndCliTests(unittest.TestCase):
    def test_the_section_is_off_by_default_and_a_wrong_type_is_an_error(self) -> None:
        self.assertFalse(JobEntrySettings().enabled)
        self.assertEqual(JobEntrySettings(enabled="yes").problems(), ["job_entry.enabled must be true or false"])  # type: ignore[arg-type]

    def test_the_shipped_configuration_keeps_the_entry_and_the_data_lake_off(self) -> None:
        settings = load_settings("config/uav.json")
        self.assertFalse(settings.job_entry.enabled)
        self.assertFalse(settings.datalake.send_enabled)

    def test_the_cli_starts_the_entry_only_when_the_settings_switch_it_on(self) -> None:
        import tempfile
        from pathlib import Path

        class Sink:
            def heartbeat_send(self, *args):
                pass

        opened = []

        def mqtt_opener(host, port, *, job_entry):
            opened.append((host, port, job_entry.enabled, job_entry.subscriptions()))
            return None

        def go(settings_text, env=None):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "uav.json"
                path.write_text(settings_text, encoding="utf-8")
                args = cli.build_parser().parse_args(["run", "--connection", "x", "--duration", "0.2", "--status-every", "0", "--settings", str(path), "--mqtt-host", "broker.local"])
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, env or {}):
                    os.environ.pop(ENABLE_ENVIRONMENT, None) if env is None else None
                    code = cli.run(args, opener=lambda *a, **k: Sink(), mqtt_opener=mqtt_opener, out=out, err=err, install_signals=False)
            return code, err.getvalue()

        code, _ = go("{}")
        self.assertEqual((code, opened), (0, []))
        code, _ = go(json.dumps({"job_entry": {"enabled": True}}))
        self.assertEqual(code, 0)
        self.assertEqual(opened, [("broker.local", 1883, True, [(JOB_TOPIC, 1)])])

    def test_a_broker_that_cannot_be_reached_ends_the_run_with_a_clear_error_and_closes_the_link(self) -> None:
        import tempfile
        from pathlib import Path

        closed = []

        class Sink:
            def heartbeat_send(self, *args):
                pass

            def close(self):
                closed.append(True)

        def refusing(host, port, *, job_entry):
            raise RuntimeError("the MQTT broker refused the connection: bad credentials")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "uav.json"
            path.write_text(json.dumps({"job_entry": {"enabled": True}}), encoding="utf-8")
            args = cli.build_parser().parse_args(["run", "--connection", "x", "--duration", "0.2", "--settings", str(path), "--mqtt-host", "b"])
            err = io.StringIO()
            code = cli.run(args, opener=lambda *a, **k: Sink(), mqtt_opener=refusing, out=io.StringIO(), err=err, install_signals=False)
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("refused the connection", err.getvalue())
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
