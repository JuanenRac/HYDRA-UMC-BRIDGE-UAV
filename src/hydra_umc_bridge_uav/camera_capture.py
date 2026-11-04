# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Geotagged capture: CAMERA_IMAGE_CAPTURED to an event and a data-lake sample
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Turn the camera's CAMERA_IMAGE_CAPTURED message into a geotagged event, so an inspection flight closes its loop.

`HOVER_AND_CAPTURE` already asks the camera for one image (MAV_CMD_IMAGE_START_CAPTURE). The camera answers, when the picture is taken, with CAMERA_IMAGE_CAPTURED:
where the vehicle was (degrees * 1e7 and millimetres), the attitude, the index of the image and where the file is. This module reads that message, checks it, and gives
back two plain shapes that other services read:

* `capture_event()`: the event for the vision node (position, altitude, attitude, image index, file URL and the success flag), the file itself stays on the camera;
* `datalake_sample()`: the same facts as a sample of `POST /ingest` of the data lake (numbers only), so the position of every picture is queryable.

A message that cannot be read, a position outside the range of a latitude and a longitude, or a failed capture never becomes a "good" picture: the event says `captured:
false` and carries the reason. Nothing here talks to the network; `wait_for_capture()` only reads from the sink the bridge already has. Not run against a real camera.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Mapping

from .mavlink_transport import _is_real_number
from .settings import CameraSettings

EVENT_SCHEMA = "uav-image-captured/1"
DATALAKE_KIND = "uav_image"
_URL_MAX = 205  # the size of file_url in the message
_CAPTURE_OK = 1


def _int_of(message: object, name: str) -> int | None:
    value = getattr(message, name, None)
    return value if type(value) is int else None


def capture_event(message: object) -> dict[str, object]:
    """The event of one CAMERA_IMAGE_CAPTURED message; `captured` is true only for a successful capture with a position that can be a place on Earth."""
    lat_e7, lon_e7 = _int_of(message, "lat"), _int_of(message, "lon")
    alt_mm, rel_mm = _int_of(message, "alt"), _int_of(message, "relative_alt")
    index, result = _int_of(message, "image_index"), _int_of(message, "capture_result")
    time_utc_us = _int_of(message, "time_utc")
    camera_id = _int_of(message, "camera_id")
    url = getattr(message, "file_url", "")
    url = url.rstrip("\x00 ") if isinstance(url, str) else ""
    quaternion = getattr(message, "q", None)
    attitude: list[float] | None = None
    if isinstance(quaternion, (list, tuple)) and len(quaternion) == 4 and all(_is_real_number(v) and math.isfinite(v) for v in quaternion):
        attitude = [float(v) for v in quaternion]

    problems: list[str] = []
    if lat_e7 is None or lon_e7 is None or not (-900000000 <= lat_e7 <= 900000000 and -1800000000 <= lon_e7 <= 1800000000):
        problems.append("the position is missing or outside the range of a latitude and a longitude")
    if result != _CAPTURE_OK:
        problems.append("the camera did not report a successful capture")
    if index is None or index < 0:
        problems.append("the image index is missing")
    if len(url) > _URL_MAX:
        url = url[:_URL_MAX]
    event: dict[str, object] = {
        "schema": EVENT_SCHEMA,
        "captured": not problems,
        "reason": "; ".join(problems) if problems else "captured",
        "image_index": index,
        "camera_id": camera_id,
        "file_url": url,
        "time_utc_us": time_utc_us if time_utc_us else None,
        "lat": lat_e7 / 1e7 if lat_e7 is not None else None,
        "lon": lon_e7 / 1e7 if lon_e7 is not None else None,
        "alt_amsl_m": alt_mm / 1000.0 if alt_mm is not None else None,
        "alt_relative_m": rel_mm / 1000.0 if rel_mm is not None else None,
        "attitude_q": attitude,
    }
    return event


def datalake_sample(event: Mapping[str, object], source_id: str, now_ms: int, sequence: int | None = None) -> dict[str, object] | None:
    """The sample of `POST /ingest` for a successful capture (the data lake takes numbers only); None for a capture that failed."""
    if event.get("captured") is not True:
        return None
    fields: dict[str, float] = {}
    for key in ("lat", "lon", "alt_amsl_m", "alt_relative_m", "image_index"):
        value = event.get(key)
        if _is_real_number(value) and math.isfinite(value):  # type: ignore[arg-type]
            fields[key] = float(value)  # type: ignore[arg-type]
    quaternion = event.get("attitude_q")
    if isinstance(quaternion, list):
        for name, value in zip(("qw", "qx", "qy", "qz"), quaternion):
            fields[name] = float(value)
    stamp = event.get("time_utc_us")
    timestamp = int(stamp) // 1000 if _is_real_number(stamp) and stamp else now_ms  # type: ignore[arg-type]
    sample: dict[str, object] = {"sourceId": source_id, "kind": DATALAKE_KIND, "timestamp": timestamp, "fields": fields}
    if sequence is not None:
        sample["sequence"] = sequence
    return sample


def wait_for_capture(sink: object, settings: CameraSettings | None = None, clock: Callable[[], float] = time.monotonic) -> dict[str, object] | None:
    """Wait up to `camera.wait_for_image_s` for the next CAMERA_IMAGE_CAPTURED on the sink; its event, or None when the camera said nothing in time."""
    settings = settings if settings is not None else CameraSettings()
    deadline = clock() + settings.wait_for_image_s
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        message = sink.recv_match("CAMERA_IMAGE_CAPTURED", True, min(remaining, 0.5))  # type: ignore[attr-defined]
        if message is not None:
            return capture_event(message)
