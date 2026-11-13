# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Mission upload (MAVLink mission protocol) with a flight-envelope check
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Upload a whole mission to the autopilot, after checking every item against the flight envelope.

The single commands of `mavlink_transport` send the vehicle to one point at a time. A mission is the list of points the autopilot flies by itself, so a point that is
outside the volume the deployment allows would be flown without the bridge in the loop. For that reason nothing leaves the bridge until EVERY item has passed:

* the upload switch is on (`mission.upload_enabled` in `config/uav.json`, off by default);
* a `FlightEnvelope` is given (a mission without a geofence is refused, not guessed);
* the number of items is within `mission.max_items`, and each item is a known kind with finite numbers;
* every waypoint, takeoff and landing point is inside the envelope (the envelope is a disc around home, so a segment between two points inside it is inside too).

Then the protocol of the MAVLink mission microservice runs: MISSION_COUNT, the vehicle asks for each item with MISSION_REQUEST_INT, the bridge answers with MISSION_ITEM_INT,
and the vehicle closes with MISSION_ACK. Only `MAV_MISSION_ACCEPTED` is a success. This module never starts the mission (no MISSION_START, no mode change): arming,
taking off and starting remain separate, gated requests. Nothing here has run against a real autopilot; the wire format is checked against the emulator in tests/ only.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .mavlink_transport import FlightEnvelope, _is_real_number
from .settings import MissionSettings

MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_TAKEOFF = 22
MAV_FRAME_MISSION = 2
MAV_FRAME_GLOBAL_INT = 5
MAV_CMD_NAV_FENCE_CIRCLE_INCLUSION = 5003
MAV_MISSION_TYPE_MISSION = 0
MAV_MISSION_TYPE_FENCE = 1
MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6

MISSION_RESULT_NAMES = {
    0: "accepted",
    1: "error",
    2: "unsupported frame",
    3: "unsupported command",
    4: "no space",
    5: "invalid",
    13: "denied",
    14: "operation cancelled",
}
_ACCEPTED = 0

KINDS = ("takeoff", "waypoint", "land", "return_to_launch")


@dataclass(frozen=True)
class MissionItem:
    kind: str
    lat: float | None = None
    lon: float | None = None
    alt_m: float | None = None
    hold_s: float = 0.0


@dataclass(frozen=True)
class MissionUploadResult:
    sent: bool
    reason: str
    items: int = 0


