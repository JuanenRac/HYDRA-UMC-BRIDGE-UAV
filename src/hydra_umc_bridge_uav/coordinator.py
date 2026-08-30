# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - UAV flight-request coordinator
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Map a correlated cell job onto a named high-level flight request.

This module deliberately never computes flight control, stabilization or a
real trajectory - it validates and forwards a small, named vocabulary of
flight requests, mirroring what a real flight controller (Pixhawk/PX4 over
MAVLink, or a DJI OSDK-class SDK) already exposes at its own high level.
Real-time stabilization, and the actual RTL/Hover failsafe execution, stay
the flight controller's own independent authority - see heartbeat.py for
this bridge's own, separate link-loss signal, which is a coordination-layer
concern, not a replacement for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, evaluate_job


@dataclass(frozen=True)
class UavDispatch:
    accepted: bool
    request: str
    reason: str
    mode: str = "plan-only"


@dataclass(frozen=True)
class UavRequestPlan:
    """Static evidence of the real flight-request vocabulary."""

    schema_version: str
    mode: str
    requests: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "mode": self.mode, "requests": list(self.requests)}


class UavCoordinator:
    """Gate jobs before a future MAVLink/OSDK transport adapter reaches a real UAV."""

    PRE_FLIGHT_CHECK = "PRE_FLIGHT_CHECK"
    TAKEOFF = "TAKEOFF"
    GOTO_WAYPOINT = "GOTO_WAYPOINT"
    HOVER_AND_CAPTURE = "HOVER_AND_CAPTURE"
    RETURN_TO_LAUNCH = "RETURN_TO_LAUNCH"

    # ABORT maps to the real, standard UAV failsafe action name
    # (RTL/"Return to Launch") - the same action a link-loss failsafe
    # would trigger, matching the pasted architecture note this project
    # started from: "if it loses connection for more than N seconds, RTL
    # or autonomous hover".
    _phase_requests = {
        JobPhase.PREPARE: PRE_FLIGHT_CHECK,
        JobPhase.LOAD: TAKEOFF,
        JobPhase.PROCESS: GOTO_WAYPOINT,
        JobPhase.UNLOAD: HOVER_AND_CAPTURE,
        JobPhase.COMPLETE: RETURN_TO_LAUNCH,
        JobPhase.ABORT: RETURN_TO_LAUNCH,
    }

    def request_plan(self) -> UavRequestPlan:
        """Return the static flight-request vocabulary without opening any real transport."""

        return UavRequestPlan("1.0", "plan-only", tuple(dict.fromkeys(self._phase_requests.values())))

    def dispatch(self, job: BridgeJob, cell_state: CellState) -> UavDispatch:
        request = self._phase_requests.get(job.phase)
        if request is None:
            return UavDispatch(False, "none", "job phase has no mapped flight request")
        decision = evaluate_job(job, cell_state)
        return UavDispatch(decision.allowed, request, decision.reason)
