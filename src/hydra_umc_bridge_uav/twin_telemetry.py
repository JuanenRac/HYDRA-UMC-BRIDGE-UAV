# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Position and attitude of the vehicle as a TwinState for the digital twin
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Feed the digital twin with where the vehicle is and how it is turned.

The vehicle reports GLOBAL_POSITION_INT (latitude and longitude in degrees * 1e7, altitudes in millimetres) and ATTITUDE (roll, pitch, yaw in radians). The twin
speaks the `TwinState` contract of the SDK: poses in metres with a unit quaternion. `TwinFeed` joins the two readings into one TwinState:

* one pose, named `vehicle`, in the frame `base`; its position is metres EAST, NORTH and UP of the home point (the same home as the flight envelope), flat-earth
  approximation, which is exact enough for a flight of a few kilometres;
* its orientation is the unit quaternion of roll, pitch and yaw (the Z-Y-X order of the SDK), so there is no gimbal lock to explain to a consumer;
* `source` is `real` or `simulated` as the configuration says (`twin.source` in `config/uav.json`), never guessed, because a consumer that mixes the two up would act
  on a vehicle that is not there; there are no joints (`joint_states` is an empty list);
* every state is checked with the SDK's own `validate_twin_state` before it is handed over; a state that does not pass is not sent;
* at most one state per `twin.min_interval_s`.

This module only builds and hands over the state (to a callable the bridge gives it); the receiving side in the twin is a separate piece. Not run against a real vehicle.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Callable, Mapping

from hydra_umc_sdk.pose import Quaternion
from hydra_umc_sdk.twin_state import validate_twin_state
from hydra_umc_sdk.validation import ContractValidationError

from .mavlink_transport import _EARTH_RADIUS_M, _is_real_number
from .settings import TwinSettings

POSE_NAME = "vehicle"


def _finite(value: object) -> bool:
    return _is_real_number(value) and math.isfinite(value)  # type: ignore[arg-type]


class TwinFeed:
    """Keeps the last position and attitude and builds a TwinState from them."""

    def __init__(
        self,
        home_lat: float,
        home_lon: float,
        settings: TwinSettings | None = None,
        send: Callable[[dict[str, object]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not (_finite(home_lat) and -90 <= home_lat <= 90 and _finite(home_lon) and -180 <= home_lon <= 180):
            raise ValueError("home_lat and home_lon must be a latitude and a longitude")
        self.settings = settings if settings is not None else TwinSettings()
        problems = self.settings.problems()
        if problems:
            raise ValueError("; ".join(problems))
        self._home_lat, self._home_lon = float(home_lat), float(home_lon)
        self._send = send
        self._clock = clock
        self._position: tuple[float, float, float] | None = None  # east, north, up (m)
        self._attitude: Quaternion | None = None
        self._sequence = 0
        self._last_sent = -math.inf
        self.refused = 0

    def observe(self, message: object) -> dict[str, object] | None:
        """Take a GLOBAL_POSITION_INT or an ATTITUDE; returns the TwinState when both are known and the interval has passed, else None."""
        get_type = getattr(message, "get_type", None)
        kind = get_type() if callable(get_type) else None
        if kind == "GLOBAL_POSITION_INT":
            self._read_position(message)
        elif kind == "ATTITUDE":
            self._read_attitude(message)
        else:
            return None
        return self._maybe_emit()

    def _read_position(self, message: object) -> None:
        lat, lon, rel = (getattr(message, name, None) for name in ("lat", "lon", "relative_alt"))
        if not all(type(v) is int for v in (lat, lon, rel)):
            self.refused += 1
            return
        if not (-900000000 <= lat <= 900000000 and -1800000000 <= lon <= 1800000000):  # type: ignore[operator]
            self.refused += 1
            return
        d_lat = math.radians(lat / 1e7 - self._home_lat)  # type: ignore[operator]
        d_lon = math.radians(lon / 1e7 - self._home_lon)  # type: ignore[operator]
        north = d_lat * _EARTH_RADIUS_M
        east = d_lon * _EARTH_RADIUS_M * math.cos(math.radians(self._home_lat))
        self._position = (east, north, rel / 1000.0)  # type: ignore[operator]

    def _read_attitude(self, message: object) -> None:
        values = [getattr(message, name, None) for name in ("roll", "pitch", "yaw")]
        if not all(_finite(v) for v in values):
            self.refused += 1
            return
        self._attitude = Quaternion.from_euler(*[float(v) for v in values]).normalized().canonical()  # type: ignore[arg-type]

    def state(self) -> dict[str, object] | None:
        """The TwinState of the last readings, or None when one of them is missing."""
        if self._position is None or self._attitude is None:
            return None
        east, north, up = self._position
        q = self._attitude
        now = datetime.fromtimestamp(self._clock(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return {
            "schema_version": "1.0",
            "source": self.settings.source,
            "robot_id": self.settings.robot_id,
            "frame": "base",
            "timestamp_utc": now,
            "sequence": self._sequence,
            "joint_states": [],
            "poses": [
                {
                    "name": POSE_NAME,
                    "pose": {
                        "schema_version": "1.0",
                        "frame": "base",
                        "position": {"x": east, "y": north, "z": up},
                        "orientation": {"w": q.w, "x": q.x, "y": q.y, "z": q.z},
                    },
                }
            ],
        }

    def _maybe_emit(self) -> dict[str, object] | None:
        now = self._clock()
        if now - self._last_sent < self.settings.min_interval_s:
            return None
        state = self.state()
        if state is None:
            return None
        try:
            validate_twin_state(state)  # type: ignore[arg-type]
        except ContractValidationError:
            self.refused += 1
            return None
        self._last_sent = now
        self._sequence += 1
        if self._send is not None:
            self._send(state)
        return state


def from_mapping(values: Mapping[str, object]) -> tuple[float, float]:
    """The home point from a mapping with `home_lat` and `home_lon`, for a caller that keeps them with the flight envelope."""
    lat, lon = values.get("home_lat"), values.get("home_lon")
    if not (_finite(lat) and _finite(lon)):
        raise ValueError("home_lat and home_lon must be numbers")
    return float(lat), float(lon)  # type: ignore[arg-type]
