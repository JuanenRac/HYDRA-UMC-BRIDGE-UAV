# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Job entry against the autopilot emulator
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The jobs of cmd/job, as the broker would hand them over, end to end against the PX4-shaped autopilot emulator.

The job entry is a library: it is not given a sender by the command line and no broker credential is provisioned for it. This test wires a sender made of the real
`MavlinkFlightControl` and the emulator, so the whole path (payload, gate, MAV_CMD, acknowledgement, answer) is run without a vehicle.
"""
from __future__ import annotations

import json
import unittest

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict

from hydra_umc_bridge_uav import MavlinkFlightControl
from hydra_umc_bridge_uav.coordinator import UavCoordinator
from hydra_umc_bridge_uav.job_entry import JOB_TOPIC, RESULT_TOPIC, UavJobEntry

from mavlink_emulator import MavlinkFlightControllerEmulator


class Client:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str) -> object:
        self.sent.append((topic, payload))
        return object()


def payload(phase: JobPhase, key: str) -> bytes:
    return json.dumps(job_to_dict(BridgeJob("job-" + key, key, "uav-1", phase, MachineState.IDLE, {}))).encode()


class JobEntryAgainstTheEmulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fc = MavlinkFlightControllerEmulator()
        self.control = MavlinkFlightControl()
        self.client = Client()
        self.cell = CellState.READY
        self.entry = UavJobEntry(UavCoordinator(), lambda: self.cell, lambda: MachineState.IDLE, self._sender, enabled=True)

    def _sender(self, dispatch):
        return self.control.send(self.fc, 1, 1, dispatch, confirm_arm=True, takeoff_altitude_m=15.0)

    def _job(self, phase: JobPhase, key: str) -> dict:
        answer = self.entry.on_message(self.client, JOB_TOPIC, payload(phase, key))
        self.assertIsNotNone(answer)
        return answer

    def test_prepare_load_and_complete_arm_take_off_and_come_home(self) -> None:
        arm = self._job(JobPhase.PREPARE, "k-arm")
        self.assertEqual((arm["allowed"], arm["sent"], arm["request"]), (True, True, "ARM"))
        self.assertTrue(self.fc.armed)
        take_off = self._job(JobPhase.LOAD, "k-load")
        self.assertEqual((take_off["allowed"], take_off["sent"], take_off["request"]), (True, True, "TAKEOFF"))
        self.assertEqual(self.fc.flight_phase, "CLIMBING")
        home = self._job(JobPhase.COMPLETE, "k-home")
        self.assertEqual((home["allowed"], home["sent"], home["request"]), (True, True, "RETURN_TO_LAUNCH"))
        self.assertEqual(self.fc.flight_phase, "RETURNING")
        self.assertEqual(len([1 for topic, _ in self.client.sent if topic == RESULT_TOPIC]), 3)

    def test_an_autopilot_that_denies_the_arm_makes_the_answer_not_allowed(self) -> None:
        self.fc.fail_prearm()
        answer = self._job(JobPhase.PREPARE, "k-denied")
        self.assertEqual((answer["allowed"], answer["sent"]), (False, False))
        self.assertFalse(self.fc.armed)

    def test_a_take_off_before_the_arm_is_denied_by_the_autopilot_and_nothing_moves(self) -> None:
        answer = self._job(JobPhase.LOAD, "k-early")
        self.assertEqual((answer["allowed"], answer["sent"]), (False, False))
        self.assertEqual(self.fc.flight_phase, "ON_GROUND")

    def test_a_cell_that_is_not_ready_sends_nothing_to_the_autopilot(self) -> None:
        self.cell = CellState.INHIBITED
        answer = self._job(JobPhase.PREPARE, "k-inhibited")
        self.assertEqual((answer["allowed"], answer["sent"]), (False, False))
        self.assertEqual(self.fc.commands, [])

    def test_the_same_key_twice_reaches_the_autopilot_once(self) -> None:
        self._job(JobPhase.PREPARE, "k-twice")
        sent = len(self.fc.commands)
        self._job(JobPhase.PREPARE, "k-twice")
        self.assertEqual(len(self.fc.commands), sent)

    def test_a_retained_replay_never_reaches_the_autopilot(self) -> None:
        self.assertIsNone(self.entry.on_message(self.client, JOB_TOPIC, payload(JobPhase.PREPARE, "k-retained"), retained=True))
        self.assertEqual(self.fc.commands, [])
        self.assertFalse(self.fc.armed)

    def test_an_entry_that_is_off_does_not_touch_the_autopilot(self) -> None:
        off = UavJobEntry(UavCoordinator(), lambda: CellState.READY, lambda: MachineState.IDLE, self._sender, enabled=False)
        self.assertIsNone(off.on_message(self.client, JOB_TOPIC, payload(JobPhase.PREPARE, "k-off")))
        self.assertEqual(self.fc.commands, [])


if __name__ == "__main__":
    unittest.main()
