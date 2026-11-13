# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Flight policy of the job entry: required envelope, human confirmation, flight phases
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""What the job entry asks BEFORE the shared gate of the SDK sees a request that starts or continues a flight.

Three rules, one design. All of them only add refusals; none relaxes the gate of the SDK, and a request that brings the vehicle to a safe state (return to launch,
land, disarm) never goes through this policy at all, so a vehicle in the air can always come home.

* The flight envelope is required (`envelope.required`, on by default). Without a home point and limits, a request to arm, take off, go to a point or hover is
  refused; with them, the altitude and the position the job names must be inside the envelope. The limits are in `config/uav.json`, never in the code.
* A human confirmation signed by the server, valid once, must come with the request (`confirmation.required`, on by default; see `human_confirmation.py`).
  The token is checked against the request and the job it names and is used up only when the request is accepted, so a request that is refused for another reason
  does not burn it.
* A vehicle that is already flying can take the next productive request (a go-to or a hover) only when `phases.chain_in_flight` is on (off by default), the
  previous request of the flight was accepted and sent, no request to come home came after it, the last one was not longer ago than `phases.chain_max_gap_s`, the
  envelope is active and the confirmation is valid. Arming and taking off are never chained: they start from the ground.

Evidence: simulator and emulator only. Nothing has flown.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from hydra_umc_sdk.bridge_contract import MachineState

from .human_confirmation import Confirmation, ConfirmationError, ConfirmationVerifier
from .mavlink_transport import FlightEnvelope
from .settings import ConfirmationSettings, EnvelopeSettings, PhaseSettings

CONFIRMATION_FIELD = "operator_confirmation"
CHAINABLE = ("GOTO_WAYPOINT", "HOVER_AND_CAPTURE")
_SAFE = ("RETURN_TO_LAUNCH", "LAND", "DISARM")


@dataclass(frozen=True)
class PolicyVerdict:
    accepted: bool
    reason: str
    # what the gate of the SDK is to be told the vehicle is doing (set only when a flight is chained); None leaves what the autopilot reported
    machine_state: MachineState | None = None
    confirmation: Confirmation | None = None
    request: str | None = None


def _parameter(parameters: Mapping[str, object], name: str) -> float | None:
    raw = parameters.get(name)
    if raw is None:
        return None
    try:
        value = float(str(raw))
    except ValueError:
        raise ValueError(f"parameter {name} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"parameter {name} is not a finite number")
    return value


class FlightPolicy:
    def __init__(
        self,
        envelope: EnvelopeSettings | None = None,
        confirmation: ConfirmationSettings | None = None,
        phases: PhaseSettings | None = None,
        verifier: ConfirmationVerifier | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.envelope_settings = envelope if envelope is not None else EnvelopeSettings()
        self.confirmation_settings = confirmation if confirmation is not None else ConfirmationSettings()
        self.phase_settings = phases if phases is not None else PhaseSettings()
        self.verifier = verifier
        self._clock = clock
        self._flight_ok = False
        self._last_request_at = 0.0
        self.envelope: FlightEnvelope | None = None
        settings = self.envelope_settings
        if settings.home_lat is not None and settings.home_lon is not None:
            self.envelope = FlightEnvelope(settings.max_altitude_m, settings.max_radius_m, settings.home_lat, settings.home_lon)

    def reset_flight(self) -> None:
        self._flight_ok = False

    def review(self, payload: Mapping[str, object], request: str | None, observed: MachineState | None) -> PolicyVerdict:
        """Whether the request may go on to the gate of the SDK, and with which view of the machine."""
        if request is None or request in _SAFE:
            return PolicyVerdict(True, "no policy applies", request=request)
        envelope_on = self.envelope_settings.required
        if envelope_on and self.envelope is None:
            return PolicyVerdict(False, "no flight envelope is configured (envelope.home_lat and envelope.home_lon in config/uav.json): the request is refused", request=request)
        if self.envelope is not None:
            problem = self._check_envelope(payload, request)
            if problem:
                return PolicyVerdict(False, problem, request=request)
        confirmation: Confirmation | None = None
        if self.confirmation_settings.required and request in self.confirmation_settings.actions:
            if self.verifier is None:
                return PolicyVerdict(False, "no confirmation key is configured (confirmation.key_file in config/uav.json): the request is refused", request=request)
            job_id = payload.get("job_id")
            try:
                confirmation = self.verifier.verify(payload.get(CONFIRMATION_FIELD), action=request, job_id=job_id if isinstance(job_id, str) else "")
            except ConfirmationError as error:
                return PolicyVerdict(False, str(error), request=request)
        state: MachineState | None = None
        if observed is MachineState.RUNNING and request in CHAINABLE:
            chained = self._chain_problem(envelope_on, confirmation)
            if chained is not None:
                return PolicyVerdict(False, chained, request=request)
            state = MachineState.IDLE
        return PolicyVerdict(True, "the flight policy allows the request", state, confirmation, request)

    def _chain_problem(self, envelope_on: bool, confirmation: Confirmation | None) -> str | None:
        """None when a request may continue a flight in the air; otherwise why it may not."""
        if not self.phase_settings.chain_in_flight:
            return "the vehicle is not on the ground and chaining requests in flight is switched off (phases.chain_in_flight)"
        if not envelope_on or self.envelope is None:
            return "chaining requests in flight needs the flight envelope to be required and configured"
        if self.confirmation_settings.required is not True or confirmation is None:
            return "chaining requests in flight needs a human confirmation"
        if not self._flight_ok:
            return "the previous request of the flight was not accepted and sent: the next one is not chained"
        if self._clock() - self._last_request_at > self.phase_settings.chain_max_gap_s:
            return "the previous request of the flight is too old to be chained"
        return None

    def _check_envelope(self, payload: Mapping[str, object], request: str) -> str | None:
        assert self.envelope is not None
        parameters = payload.get("parameters")
        if not isinstance(parameters, Mapping):
            return None
        try:
            if request == "TAKEOFF":
                altitude = _parameter(parameters, "takeoff_altitude_m")
                return self.envelope.refusal_for_altitude(altitude) if altitude is not None else None
            if request == "GOTO_WAYPOINT":
                altitude = _parameter(parameters, "waypoint_alt_m")
                lat, lon = _parameter(parameters, "waypoint_lat"), _parameter(parameters, "waypoint_lon")
                refusal = self.envelope.refusal_for_altitude(altitude) if altitude is not None else None
                if refusal is None and lat is not None and lon is not None:
                    refusal = self.envelope.refusal_for_position(lat, lon)
                return refusal
        except ValueError as error:
            return str(error)
        return None

    def use_confirmation(self, verdict: PolicyVerdict) -> str | None:
        """Called when the request was accepted: uses the confirmation up. Returns a reason when it could not be used (it was used meanwhile): the caller must then refuse."""
        if verdict.confirmation is not None and self.verifier is not None and not self.verifier.consume(verdict.confirmation):
            return "this confirmation was already used"
        return None

    def note_flight(self, verdict: PolicyVerdict, *, sent: bool) -> None:
        """Called with the final answer of an accepted request: a request to come home ends the flight for chaining; any other one is the last request of the flight."""
        request = verdict.request
        if request in _SAFE:
            self._flight_ok = False
        elif request is not None:
            self._flight_ok = bool(sent)
            self._last_request_at = self._clock()
