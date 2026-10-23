# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Own ground-control-station heartbeat
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Send this bridge's own MAVLink HEARTBEAT as a ground control station.

An autopilot that is told to react to a lost ground station (ArduPilot `FS_GCS_ENABLE`, PX4 `NAV_DLL_ACT`)
decides that the station is gone when the HEARTBEATs of a station stop arriving. If this bridge sent none, the
autopilot could not tell a dead bridge from a quiet one, and its own failsafe would never fire when the
bridge dies. The bridge therefore sends a HEARTBEAT of type `MAV_TYPE_GCS` at a steady rate for as long as
it runs.

The rate is the deployment's choice: `HYDRA_UAV_GCS_HEARTBEAT_HZ` (default 1 Hz, the rate MAVLink
asks of a station; between 0.1 and 10 Hz) and `0` switches the heartbeat off for a link that another station
already keeps alive. A heartbeat that cannot be sent is counted and reported by `status()`; it never raises
into the bridge, and the next beat is tried on time.

The thread only sends; whether anything is sent to the vehicle besides this is decided, as before, by the
gate in front of `MavlinkFlightControl`.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

ENVIRONMENT_VARIABLE = "HYDRA_UAV_GCS_HEARTBEAT_HZ"
DEFAULT_RATE_HZ = 1.0
MIN_RATE_HZ = 0.1
MAX_RATE_HZ = 10.0

# MAVLink common.xml numeric ids.
MAV_TYPE_GCS = 6
MAV_AUTOPILOT_INVALID = 8
MAV_STATE_ACTIVE = 4


class HeartbeatSink(Protocol):
    """What a link has to offer; matches pymavlink's `MAVLink.heartbeat_send()`."""

    def heartbeat_send(self, type: int, autopilot: int, base_mode: int, custom_mode: int, system_status: int) -> object: ...


def parse_rate_hz(value: str | float | int | None) -> float:
    """The configured rate. Unset or empty is the default; 0 switches it off; anything else must be in range."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_RATE_HZ
    try:
        rate = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{ENVIRONMENT_VARIABLE} must be a number, got {value!r}") from error
    if not math.isfinite(rate) or rate < 0 or (rate != 0 and not MIN_RATE_HZ <= rate <= MAX_RATE_HZ):
        raise ValueError(
            f"{ENVIRONMENT_VARIABLE} must be 0 (off) or between {MIN_RATE_HZ:g} and {MAX_RATE_HZ:g} Hz, got {value!r}"
        )
    return rate


def rate_from_environment(environ: dict[str, str] | None = None) -> float:
    return parse_rate_hz((os.environ if environ is None else environ).get(ENVIRONMENT_VARIABLE))


@dataclass(frozen=True)
class HeartbeatStatus:
    running: bool
    rate_hz: float
    sent: int
    failed: int
    last_error: str | None


class GcsHeartbeat:
    """Sends the station heartbeat on a background thread, or on demand with `beat()` (used by the tests)."""

    def __init__(self, sink: HeartbeatSink, rate_hz: float = DEFAULT_RATE_HZ, *, wait: Callable[[threading.Event, float], bool] | None = None) -> None:
        self._sink = sink
        self._rate_hz = parse_rate_hz(rate_hz)
        self._wait = wait or (lambda stop, seconds: stop.wait(seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sent = 0
        self._failed = 0
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._rate_hz > 0

    def beat(self) -> bool:
        """Send one heartbeat now; True when the link took it."""
        try:
            self._sink.heartbeat_send(MAV_TYPE_GCS, MAV_AUTOPILOT_INVALID, 0, 0, MAV_STATE_ACTIVE)
        except Exception as error:  # noqa: BLE001 - a broken link must never stop the beats or the bridge
            with self._lock:
                self._failed += 1
                self._last_error = f"{type(error).__name__}: {error}"
            return False
        with self._lock:
            self._sent += 1
        return True

    def _run(self) -> None:
        period = 1.0 / self._rate_hz
        while not self._stop.is_set():
            self.beat()
            if self._wait(self._stop, period):
                break

    def start(self) -> bool:
        """Start the thread. False when the heartbeat is switched off or already running."""
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gcs-heartbeat", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None

    def status(self) -> HeartbeatStatus:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            return HeartbeatStatus(running, self._rate_hz, self._sent, self._failed, self._last_error)

    def __enter__(self) -> "GcsHeartbeat":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
