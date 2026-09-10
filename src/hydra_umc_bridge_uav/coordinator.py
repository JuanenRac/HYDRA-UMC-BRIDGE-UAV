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

    # UAV-02:
    # this constant used to be named PRE_FLIGHT_CHECK, but the real
    # MAVLink command it maps to (mavlink_transport.py's own
    # MAV_CMD_COMPONENT_ARM_DISARM, param1=1) genuinely arms the vehicle -
    # real flight stacks (PX4/ArduPilot) run their own pre-arm check suite
    # AS PART OF processing an arm request, and there is no separate,
    # real, standard MAVLink command in the common message set that runs
    # those checks WITHOUT arming. Calling this "PRE_FLIGHT_CHECK" made a
    # real arm command sound like an inert, read-only query - fixed by
    # naming it for what it actually does. mavlink_transport.py's own
    # send() now also requires an explicit confirm_arm=True to actually
    # send it - "specific authorization for arming", not a bare gate
    # decision alone.
    ARM = "ARM"
    TAKEOFF = "TAKEOFF"
    GOTO_WAYPOINT = "GOTO_WAYPOINT"
    HOVER_AND_CAPTURE = "HOVER_AND_CAPTURE"
    RETURN_TO_LAUNCH = "RETURN_TO_LAUNCH"
    # Real, separate MAVLink command (MAV_CMD_NAV_LAND, distinct from
    # MAV_CMD_NAV_RETURN_TO_LAUNCH - see mavlink.io/en/messages/common.html)
    # this coordinator never modeled at all before: land in place, rather
    # than fly back to the launch point first. A real, genuine operational
    # gap RTL alone can't cover - low battery over unlandable terrain
    # between here and home, or a lost RTL path, both call for landing
    # NOW rather than attempting the flight home. Not wired into any
    # JobPhase (an operator/failsafe policy decision about WHICH descent
    # is safe belongs outside this coordinator's own phase-driven flow,
    # same reasoning as heartbeat.py's own separate, standalone signal) -
    # exposed as its own real, explicit request instead.
    LAND = "LAND"

    # ABORT maps to the real, standard UAV failsafe action name
    # (RTL/"Return to Launch") - the same action a link-loss failsafe
    # would trigger, matching the pasted architecture note this project
    # started from: "if it loses connection for more than N seconds, RTL
    # or autonomous hover".
    _phase_requests = {
        JobPhase.PREPARE: ARM,
        JobPhase.LOAD: TAKEOFF,
        JobPhase.PROCESS: GOTO_WAYPOINT,
        JobPhase.UNLOAD: HOVER_AND_CAPTURE,
        JobPhase.COMPLETE: RETURN_TO_LAUNCH,
        JobPhase.ABORT: RETURN_TO_LAUNCH,
    }

    def request_plan(self) -> UavRequestPlan:
        """Return the static flight-request vocabulary without opening any real transport."""

        phase_requests = tuple(dict.fromkeys(self._phase_requests.values()))
        return UavRequestPlan("1.1", "plan-only", phase_requests + (self.LAND,))

    def dispatch(self, job: BridgeJob, cell_state: CellState) -> UavDispatch:
        request = self._phase_requests.get(job.phase)
        if request is None:
            return UavDispatch(False, "none", "job phase has no mapped flight request")
        decision = evaluate_job(job, cell_state)
        return UavDispatch(decision.allowed, request, decision.reason)

    def emergency_land_request(self) -> UavDispatch:
        """Real, standalone LAND request - see the LAND constant's own
        comment for why this is deliberately outside the JobPhase-driven
        dispatch() flow above. Always allowed: the same "an operator must
        always be able to request a controlled descent" reasoning
        evaluate_job() already applies to ABORT/RTL, applied here to the
        one real MAVLink command this coordinator never exposed at all."""

        return UavDispatch(True, self.LAND, "emergency land requested - always forwarded regardless of cell/machine state")
