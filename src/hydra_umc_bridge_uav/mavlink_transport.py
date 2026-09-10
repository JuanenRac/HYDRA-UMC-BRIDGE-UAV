# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Real MAVLink command transport
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Send an already-gated UavDispatch as a real MAVLink COMMAND_LONG - never
computes flight control, stabilization or a trajectory itself.

Every request this module can send maps to a real, numbered MAV_CMD from
the MAVLink common message set (researched against the authoritative
common.xml, github.com/mavlink/mavlink/blob/master/message_definitions/
v1.0/common.xml), never an invented or guessed command ID:

- ARM -> MAV_CMD_COMPONENT_ARM_DISARM (400), param1=1 (arm). UAV-02: this
  used to be named PRE_FLIGHT_CHECK, sounding like an inert, read-only query - it
  genuinely arms the vehicle. PX4/ArduPilot both run their own real
  internal pre-arm check suite as part of processing an arm request (so
  there is no separate, real, standard MAVLink command that runs those
  checks WITHOUT arming), but that does not make "arm" a safe name to
  hide behind "check". `send()` below requires an explicit
  `confirm_arm=True` before this one specific request is actually sent -
  "specific authorization for arming", not just the shared cell/machine
  gate every other request already passes through.
- TAKEOFF -> MAV_CMD_NAV_TAKEOFF (22), param7=target altitude (metres).
- GOTO_WAYPOINT -> MAV_CMD_DO_REPOSITION (192), the real, documented
  command for an immediate GUIDED-mode "go here now" - MAV_CMD_NAV_WAYPOINT
  is a mission-item type for a pre-planned mission, not a real-time
  COMMAND_LONG; DO_REPOSITION is the correct command for this coordinator's
  own real-time, single-target semantics.
- HOVER_AND_CAPTURE -> MAV_CMD_NAV_LOITER_UNLIM (17) followed by
  MAV_CMD_IMAGE_START_CAPTURE (2000) - two real, separate commands, sent in
  that order (hold position, then capture).
- RETURN_TO_LAUNCH -> MAV_CMD_NAV_RETURN_TO_LAUNCH (20).
- LAND -> MAV_CMD_NAV_LAND (21) - distinct from RTL, see coordinator.py's
  own comment on the LAND constant.

Real-time stabilization and the actual failsafe execution remain the
flight controller's own independent authority, unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .coordinator import UavDispatch

# Real, universal geographic bounds - never a per-deployment "profile"
# choice, unlike a maximum operating altitude (jurisdiction/regulatory,
# e.g. FAA Part 107's ~122m AGL cap for small UAS - deliberately NOT
# hardcoded here, since inventing one real number would misrepresent it
# as a universal physical limit the way LATITUDE/LONGITUDE genuinely
# are). A maximum-altitude profile parameter is real, deferred future
# work (see MEJ-11), not silently skipped.
_LATITUDE_RANGE_DEG = (-90.0, 90.0)
_LONGITUDE_RANGE_DEG = (-180.0, 180.0)

# Real MAV_CMD numeric IDs - see this module's own docstring for the source.
_MAV_CMD_NAV_LOITER_UNLIM = 17
_MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
_MAV_CMD_NAV_LAND = 21
_MAV_CMD_NAV_TAKEOFF = 22
_MAV_CMD_DO_REPOSITION = 192
_MAV_CMD_COMPONENT_ARM_DISARM = 400
_MAV_CMD_IMAGE_START_CAPTURE = 2000


class MavlinkCommandSink(Protocol):
    """The minimal real interface this module depends on - matches
    pymavlink's own real `MAVLink.command_long_send()` signature."""

    def command_long_send(
        self,
        target_system: int,
        target_component: int,
        command: int,
        confirmation: int,
        param1: float,
        param2: float,
        param3: float,
        param4: float,
        param5: float,
        param6: float,
        param7: float,
    ) -> object: ...


def open_mavlink_connection(connection_string: str) -> MavlinkCommandSink:
    """Open a real MAVLink link. The only place this module imports pymavlink.

    `connection_string` is pymavlink's own real connection URL, e.g.
    "udp:127.0.0.1:14550" (SITL/companion link) or "/dev/ttyUSB0" (serial
    telemetry radio). Raises RuntimeError with a clear message if pymavlink
    isn't installed, rather than letting an ImportError surface from deep
    inside this module.
    """

    try:
        from pymavlink import mavutil  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "pymavlink is not installed - install it to send real MAVLink commands "
            "(this module's command-building/gating logic works and is tested without it)"
        ) from error
    connection = mavutil.mavlink_connection(connection_string)
    connection.wait_heartbeat()
    return connection.mav


@dataclass(frozen=True)
class MavlinkSendResult:
    sent: bool
    reason: str
    commands: tuple[int, ...] = ()


