# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Flight-state table and simulated vehicle
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Which flight request is legal from which vehicle state, and a vehicle that cannot fly.

Three things that must never be mixed up with a flight order are kept apart:

* **Telemetry** is a read-only snapshot (`Telemetry`); nothing that reads it
  can change the vehicle.
* **Authorization** is a separate, explicit permission (`grant_authorization`
  / `revoke_authorization`). Without it a request that leaves the ground is
  refused, whatever the cell state says.
* **Arming** is a state of the vehicle (`ARMED`), reached only by an accepted
  `ARM` request and left by landing, return or disarm.

`RETURN_TO_LAUNCH` and `LAND` are always accepted: a stop must never be
blocked by authorization or state. The simulated vehicle is pure bookkeeping -
it imports no transport and cannot produce real flight.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .coordinator import UavCoordinator, UavDispatch


class UavState(str, Enum):
    GROUNDED = "GROUNDED"
    ARMED = "ARMED"
    AIRBORNE = "AIRBORNE"
    HOVERING = "HOVERING"
    RETURNING = "RETURNING"
    LANDING = "LANDING"


U = UavCoordinator
_AIR = frozenset({UavState.AIRBORNE, UavState.HOVERING, UavState.RETURNING, UavState.LANDING})

# request -> (states it may start from, state it leads to)
TRANSITIONS: dict[str, tuple[frozenset[UavState], UavState]] = {
    U.ARM: (frozenset({UavState.GROUNDED}), UavState.ARMED),
    U.TAKEOFF: (frozenset({UavState.ARMED}), UavState.AIRBORNE),
    U.GOTO_WAYPOINT: (frozenset({UavState.AIRBORNE, UavState.HOVERING}), UavState.AIRBORNE),
    U.HOVER_AND_CAPTURE: (frozenset({UavState.AIRBORNE}), UavState.HOVERING),
    # A stop is legal from every state; the target depends on where it starts.
    U.RETURN_TO_LAUNCH: (frozenset(UavState), UavState.RETURNING),
    U.LAND: (frozenset(UavState), UavState.LANDING),
}

# Requests that take the vehicle off the ground and therefore need a grant.
NEEDS_AUTHORIZATION = frozenset({U.ARM, U.TAKEOFF, U.GOTO_WAYPOINT, U.HOVER_AND_CAPTURE})
ALWAYS_ACCEPTED = frozenset({U.RETURN_TO_LAUNCH, U.LAND})


@dataclass(frozen=True)
class Telemetry:
    """A read-only snapshot. Building one never changes the vehicle."""

    state: UavState
    armed: bool
    authorized: bool
    accepted_requests: int
    refused_requests: int


@dataclass(frozen=True)
class StepResult:
    applied: bool
    state: UavState
    reason: str


class SimulatedUav:
    """Follows `TRANSITIONS`; performs no real flight and reaches no transport."""

    def __init__(self) -> None:
        self._state = UavState.GROUNDED
        self._authorized = False
        self._accepted = 0
        self._refused = 0

    # -- authorization: its own explicit act, never implied by a request ----
    def grant_authorization(self) -> None:
        self._authorized = True

    def revoke_authorization(self) -> None:
        self._authorized = False

    # -- telemetry: read only -------------------------------------------------
    def telemetry(self) -> Telemetry:
        return Telemetry(
            state=self._state,
            armed=self._state is not UavState.GROUNDED,
            authorized=self._authorized,
            accepted_requests=self._accepted,
            refused_requests=self._refused,
        )

    def complete_landing(self) -> None:
        """The vehicle reports touchdown: back to GROUNDED and disarmed, authorization dropped."""
        if self._state in (UavState.LANDING, UavState.RETURNING):
            self._state = UavState.GROUNDED
            self._authorized = False

    def _refuse(self, reason: str) -> StepResult:
        self._refused += 1
        return StepResult(False, self._state, reason)

    def apply(self, dispatch: UavDispatch) -> StepResult:
        request = dispatch.request
        if request not in TRANSITIONS:
            return self._refuse(f"unknown request {request!r}")
        if request in ALWAYS_ACCEPTED:
            # A stop is honoured whatever the coordinator or authorization said.
            if self._state in (UavState.GROUNDED, UavState.ARMED):
                self._state = UavState.GROUNDED
                self._authorized = False
            else:
                self._state = TRANSITIONS[request][1]
            self._accepted += 1
            return StepResult(True, self._state, "ok")
        if not dispatch.accepted:
            return self._refuse(f"dispatch not accepted: {dispatch.reason}")
        if request in NEEDS_AUTHORIZATION and not self._authorized:
            return self._refuse(f"{request} needs an explicit authorization")
        allowed_from, target = TRANSITIONS[request]
        if self._state not in allowed_from:
            return self._refuse(f"{request} is not legal from {self._state.value}")
        self._state = target
        self._accepted += 1
        return StepResult(True, target, "ok")
