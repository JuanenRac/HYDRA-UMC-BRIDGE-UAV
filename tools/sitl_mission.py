#!/usr/bin/env python3
# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - tools/sitl_mission.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# Mission upload check against an autopilot simulator (PX4 or ArduPilot SITL) that is already running: the bridge's own uploader
# (mavlink_mission.MissionUploader) sends a small mission inside a flight envelope around the position the simulator reports, the simulator asks
# for every item and closes with an acknowledgement. The check passes only when the simulator ACCEPTED the mission. The mission is never started:
# no arming, no mode change, no MISSION_START. A second mission with a point outside the envelope must be refused before anything leaves the bridge.
# The optional CI job (.github/workflows/sitl.yml) starts the simulator and runs this; it is not part of the normal checks.
#
#   py -3 tools/sitl_mission.py --connection udpin:0.0.0.0:14540
# =============================================================================
from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Any, Callable, Sequence

from hydra_umc_bridge_uav.mavlink_mission import MissionUploader, MissionUploadResult, parse_items
from hydra_umc_bridge_uav.mavlink_transport import FlightEnvelope
from hydra_umc_bridge_uav.settings import MissionSettings

METRES_PER_DEGREE = 111_320.0


def build_mission(home_lat: float, home_lon: float, altitude_m: float = 30.0, offset_m: float = 40.0) -> list[dict[str, Any]]:
    """A takeoff, two waypoints north and east of home, and a landing back at home, as the plain data the bridge reads."""
    north = offset_m / METRES_PER_DEGREE
    east = offset_m / (METRES_PER_DEGREE * max(math.cos(math.radians(home_lat)), 1e-6))
    return [
        {"kind": "takeoff", "alt_m": altitude_m},
        {"kind": "waypoint", "lat": home_lat + north, "lon": home_lon, "alt_m": altitude_m, "hold_s": 1.0},
        {"kind": "waypoint", "lat": home_lat + north, "lon": home_lon + east, "alt_m": altitude_m},
        {"kind": "land", "lat": home_lat, "lon": home_lon},
    ]


def build_outside_mission(home_lat: float, home_lon: float, radius_m: float) -> list[dict[str, Any]]:
    """A mission with one waypoint well outside a disc of `radius_m` around home: the bridge has to refuse it on its own."""
    return build_mission(home_lat, home_lon, offset_m=radius_m * 5.0)


def judge(accepted: MissionUploadResult, outside: MissionUploadResult, count: int) -> tuple[bool, str]:
    """(passed, reason): the real mission accepted with every item served, the one outside the envelope refused without sending anything."""
    if not accepted.sent:
        return False, f"the simulator did not accept the mission: {accepted.reason}"
    if accepted.items != count:
        return False, f"the simulator accepted the mission but {accepted.items} of {count} items were served"
    if outside.sent:
        return False, "a mission with a point outside the envelope was uploaded"
    if outside.items != 0:
        return False, f"the mission outside the envelope was refused only after {outside.items} item(s) had been sent"
    return True, f"the simulator accepted {count} items and the mission outside the envelope was refused before anything was sent"


def upload_both(sink: Any, home_lat: float, home_lon: float, radius_m: float) -> tuple[bool, str]:
    """Upload the good mission and the outside one through the bridge's uploader with `sink` (a MavlinkCommandSink with target ids)."""
    envelope = FlightEnvelope(max_altitude_m=120.0, max_radius_m=radius_m, home_lat=home_lat, home_lon=home_lon)
    uploader = MissionUploader(MissionSettings(upload_enabled=True, max_items=20, request_timeout_s=5.0, total_timeout_s=60.0))
    good, why = parse_items(build_mission(home_lat, home_lon))
    if why:
        return False, f"the mission built here is not valid: {why}"
    bad, why = parse_items(build_outside_mission(home_lat, home_lon, radius_m))
    if why:
        return False, f"the mission outside the envelope built here is not valid: {why}"
    accepted = uploader.upload(sink, sink.target_system, sink.target_component, good, envelope)
    outside = uploader.upload(sink, sink.target_system, sink.target_component, bad, envelope)
    return judge(accepted, outside, len(good))


def read_home(connection: Any, timeout_s: float) -> tuple[float, float] | None:
    """The position the vehicle reports (GLOBAL_POSITION_INT, degrees), or None when it sends none in time."""
    message = connection.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=timeout_s)
    if message is None:
        return None
    lat, lon = getattr(message, "lat", None), getattr(message, "lon", None)
    if not isinstance(lat, int) or not isinstance(lon, int) or (lat == 0 and lon == 0):
        return None
    return lat / 1e7, lon / 1e7


def _open(url: str, timeout_s: float) -> Any:
    # the mission protocol with integer coordinates (MISSION_REQUEST_INT) is the MAVLink 2 one: pymavlink speaks MAVLink 1 unless told otherwise
    os.environ.setdefault("MAVLINK20", "1")
    from pymavlink import mavutil  # imported here: the judgement above needs no pymavlink

    connection = mavutil.mavlink_connection(url)
    if connection.wait_heartbeat(timeout=timeout_s) is None:
        raise TimeoutError(f"no heartbeat from the simulator within {timeout_s:g} s")
    return connection


def main(argv: Sequence[str] | None = None, connect: Callable[[str, float], Any] = _open) -> int:
    parser = argparse.ArgumentParser(description="Mission upload check of the bridge against a running autopilot simulator.")
    parser.add_argument("--connection", required=True, help="pymavlink connection URL of the simulator, e.g. udpin:0.0.0.0:14540")
    parser.add_argument("--open-timeout", type=float, default=60.0, help="seconds to wait for the first heartbeat of the simulator (default 60)")
    parser.add_argument("--position-timeout", type=float, default=60.0, help="seconds to wait for the position of the vehicle (default 60)")
    parser.add_argument("--radius", type=float, default=200.0, help="radius of the flight envelope around the position, in metres (default 200)")
    args = parser.parse_args(argv)
    if min(args.open_timeout, args.position_timeout, args.radius) <= 0:
        print("error: --open-timeout, --position-timeout and --radius must be positive", file=sys.stderr)
        return 2
    try:
        from hydra_umc_bridge_uav.mavlink_transport import _PymavlinkConnectionSink

        connection = connect(args.connection, args.open_timeout)
        home = read_home(connection, args.position_timeout)
        if home is None:
            print("SITL_MISSION=FAIL the vehicle reported no position in time")
            return 1
        passed, reason = upload_both(_PymavlinkConnectionSink(connection), home[0], home[1], args.radius)
    except Exception as error:  # noqa: BLE001 - a simulator that is not there is a failed check with a reason, not a traceback
        print(f"SITL_MISSION=FAIL {type(error).__name__}: {error}")
        return 1
    print(f"SITL_MISSION={'PASS' if passed else 'FAIL'} {reason}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
