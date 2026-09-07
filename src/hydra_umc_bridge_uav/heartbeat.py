# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Link-loss heartbeat failsafe monitor
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A real, deterministic link-loss failsafe state machine for this bridge.

The pasted architecture note this project started from is explicit that a
UAV bridge REQUIRES a heartbeat watchdog: "if it loses connection for more
than N seconds, RTL (Return to Launch) or autonomous Hover". This is that
watchdog - pure, dependency-free, and driven by an explicit `now` the caller
supplies rather than a hidden internal clock, so its every transition is
deterministic and testable without ever actually sleeping.

Honest boundary: this is THIS bridge's own coordination-layer signal for
"have I heard from the UAV recently enough to trust its state" - it is not,
and cannot be, a replacement for the flight controller's own independent,
firmware-level link-loss failsafe (Pixhawk/PX4's own RC/telemetry-loss
handling, or DJI's own equivalent). Both should exist; this one exists so
HYDRA-UMC's own coordination layer never keeps treating a UAV as reachable
past a real, bounded silence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class LinkStatus(str, Enum):
    OK = "OK"
    LOST = "LOST"


@dataclass(frozen=True)
class HeartbeatState:
    status: LinkStatus
    seconds_since_last_heartbeat: float
    # The real action this bridge would request if a transport adapter
    # existed - RETURN_TO_LAUNCH by default (see UavCoordinator's own
    # ABORT mapping), configurable to a hover-in-place policy instead.
    failsafe_action: str | None


class HeartbeatMonitor:
    """Tracks the last real heartbeat and reports link status against a real deadline.

    `now` is always explicitly supplied by the caller (never read from
    `time.time()` internally) - this keeps every transition in this class
    fully deterministic and testable with fake timestamps, not real sleeps.
    """

    def __init__(self, timeout_s: float, failsafe_action: str = "RETURN_TO_LAUNCH") -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be a finite, positive number of seconds")
        self._timeout_s = timeout_s
        self._failsafe_action = failsafe_action
        self._last_heartbeat_at: float | None = None

    def observe(self, now: float) -> None:
        """Record a real heartbeat received at `now`."""

        if not math.isfinite(now):
            raise ValueError("heartbeat timestamp must be finite")
        self._last_heartbeat_at = now

    def state(self, now: float) -> HeartbeatState:
        """Return the real link status as of `now`, never mutating monitor state.

        No heartbeat ever observed counts as LOST from the very first
        check - an unconfigured/never-connected monitor must never report
        a false OK.
        """

        if not math.isfinite(now):
            raise ValueError("state timestamp must be finite")
        if self._last_heartbeat_at is None:
            return HeartbeatState(LinkStatus.LOST, float("inf"), self._failsafe_action)
        elapsed = now - self._last_heartbeat_at
        if elapsed < 0:
            raise ValueError("now must not be earlier than the last observed heartbeat")
        if elapsed > self._timeout_s:
            return HeartbeatState(LinkStatus.LOST, elapsed, self._failsafe_action)
        return HeartbeatState(LinkStatus.OK, elapsed, None)
