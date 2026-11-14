# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - A repeated job key whose first attempt left no answer
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A repeat whose first attempt left no answer is answered with what the vehicle reports now (switch off by default) and never run again; no reading keeps it refused. Simulated reading, evidence=simulator."""

import os
import unittest
from unittest import mock

from hydra_umc_sdk.bridge_contract import job_to_dict
from hydra_umc_sdk.job_intake import IdempotencyStore

from hydra_umc_bridge_uav import BridgeJob, CellState, JobPhase, MachineState, UavCoordinator
from hydra_umc_bridge_uav.coordinator import IN_DOUBT_CONSULT_ENVIRONMENT


def message(phase=JobPhase.PROCESS, key="key-1"):
    return job_to_dict(BridgeJob("job-1", key, "vehicle-1", phase, MachineState.IDLE, {}))


def in_doubt_store(key="key-1"):
    store = IdempotencyStore()
    assert store.claim(key)  # the first attempt claimed the key and never stored an answer
    return store


class OffByDefaultTests(unittest.TestCase):
    def test_off_by_default_a_job_in_doubt_is_refused_as_before(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(IN_DOUBT_CONSULT_ENVIRONMENT, None)
            result = UavCoordinator(in_doubt_store()).handle_message(message(), CellState.READY, MachineState.RUNNING)
        self.assertFalse(result.accepted)
        self.assertIn("already handled", result.reason)
        self.assertNotIn("now reports", result.reason)

    def test_the_environment_switch_turns_it_on(self):
        with mock.patch.dict(os.environ, {IN_DOUBT_CONSULT_ENVIRONMENT: "1"}):
            result = UavCoordinator(in_doubt_store()).handle_message(message(), CellState.READY, MachineState.RUNNING)
        self.assertIn("now reports RUNNING", result.reason)

    def test_any_other_value_of_the_switch_keeps_it_off(self):
        for value in ("0", "", "true", "yes"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {IN_DOUBT_CONSULT_ENVIRONMENT: value}):
                result = UavCoordinator(in_doubt_store()).handle_message(message(), CellState.READY, MachineState.RUNNING)
                self.assertNotIn("now reports", result.reason)


class ConsultTests(unittest.TestCase):
    def make(self, store=None):
        return UavCoordinator(store if store is not None else in_doubt_store(), in_doubt_consult=True)

    def test_the_repeat_is_answered_with_the_state_the_vehicle_reports_and_is_not_run_again(self):
        result = self.make().handle_message(message(), CellState.READY, MachineState.RUNNING)
        self.assertFalse(result.accepted)
        self.assertEqual(result.request, "none")
        self.assertIn("now reports RUNNING", result.reason)
        self.assertIn("not run again", result.reason)

    def test_an_idle_machine_and_a_ready_cell_do_not_turn_a_repeat_into_a_new_job(self):
        result = self.make().handle_message(message(), CellState.READY, MachineState.IDLE)
        self.assertFalse(result.accepted)
        self.assertIn("now reports IDLE", result.reason)

    def test_no_reading_leaves_the_repeat_refused(self):
        result = self.make().handle_message(message(), CellState.READY, None)
        self.assertFalse(result.accepted)
        self.assertIn("already handled", result.reason)
        self.assertNotIn("now reports", result.reason)

    def test_the_answer_is_stored_so_a_third_repeat_gets_the_same_one(self):
        store = in_doubt_store()
        bridge = self.make(store)
        first = bridge.handle_message(message(), CellState.READY, MachineState.HOLDING)
        third = bridge.handle_message(message(), CellState.READY, MachineState.IDLE)
        self.assertEqual(third.reason, first.reason)
        self.assertIn("HOLDING", third.reason)
        self.assertTrue(store.recall("key-1").startswith("in doubt: "))

    def test_a_key_with_a_stored_answer_gets_that_answer_not_a_reading(self):
        bridge = self.make(IdempotencyStore())
        bridge.handle_message(message(), CellState.READY, MachineState.IDLE)
        again = bridge.handle_message(message(), CellState.READY, MachineState.RUNNING)
        self.assertFalse(again.accepted)
        self.assertNotIn("now reports", again.reason)

    def test_a_new_key_is_evaluated_normally_with_the_switch_on(self):
        result = self.make(in_doubt_store("other")).handle_message(message(key="key-2"), CellState.READY, MachineState.IDLE)
        self.assertTrue(result.accepted)

    def test_the_reading_of_one_message_is_not_kept_for_the_next(self):
        bridge = self.make(in_doubt_store("a"))
        bridge.handle_message(message(key="a"), CellState.READY, MachineState.RUNNING)
        store = bridge._store
        self.assertTrue(store.claim("b"))
        later = bridge.handle_message(message(key="b"), CellState.READY, None)
        self.assertNotIn("now reports", later.reason)

    def test_what_only_brings_it_to_a_safe_state_is_still_forwarded_again(self):
        store = in_doubt_store("home")
        result = self.make(store).handle_message(message(JobPhase.COMPLETE, key="home"), CellState.READY, MachineState.IDLE)
        self.assertTrue(result.accepted)
        self.assertNotIn("now reports", result.reason)


if __name__ == "__main__":
    unittest.main()
