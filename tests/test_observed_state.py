# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - The gate uses the machine state the autopilot reports
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
import unittest

from hydra_umc_bridge_uav import BridgeJob, CellState, JobPhase, MachineState, UavCoordinator


def job(claimed=MachineState.IDLE, phase=JobPhase.PROCESS):
    return BridgeJob("job-1", "key-1", "uav-1", phase, claimed, {})


class ObservedStateTests(unittest.TestCase):
    def test_the_reported_state_beats_the_claim_in_the_job(self):
        dispatch = UavCoordinator().dispatch(job(MachineState.IDLE), CellState.READY, MachineState.RUNNING)
        self.assertFalse(dispatch.accepted)
        self.assertIn("RUNNING", dispatch.reason)

    def test_a_vehicle_reported_idle_is_accepted_whatever_the_job_claims(self):
        self.assertTrue(UavCoordinator().dispatch(job(MachineState.FAULT), CellState.READY, MachineState.IDLE).accepted)

    def test_without_a_report_the_claim_is_still_used(self):
        self.assertTrue(UavCoordinator().dispatch(job(MachineState.IDLE), CellState.READY).accepted)
        self.assertFalse(UavCoordinator().dispatch(job(MachineState.RUNNING), CellState.READY).accepted)

    def test_return_to_launch_is_never_blocked_by_either_state(self):
        abort = job(MachineState.FAULT, JobPhase.ABORT)
        self.assertTrue(UavCoordinator().dispatch(abort, CellState.FAULT, MachineState.FAULT).accepted)


if __name__ == "__main__":
    unittest.main()
