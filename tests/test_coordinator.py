# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Coordinator tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================

import unittest
from types import SimpleNamespace

from hydra_umc_sdk.bridge_contract import BridgeError
from hydra_umc_bridge_uav import BridgeJob, CellState, JobPhase, MachineState, UavCoordinator


def job(phase=JobPhase.PROCESS, state=MachineState.IDLE):
    return BridgeJob("job-1", "idempotency-1", "uav-1", phase, state, {})


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = UavCoordinator()

    def test_ready_job_requests_the_waypoint_flight(self):
        result = self.coordinator.dispatch(job(), CellState.READY, MachineState.IDLE)
        self.assertTrue(result.accepted)
        self.assertEqual(result.request, "GOTO_WAYPOINT")

    def test_busy_machine_is_not_reused(self):
        result = self.coordinator.dispatch(job(), CellState.READY, MachineState.RUNNING)
        self.assertFalse(result.accepted)

    def test_abort_requests_return_to_launch_and_stays_available_during_fault(self):
        result = self.coordinator.dispatch(job(JobPhase.ABORT, MachineState.FAULT), CellState.FAULT, MachineState.FAULT)
        self.assertTrue(result.accepted)
        self.assertEqual(result.request, "RETURN_TO_LAUNCH")

    def test_complete_also_requests_return_to_launch(self):
        # COMPLETE and ABORT deliberately share the same real request name -
        # a normal mission end and an emergency abort both mean "come home".
        result = self.coordinator.dispatch(job(JobPhase.COMPLETE), CellState.READY, MachineState.IDLE)
        self.assertTrue(result.accepted)
        self.assertEqual(result.request, "RETURN_TO_LAUNCH")

    # 's
    # own real fix now rejects an unrecognised `phase` AT CONSTRUCTION
    # TIME (`BridgeJob.__post_init__` requires a real `JobPhase` member) -
    # this test used to construct one directly with a raw string, which
    # is no longer possible through the real public constructor at all.
    # The intended fix: update this test to require rejection at
    # the constructor (below), and cover the coordinator's OWN defensive
    # fallback with an explicit double instead of weakening the SDK's
    # public contract to let the old construction succeed again.
    def test_constructing_a_bridge_job_with_an_unknown_phase_is_refused_by_the_sdk_itself(self):
        with self.assertRaises(BridgeError):
            BridgeJob("job-2", "idempotency-2", "uav-1", "SOME_FUTURE_PHASE", MachineState.IDLE, {})

    def test_dispatch_fails_closed_on_a_job_double_carrying_an_unmapped_phase(self):
        # A real BridgeJob can never carry an unmapped phase any more (see
        # above) - this proves dispatch()'s own `_phase_requests.get(...)`
        # fallback still fails closed as defense-in-depth, using a minimal
        # explicit double (only the one attribute dispatch() actually
        # reads) rather than a real BridgeJob the SDK would now refuse to
        # construct at all.
        unknown = SimpleNamespace(phase="SOME_FUTURE_PHASE")
        result = self.coordinator.dispatch(unknown, CellState.READY, MachineState.IDLE)
        self.assertFalse(result.accepted)
        self.assertEqual(result.request, "none")

    def test_a_normal_end_of_mission_still_brings_the_vehicle_home_whatever_the_cell_or_machine_says(self):
        # COMPLETE -> return to launch: the vehicle is flying (not IDLE) and the cell may have been inhibited meanwhile.
        for cell in (CellState.INHIBITED, CellState.FAULT):
            for machine in (MachineState.RUNNING, MachineState.FAULT):
                with self.subTest(cell=cell, machine=machine):
                    result = self.coordinator.dispatch(job(JobPhase.COMPLETE), cell, machine)
                    self.assertTrue(result.accepted, result.reason)
                    self.assertEqual(result.request, "RETURN_TO_LAUNCH")
        observed = self.coordinator.dispatch(job(JobPhase.COMPLETE), CellState.READY, MachineState.RUNNING)
        self.assertTrue(observed.accepted)

    def test_the_flight_phases_are_refused_when_the_vehicle_state_was_not_read(self):
        # the claim in the job message is not a reading of the autopilot
        for phase in (JobPhase.PREPARE, JobPhase.LOAD, JobPhase.PROCESS, JobPhase.UNLOAD):
            with self.subTest(phase=phase):
                result = self.coordinator.dispatch(job(phase), CellState.READY)
                self.assertFalse(result.accepted)
                self.assertIn("did not read", result.reason)

    def test_coming_home_does_not_need_a_reading_of_the_vehicle(self):
        self.assertTrue(self.coordinator.dispatch(job(JobPhase.COMPLETE), CellState.READY).accepted)
        self.assertTrue(self.coordinator.dispatch(job(JobPhase.ABORT), CellState.READY).accepted)

    def test_the_other_flight_phases_are_still_gated_by_the_cell_and_the_machine(self):
        for phase in (JobPhase.PREPARE, JobPhase.LOAD, JobPhase.PROCESS, JobPhase.UNLOAD):
            with self.subTest(phase=phase):
                self.assertFalse(self.coordinator.dispatch(job(phase), CellState.INHIBITED, MachineState.IDLE).accepted)
                self.assertFalse(self.coordinator.dispatch(job(phase), CellState.READY, MachineState.RUNNING).accepted)

    def test_disarm_is_a_standalone_always_allowed_request(self):
        disarm = self.coordinator.disarm_request()
        self.assertTrue(disarm.accepted)
        self.assertEqual(disarm.request, "DISARM")
        self.assertIn("DISARM", self.coordinator.request_plan().requests)

    def test_request_plan_is_static_and_explicitly_not_a_runtime(self):
        plan = self.coordinator.request_plan().to_dict()
        self.assertEqual(plan["schema_version"], "1.1")
        self.assertEqual(plan["mode"], "plan-only")
        self.assertIn("RETURN_TO_LAUNCH", plan["requests"])
        self.assertIn("ARM", plan["requests"])
        # COMPLETE/ABORT collapsing onto the same real request name must not
        # produce a duplicate entry in the static plan.
        self.assertEqual(plan["requests"].count("RETURN_TO_LAUNCH"), 1)

    def test_request_plan_includes_the_real_standalone_land_request(self):
        # LAND (MAV_CMD_NAV_LAND) is real and distinct from RETURN_TO_LAUNCH
        # (MAV_CMD_NAV_RETURN_TO_LAUNCH) - see coordinator.py's own LAND
        # comment - it must appear exactly once in the static plan even
        # though it isn't reached through any JobPhase.
        plan = self.coordinator.request_plan().to_dict()
        self.assertEqual(plan["requests"].count("LAND"), 1)

    def test_emergency_land_is_always_accepted_regardless_of_cell_or_machine_state(self):
        # Same "an operator must always be able to request a controlled
        # descent" reasoning already applied to ABORT/RTL - a real land
        # request must never be gated on IDLE/READY.
        result = self.coordinator.emergency_land_request()
        self.assertTrue(result.accepted)
        self.assertEqual(result.request, "LAND")


if __name__ == "__main__":
    unittest.main()
