# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Job intake tests (retained, malformed and repeated messages)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================

import unittest

from hydra_umc_sdk.bridge_contract import job_to_dict
from hydra_umc_sdk.job_intake import IdempotencyStore
from hydra_umc_bridge_uav import BridgeJob, CellState, JobPhase, MachineState, UavCoordinator


def message(phase=JobPhase.PROCESS, key="key-1", state=MachineState.IDLE):
    return job_to_dict(BridgeJob("job-1", key, "uav-1", phase, state, {}))


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = UavCoordinator()

    def test_a_fresh_message_is_dispatched(self):
        result = self.coordinator.handle_message(message(), CellState.READY, MachineState.IDLE)
        self.assertTrue(result.accepted)
        self.assertEqual(result.request, "GOTO_WAYPOINT")

    def test_a_repeat_of_the_same_key_is_not_run_a_second_time(self):
        self.coordinator.handle_message(message(), CellState.READY, MachineState.IDLE)
        second = self.coordinator.handle_message(message(), CellState.READY, MachineState.IDLE)
        self.assertFalse(second.accepted)
        self.assertIn("already handled", second.reason)

    def test_a_retained_message_is_ignored(self):
        result = self.coordinator.handle_message(message(), CellState.READY, MachineState.IDLE, retained=True)
        self.assertFalse(result.accepted)
        self.assertIn("retained", result.reason)

    def test_a_malformed_message_is_refused_and_does_not_raise(self):
        result = self.coordinator.handle_message({"job_id": "x"}, CellState.READY, MachineState.IDLE)
        self.assertFalse(result.accepted)
        self.assertIn("malformed", result.reason)

    def test_a_repeated_return_to_launch_is_forwarded_again(self):
        first = self.coordinator.handle_message(message(JobPhase.ABORT, "abort-1", MachineState.FAULT), CellState.FAULT, MachineState.FAULT)
        second = self.coordinator.handle_message(message(JobPhase.ABORT, "abort-1", MachineState.FAULT), CellState.FAULT, MachineState.FAULT)
        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(second.request, "RETURN_TO_LAUNCH")

    def test_the_answer_is_stored_with_the_key(self):
        store = IdempotencyStore()
        coordinator = UavCoordinator(store)
        coordinator.handle_message(message(), CellState.READY, MachineState.IDLE)
        self.assertIn("accepted: GOTO_WAYPOINT", store.recall("key-1"))

    def test_a_refused_job_is_also_remembered_so_it_is_not_retried_into_acceptance(self):
        refused = self.coordinator.handle_message(message(), CellState.READY, MachineState.RUNNING)
        self.assertFalse(refused.accepted)
        again = self.coordinator.handle_message(message(), CellState.READY, MachineState.IDLE)
        self.assertFalse(again.accepted)


if __name__ == "__main__":
    unittest.main()