def parse_items(raw: object) -> tuple[list[MissionItem], str | None]:
    """The items of a mission from plain data (a list of objects). Returns (items, None) or ([], the reason it is not a mission)."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        return [], "a mission is a non-empty list of items"
    items: list[MissionItem] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            return [], f"item {index} must be an object"
        kind = entry.get("kind")
        if kind not in KINDS:
            return [], f"item {index}: kind must be one of {', '.join(KINDS)}"
        unknown = sorted(set(entry) - {"kind", "lat", "lon", "alt_m", "hold_s"})
        if unknown:
            return [], f"item {index}: unknown keys {', '.join(unknown)}"
        needs = {"takeoff": ("alt_m",), "waypoint": ("lat", "lon", "alt_m"), "land": ("lat", "lon"), "return_to_launch": ()}[kind]
        for name in needs:
            if not _is_real_number(entry.get(name)) or not math.isfinite(entry[name]):
                return [], f"item {index}: {name} must be a finite number"
        for name in ("lat", "lon", "alt_m", "hold_s"):
            if name in entry and (not _is_real_number(entry[name]) or not math.isfinite(entry[name])):
                return [], f"item {index}: {name} must be a finite number"
        items.append(MissionItem(kind, entry.get("lat"), entry.get("lon"), entry.get("alt_m"), float(entry.get("hold_s", 0.0))))
    return items, None


def check_items(items: Sequence[MissionItem], envelope: FlightEnvelope | None, settings: MissionSettings) -> str | None:
    """The reason a mission may not be uploaded, or None when it may."""
    if not settings.upload_enabled:
        return "the mission upload is switched off (mission.upload_enabled in config/uav.json)"
    if envelope is None:
        return "a mission needs a flight envelope: without a geofence the bridge does not upload one"
    if not items:
        return "the mission has no items"
    if len(items) > settings.max_items:
        return f"the mission has {len(items)} items, more than the {settings.max_items} the configuration allows"
    for index, item in enumerate(items):
        if item.kind in ("waypoint", "land"):
            if item.lat is None or item.lon is None:
                return f"item {index}: the position is missing"
            if not -90 <= item.lat <= 90 or not -180 <= item.lon <= 180:
                return f"item {index}: the position is outside the range of a latitude and a longitude"
            refused = envelope.refusal_for_position(item.lat, item.lon)
            if refused:
                return f"item {index}: {refused}"
        if item.kind in ("waypoint", "takeoff") and item.alt_m is not None:
            if item.alt_m <= 0:
                return f"item {index}: the altitude must be above the takeoff point"
            refused = envelope.refusal_for_altitude(item.alt_m)
            if refused:
                return f"item {index}: {refused}"
        if item.hold_s < 0:
            return f"item {index}: the hold time cannot be negative"
    return None


def _item_message(item: MissionItem) -> tuple[int, int, float, float, int, int, float]:
    """(frame, command, param1, param2, x, y, z) of one MISSION_ITEM_INT."""
    if item.kind == "takeoff":
        return MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, MAV_CMD_NAV_TAKEOFF, 0.0, 0.0, 0, 0, float(item.alt_m or 0.0)
    if item.kind == "waypoint":
        return MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, MAV_CMD_NAV_WAYPOINT, item.hold_s, 0.0, round(float(item.lat) * 1e7), round(float(item.lon) * 1e7), float(item.alt_m)  # type: ignore[arg-type]
    if item.kind == "land":
        return MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, MAV_CMD_NAV_LAND, 0.0, 0.0, round(float(item.lat) * 1e7), round(float(item.lon) * 1e7), 0.0  # type: ignore[arg-type]
    return MAV_FRAME_MISSION, MAV_CMD_NAV_RETURN_TO_LAUNCH, 0.0, 0.0, 0, 0, 0.0


#: The vehicle asks for the next item with MISSION_REQUEST_INT, or with the older MISSION_REQUEST until it has seen an integer item (PX4 does); both carry `seq`.
_AWAITED = ["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"]


def _message_type(message: object) -> str:
    """The MAVLink name of a message (pymavlink's get_type())."""
    get_type = getattr(message, "get_type", None)
    return str(get_type()) if callable(get_type) else ""


class MissionUploader:
    """Run the upload; every failure comes back as a reason, nothing raises for a refused or silent vehicle."""

    def __init__(self, settings: MissionSettings | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings if settings is not None else MissionSettings()
        self._clock = clock

    def upload(self, sink: object, target_system: int, target_component: int, items: Sequence[MissionItem], envelope: FlightEnvelope | None) -> MissionUploadResult:
        refused = check_items(items, envelope, self.settings)
        if refused:
            return MissionUploadResult(False, refused)
        messages = [_item_message(item) for item in items]
        return self._protocol(sink, target_system, target_component, messages, MAV_MISSION_TYPE_MISSION, "the vehicle accepted the mission (it was not started)")

    def upload_geofence(self, sink: object, target_system: int, target_component: int, envelope: FlightEnvelope | None, *, enabled: bool) -> MissionUploadResult:
        """Load the radius of the envelope into the autopilot as one circular inclusion fence (MAVLink fence protocol, mission type 1).

        Only with `enabled` (`envelope.upload_geofence`, off by default) and an envelope. The altitude ceiling is not part of this fence: an autopilot keeps it as a
        parameter of its own, which the bridge does not write. The vehicle is not told to use the fence; enabling it is the autopilot's own configuration.
        """
        if not enabled:
            return MissionUploadResult(False, "the geofence upload is switched off (envelope.upload_geofence in config/uav.json)")
        if envelope is None:
            return MissionUploadResult(False, "a geofence needs a flight envelope")
        messages = [(MAV_FRAME_GLOBAL_INT, MAV_CMD_NAV_FENCE_CIRCLE_INCLUSION, float(envelope.max_radius_m), 1.0, round(envelope.home_lat * 1e7), round(envelope.home_lon * 1e7), 0.0)]
        return self._protocol(sink, target_system, target_component, messages, MAV_MISSION_TYPE_FENCE, "the vehicle accepted the geofence")

    def _protocol(
        self, sink: object, target_system: int, target_component: int, messages: Sequence[tuple[int, int, float, float, int, int, float]], mission_type: int, success: str
    ) -> MissionUploadResult:
        try:
            sink.mission_count_send(target_system, target_component, len(messages), mission_type)  # type: ignore[attr-defined]
            served: set[int] = set()
            deadline = self._clock() + self.settings.total_timeout_s
            while True:
                if self._clock() > deadline:
                    return MissionUploadResult(False, "the vehicle did not close the upload in time", len(served))
                # ONE wait for either answer: waiting for a kind drops every message of another kind that arrives meanwhile, so asking for the
                # acknowledgement and for the next request one after the other could throw away the one that was wanted
                request = sink.recv_match(_AWAITED, True, self.settings.request_timeout_s)  # type: ignore[attr-defined]
                if request is None:
                    return MissionUploadResult(False, "the vehicle did not ask for the next item in time", len(served))
                if _message_type(request) == "MISSION_ACK":
                    return self._closed(request, len(messages), len(served), success)
                seq = getattr(request, "seq", None)
                if type(seq) is not int or not 0 <= seq < len(messages):
                    return MissionUploadResult(False, f"the vehicle asked for item {seq!r}, which is not in the mission", len(served))
                frame, command, p1, p2, x, y, z = messages[seq]
                sink.mission_item_int_send(  # type: ignore[attr-defined]
                    target_system, target_component, seq, frame, command, 0, 1, p1, p2, 0.0, 0.0, x, y, z, mission_type
                )
                served.add(seq)
        except Exception as error:  # noqa: BLE001 - a lost link or a sink that cannot send: fail closed with a reason
            return MissionUploadResult(False, f"the upload failed: {type(error).__name__}: {error}")

    @staticmethod
    def _closed(ack: object, count: int, served: int, success: str = "the vehicle accepted the mission (it was not started)") -> MissionUploadResult:
        result = getattr(ack, "type", None)
        if result == _ACCEPTED and served >= count:
            return MissionUploadResult(True, success, count)
        if result == _ACCEPTED:
            return MissionUploadResult(False, "the vehicle closed the upload before it had every item", served)
        name = MISSION_RESULT_NAMES.get(result, f"result {result!r}") if isinstance(result, int) else "an unreadable result"
        return MissionUploadResult(False, f"the vehicle refused the mission: {name}", served)
