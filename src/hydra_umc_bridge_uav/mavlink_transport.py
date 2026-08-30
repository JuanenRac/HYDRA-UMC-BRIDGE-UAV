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

- PRE_FLIGHT_CHECK -> MAV_CMD_COMPONENT_ARM_DISARM (400), param1=1 (arm).
  Arming is the real MAVLink precondition before takeoff - PX4/ArduPilot
  both run their own real internal pre-arm check suite as part of
  processing an arm request, so requesting arm genuinely is the real-world
  equivalent of "run pre-flight checks and get ready", not an approximation.
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

from dataclasses import dataclass
from typing import Protocol

from .coordinator import UavDispatch

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


class MavlinkFlightControl:
    """Send only the real, mapped MAV_CMD for an already-gated UavDispatch."""

    def send(
        self,
        sink: MavlinkCommandSink,
        target_system: int,
        target_component: int,
        dispatch: UavDispatch,
        *,
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

        if dispatch.request == "PRE_FLIGHT_CHECK":
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_COMPONENT_ARM_DISARM, param1=1
            )
        if dispatch.request == "TAKEOFF":
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_NAV_TAKEOFF, param7=takeoff_altitude_m
            )
        if dispatch.request == "GOTO_WAYPOINT":
            if waypoint_lat is None or waypoint_lon is None or waypoint_alt_m is None:
                return MavlinkSendResult(False, "GOTO_WAYPOINT requires waypoint_lat/waypoint_lon/waypoint_alt_m")
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
