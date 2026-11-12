# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Global stop tests (the job entry follows the announcement of the server)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Off by default; with the switch on, a job that asks the vehicle to do something is refused while the server says SAFE_STOP (or cannot be read), and only a clear READY ends it.

The requests that only bring the vehicle home (return to launch, land, disarm) always go through, and a refused job does not use up its idempotency key.
"""
from __future__ import annotations

import json
import os
import random
import unittest
from unittest import mock

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict
from hydra_umc_sdk.global_stop import DEFAULT_TOPIC, GlobalStopGuard, GlobalStopSettings

from hydra_umc_bridge_uav.coordinator import UavCoordinator
from hydra_umc_bridge_uav.job_entry import ENABLE_ENVIRONMENT, GLOBAL_STOP_ENVIRONMENT_NAME, JOB_TOPIC, RESULT_TOPIC, UavJobEntry

READY = json.dumps({"state": "READY"}).encode()
STOPPED = json.dumps({"state": "SAFE_STOP"}).encode()


class Client:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str) -> object:
        self.sent.append((topic, payload))
        return object()


def job(phase: JobPhase, key: str = "key-1") -> bytes:
    return json.dumps(job_to_dict(BridgeJob("job-1", key, "uav-1", phase, MachineState.IDLE, {}))).encode()


def entry(*, enabled: bool = True, strict: bool = False, switch: bool = True) -> UavJobEntry:
    guard = GlobalStopGuard(GlobalStopSettings(enabled=switch, topic=DEFAULT_TOPIC, strict=strict))
    return UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: MachineState.IDLE, None, enabled=enabled, global_stop=guard)


def answer(e: UavJobEntry, phase: JobPhase, key: str = "key-1") -> dict:
    result = e.on_message(Client(), JOB_TOPIC, job(phase, key))
    assert result is not None
    return result


class SwitchTests(unittest.TestCase):
    def test_off_by_default_the_environment_name_is_uav(self) -> None:
        self.assertEqual(GLOBAL_STOP_ENVIRONMENT_NAME, "UAV")
        with mock.patch.dict(os.environ, {ENABLE_ENVIRONMENT: "1"}):
            os.environ.pop("HYDRA_UMC_UAV_GLOBAL_STOP", None)
            plain = UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: MachineState.IDLE)
        self.assertFalse(plain.global_stop.enabled)
        self.assertEqual(plain.subscriptions(), [(JOB_TOPIC, 1)])
        self.assertFalse(plain.handles(DEFAULT_TOPIC))

    def test_the_environment_switches_it_on_and_the_topic_is_subscribed(self) -> None:
        with mock.patch.dict(os.environ, {ENABLE_ENVIRONMENT: "1", "HYDRA_UMC_UAV_GLOBAL_STOP": "1"}):
            followed = UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: MachineState.IDLE)
        self.assertTrue(followed.global_stop.enabled)
        self.assertEqual(followed.subscriptions(), [(JOB_TOPIC, 1), (DEFAULT_TOPIC, 1)])
        self.assertTrue(followed.handles(DEFAULT_TOPIC))

    def test_a_job_entry_that_is_off_follows_nothing_even_with_the_switch_on(self) -> None:
        e = entry(enabled=False)
        self.assertEqual(e.subscriptions(), [])
        self.assertFalse(e.handles(DEFAULT_TOPIC))
        self.assertIsNone(e.on_message(Client(), DEFAULT_TOPIC, STOPPED))
        self.assertEqual(e.global_stop.state, "UNKNOWN")

    def test_with_the_switch_off_an_announcement_changes_nothing(self) -> None:
        e = entry(switch=False)
        self.assertIsNone(e.on_message(Client(), DEFAULT_TOPIC, STOPPED))
        self.assertTrue(answer(e, JobPhase.PROCESS)["allowed"])

    def test_a_settings_object_is_enough(self) -> None:
        e = UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: MachineState.IDLE, None, enabled=True, global_stop=GlobalStopSettings(enabled=True, topic="plant/stop"))
        self.assertEqual(e.subscriptions(), [(JOB_TOPIC, 1), ("plant/stop", 1)])


class StopTests(unittest.TestCase):
    def test_every_phase_that_asks_for_work_is_refused_while_stopped(self) -> None:
        e = entry()
        e.on_message(Client(), DEFAULT_TOPIC, STOPPED, retained=True)
        for index, phase in enumerate((JobPhase.PREPARE, JobPhase.LOAD, JobPhase.PROCESS, JobPhase.UNLOAD)):
            result = answer(e, phase, key=f"k{index}")
            self.assertFalse(result["allowed"], phase)
            self.assertFalse(result["sent"])
            self.assertEqual(result["mode"], "refused")
            self.assertIn("global stop active", str(result["reason"]))
            self.assertEqual(result["job_id"], "job-1")

    def test_coming_home_always_goes_through(self) -> None:
        e = entry()
        e.on_message(Client(), DEFAULT_TOPIC, STOPPED)
        for index, phase in enumerate((JobPhase.COMPLETE, JobPhase.ABORT)):
            result = answer(e, phase, key=f"home{index}")
            self.assertTrue(result["allowed"], phase)
            self.assertEqual(result["request"], "RETURN_TO_LAUNCH")

    def test_a_refused_job_does_not_use_up_its_key(self) -> None:
        e = entry()
        e.on_message(Client(), DEFAULT_TOPIC, STOPPED)
        self.assertFalse(answer(e, JobPhase.PROCESS, "same")["allowed"])
        e.on_message(Client(), DEFAULT_TOPIC, READY)
        again = answer(e, JobPhase.PROCESS, "same")
        self.assertTrue(again["allowed"])
        self.assertEqual(again["request"], "GOTO_WAYPOINT")

    def test_only_a_clear_ready_ends_the_refusal(self) -> None:
        e = entry()
        for text in (b"not json", b"[]", b'{"state": "ready"}', b'{"state": "MAYBE"}', b'{"other": 1}', b"\xff\xfe"):
            e.on_message(Client(), DEFAULT_TOPIC, text)
            self.assertFalse(answer(e, JobPhase.PROCESS, "x")["allowed"], text)
        e.on_message(Client(), DEFAULT_TOPIC, READY)
        self.assertTrue(answer(e, JobPhase.PROCESS, "x")["allowed"])
        e.on_message(Client(), DEFAULT_TOPIC, STOPPED)
        self.assertFalse(answer(e, JobPhase.PROCESS, "y")["allowed"])

    def test_before_the_first_announcement_it_follows_the_strict_flag(self) -> None:
        self.assertTrue(answer(entry(strict=False), JobPhase.PROCESS)["allowed"])
        strict = answer(entry(strict=True), JobPhase.PROCESS)
        self.assertFalse(strict["allowed"])
        self.assertIn("not known yet", str(strict["reason"]))

    def test_the_announcement_is_read_even_when_retained_but_a_retained_job_is_not(self) -> None:
        e = entry()
        client = Client()
        self.assertIsNone(e.on_message(client, DEFAULT_TOPIC, STOPPED, retained=True))
        self.assertEqual(e.global_stop.state, "SAFE_STOP")
        self.assertIsNone(e.on_message(client, JOB_TOPIC, job(JobPhase.PROCESS), retained=True))
        self.assertEqual(client.sent, [])

    def test_the_answer_is_published_on_the_result_topic(self) -> None:
        e = entry()
        e.on_message(Client(), DEFAULT_TOPIC, STOPPED)
        client = Client()
        e.on_message(client, JOB_TOPIC, job(JobPhase.LOAD))
        topics = [t for t, _ in client.sent]
        self.assertEqual(topics, [RESULT_TOPIC])
        self.assertFalse(json.loads(client.sent[0][1])["allowed"])


class MovesTheVehicleTests(unittest.TestCase):
    def test_the_table(self) -> None:
        c = UavCoordinator()
        expected = {"PREPARE": True, "LOAD": True, "PROCESS": True, "UNLOAD": True, "COMPLETE": False, "ABORT": False}
        for phase, moving in expected.items():
            self.assertEqual(c.moves_the_vehicle({"phase": phase}), moving, phase)
            self.assertEqual(c.moves_the_vehicle({"phase": phase.lower()}), moving, phase)

    def test_what_cannot_be_read_counts_as_moving(self) -> None:
        c = UavCoordinator()
        for bad in ({}, {"phase": None}, {"phase": 3}, {"phase": ""}, {"phase": "FLY"}, {"phase": ["PROCESS"]}):
            self.assertTrue(c.moves_the_vehicle(bad), bad)


class FuzzTests(unittest.TestCase):
    def test_no_payload_gets_a_moving_request_through_while_stopped(self) -> None:
        rng = random.Random(20260512)
        e = entry()
        e.on_message(Client(), DEFAULT_TOPIC, STOPPED)
        words = ["PREPARE", "LOAD", "PROCESS", "UNLOAD", "COMPLETE", "ABORT", "process", " Process ", "x", "", None, 7, [], {}]
        for n in range(400):
            body: dict[str, object] = {"job_id": f"j{n}", "idempotency_key": f"fz{n}", "source": "uav-1", "phase": rng.choice(words), "machine_state": rng.choice(["IDLE", "BUSY", "SAFE_STOP", None]), "parameters": {}}
            if rng.random() < 0.2:
                body.pop("phase")
            result = e.on_message(Client(), JOB_TOPIC, json.dumps(body).encode())
            self.assertIsNotNone(result)
            assert result is not None
            if result["allowed"]:
                self.assertEqual(result["request"], "RETURN_TO_LAUNCH", body)

    def test_garbage_payloads_never_raise(self) -> None:
        rng = random.Random(7)
        e = entry()
        for _ in range(200):
            blob = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
            e.on_message(Client(), DEFAULT_TOPIC, blob)
            e.on_message(Client(), JOB_TOPIC, blob)


if __name__ == "__main__":
    unittest.main()
