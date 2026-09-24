# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Flight-state table and simulated vehicle tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================

import unittest

from hydra_umc_bridge_uav import BridgeJob, CellState, JobPhase, MachineState, UavCoordinator
from hydra_umc_bridge_uav.simulated_uav import (
    ALWAYS_ACCEPTED,
    NEEDS_AUTHORIZATION,
    TRANSITIONS,
    SimulatedUav,
    UavState,
)


def dispatch(phase, cell=CellState.READY):
    job = BridgeJob("job-1", "idem-1", "uav-1", phase, MachineState.IDLE, {})
    return UavCoordinator().dispatch(job, cell)


def flown(uav, *phases):
    uav.grant_authorization()
    for phase in phases:
        assert uav.apply(dispatch(phase)).applied, phase


class SimulatedUavTests(unittest.TestCase):
    def test_every_request_has_a_rule(self):
        requests = set(UavCoordinator._phase_requests.values()) | {UavCoordinator.LAND}
        self.assertEqual(requests, set(TRANSITIONS))

    def test_a_full_sortie_walks_the_table(self):
        uav = SimulatedUav()
        flown(uav, JobPhase.PREPARE, JobPhase.LOAD, JobPhase.PROCESS, JobPhase.UNLOAD)
        self.assertEqual(uav.telemetry().state, UavState.HOVERING)
        self.assertTrue(uav.apply(dispatch(JobPhase.COMPLETE)).applied)
        self.assertEqual(uav.telemetry().state, UavState.RETURNING)
        uav.complete_landing()
        self.assertEqual(uav.telemetry().state, UavState.GROUNDED)
        self.assertFalse(uav.telemetry().authorized)

    def test_nothing_that_leaves_the_ground_runs_without_authorization(self):
        for phase in (JobPhase.PREPARE,):
            uav = SimulatedUav()
            result = uav.apply(dispatch(phase))
            self.assertFalse(result.applied)
            self.assertIn("authorization", result.reason)
            self.assertEqual(uav.telemetry().state, UavState.GROUNDED)
        self.assertTrue(NEEDS_AUTHORIZATION.isdisjoint(ALWAYS_ACCEPTED))

    def test_authorization_does_not_arm_and_arming_does_not_authorize(self):
        uav = SimulatedUav()
        uav.grant_authorization()
        self.assertFalse(uav.telemetry().armed)
        uav.revoke_authorization()
        uav.grant_authorization()
        uav.apply(dispatch(JobPhase.PREPARE))
        uav.revoke_authorization()
        self.assertTrue(uav.telemetry().armed)
        self.assertFalse(uav.apply(dispatch(JobPhase.LOAD)).applied)

    def test_out_of_order_requests_are_refused(self):
        uav = SimulatedUav()
        uav.grant_authorization()
        result = uav.apply(dispatch(JobPhase.LOAD))
        self.assertFalse(result.applied)
        self.assertEqual(uav.telemetry().state, UavState.GROUNDED)

    def test_a_request_the_coordinator_refused_changes_nothing(self):
        uav = SimulatedUav()
        uav.grant_authorization()
        self.assertFalse(uav.apply(dispatch(JobPhase.PREPARE, cell=CellState.SAFE_STOP)).applied)
        self.assertEqual(uav.telemetry().state, UavState.GROUNDED)

    def test_stop_and_land_are_never_blocked(self):
        for state in UavState:
            for request in (UavCoordinator.RETURN_TO_LAUNCH, UavCoordinator.LAND):
                uav = SimulatedUav()
                uav._state = state
                refused = dispatch(JobPhase.ABORT, cell=CellState.FAULT)
                refused = type(refused)(False, request, "refused upstream")
                self.assertTrue(uav.apply(refused).applied, (state, request))

    def test_telemetry_is_a_snapshot_that_cannot_change_the_vehicle(self):
        uav = SimulatedUav()
        snapshot = uav.telemetry()
        with self.assertRaises(Exception):
            snapshot.state = UavState.AIRBORNE  # frozen dataclass
        self.assertEqual(uav.telemetry().state, UavState.GROUNDED)

    def test_module_imports_no_transport(self):
        import hydra_umc_bridge_uav.simulated_uav as module

        code = open(module.__file__, encoding="utf-8").read().split('"""', 2)[2]
        self.assertNotIn("mavlink_transport", code)


if __name__ == "__main__":
    unittest.main()