def _is_real_number(value: object) -> bool:
    """True only for a genuine `int`/`float` - `bool` is a real Python
    subclass of `int`, so `isinstance(True, (int, float))` is also `True`;
    without excluding it explicitly, a caller passing `takeoff_altitude_m=
    True` sails through every finiteness/range check below as if it were
    the real number `1.0` (REV-007)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class MavlinkFlightControl:
    """Send only the real, mapped MAV_CMD for an already-gated UavDispatch."""

    def send(
        self,
        sink: MavlinkCommandSink,
        target_system: int,
        target_component: int,
        dispatch: UavDispatch,
        *,
        confirm_arm: bool = False,
        takeoff_altitude_m: float = 10.0,
        waypoint_lat: float | None = None,
        waypoint_lon: float | None = None,
        waypoint_alt_m: float | None = None,
    ) -> MavlinkSendResult:
        # A rejected dispatch (the shared SDK gate already said no) must
        # never reach the network - the transport layer is not a second
        # place to reconsider a safety decision already made.
        if not dispatch.accepted:
            return MavlinkSendResult(False, dispatch.reason)

        if dispatch.request == "ARM" and confirm_arm is not True:
            # UAV-02 - a real arm command needs its own explicit,
            # deliberate opt-in from the caller, never just the same
            # shared cell/machine gate every other request already passes
            # through. Defaults to refusing: confirm_arm must be True.
            #
            # REV-007: this
            # used to be `not confirm_arm`, which Python's own truthiness
            # rules make dangerously permissive - a real caller passing
            # the STRING `'false'` (a non-empty string, therefore truthy)
            # made `not confirm_arm` evaluate to `False`, letting a real
            # arm command through despite never receiving a real `True`.
            # `is not True` accepts nothing but the literal boolean.
            return MavlinkSendResult(False, "arming a real vehicle requires confirm_arm=True - refusing by default")

        if dispatch.request == "ARM":
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_COMPONENT_ARM_DISARM, param1=1
            )
        if dispatch.request == "TAKEOFF":
            # UAV-01: a non-finite (NaN/+-inf) or non-positive altitude
            # used to sail straight through into a real MAV_CMD_NAV_TAKEOFF
            # param7 with no check at all - a fake sink still reported
            # sent=True for it. A takeoff altitude of 0 or below is
            # physically meaningless for this command's own real purpose.
            if not _is_real_number(takeoff_altitude_m) or not math.isfinite(takeoff_altitude_m) or takeoff_altitude_m <= 0:
                return MavlinkSendResult(False, f"takeoff_altitude_m must be a finite, positive number, got {takeoff_altitude_m!r}")
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_NAV_TAKEOFF, param7=takeoff_altitude_m
            )
        if dispatch.request == "GOTO_WAYPOINT":
            if waypoint_lat is None or waypoint_lon is None or waypoint_alt_m is None:
                return MavlinkSendResult(False, "GOTO_WAYPOINT requires waypoint_lat/waypoint_lon/waypoint_alt_m")
            # UAV-01 - same real gap as TAKEOFF above, for all 3 real
            # coordinates a MAV_CMD_DO_REPOSITION actually carries.
            # Latitude/longitude are checked against real, universal
            # geographic bounds (never a "profile" choice); altitude only
            # against finiteness/positivity - see this module's own
            # _LATITUDE_RANGE_DEG/_LONGITUDE_RANGE_DEG comment for why a
            # maximum altitude is deliberately NOT enforced here.
            if not _is_real_number(waypoint_lat) or not math.isfinite(waypoint_lat) or not (_LATITUDE_RANGE_DEG[0] <= waypoint_lat <= _LATITUDE_RANGE_DEG[1]):
                return MavlinkSendResult(False, f"waypoint_lat must be a finite number in [-90, 90], got {waypoint_lat!r}")
            if not _is_real_number(waypoint_lon) or not math.isfinite(waypoint_lon) or not (_LONGITUDE_RANGE_DEG[0] <= waypoint_lon <= _LONGITUDE_RANGE_DEG[1]):
                return MavlinkSendResult(False, f"waypoint_lon must be a finite number in [-180, 180], got {waypoint_lon!r}")
            if not _is_real_number(waypoint_alt_m) or not math.isfinite(waypoint_alt_m) or waypoint_alt_m <= 0:
                return MavlinkSendResult(False, f"waypoint_alt_m must be a finite, positive number, got {waypoint_alt_m!r}")
            return self._send_one(
                sink,
                target_system,
                target_component,
                _MAV_CMD_DO_REPOSITION,
                param1=-1,  # no ground-speed change requested
                param5=waypoint_lat,
                param6=waypoint_lon,
                param7=waypoint_alt_m,
            )
        if dispatch.request == "HOVER_AND_CAPTURE":
            loiter = self._send_one(sink, target_system, target_component, _MAV_CMD_NAV_LOITER_UNLIM)
            if not loiter.sent:
                return loiter
            capture = self._send_one(
                sink, target_system, target_component, _MAV_CMD_IMAGE_START_CAPTURE, param1=0
            )
            return MavlinkSendResult(
                capture.sent, capture.reason, (_MAV_CMD_NAV_LOITER_UNLIM, _MAV_CMD_IMAGE_START_CAPTURE)
            )
        if dispatch.request == "RETURN_TO_LAUNCH":
            return self._send_one(sink, target_system, target_component, _MAV_CMD_NAV_RETURN_TO_LAUNCH)
        if dispatch.request == "LAND":
            return self._send_one(sink, target_system, target_component, _MAV_CMD_NAV_LAND)
        return MavlinkSendResult(False, f"no real MAV_CMD mapped for request {dispatch.request!r}")

    @staticmethod
    def _send_one(
        sink: MavlinkCommandSink,
        target_system: int,
        target_component: int,
        command: int,
        *,
        param1: float = 0,
        param2: float = 0,
        param3: float = 0,
        param4: float = 0,
        param5: float = 0,
        param6: float = 0,
        param7: float = 0,
    ) -> MavlinkSendResult:
        try:
            sink.command_long_send(
                target_system, target_component, command, 0, param1, param2, param3, param4, param5, param6, param7
            )
        except OSError as error:
            return MavlinkSendResult(False, f"MAVLink send failed: {error}", (command,))
        return MavlinkSendResult(True, "sent", (command,))
