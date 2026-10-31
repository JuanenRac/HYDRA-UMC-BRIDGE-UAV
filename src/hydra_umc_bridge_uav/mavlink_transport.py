# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Real MAVLink command transport
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Send an already-gated UavDispatch as a real MAVLink command - never
computes flight control, stabilization or a trajectory itself.

Every request this module can send maps to a real, numbered MAV_CMD from
the MAVLink common message set (researched against the authoritative
common.xml, github.com/mavlink/mavlink/blob/master/message_definitions/
v1.0/common.xml), never an invented or guessed command ID:

- ARM -> MAV_CMD_COMPONENT_ARM_DISARM (400), param1=1 (arm). this
  used to be named PRE_FLIGHT_CHECK, sounding like an inert, read-only query - it
  genuinely arms the vehicle. PX4/ArduPilot both run their own real
  internal pre-arm check suite as part of processing an arm request (so
  there is no separate, real, standard MAVLink command that runs those
  checks WITHOUT arming), but that does not make "arm" a safe name to
  hide behind "check". `send()` below requires an explicit
  `confirm_arm=True` before this one specific request is actually sent -
  "specific authorization for arming", not just the shared cell/machine
  gate every other request already passes through.
- DISARM -> MAV_CMD_COMPONENT_ARM_DISARM (400), param1=0, param2=0 (no
  force): the autopilot refuses it in flight on its own. There is
  deliberately no forced disarm / kill here.
- TAKEOFF -> MAV_CMD_NAV_TAKEOFF (22), param7=target altitude (metres), as a
  COMMAND_LONG. COMMAND_LONG carries no altitude frame, so what that altitude
  is measured from is the autopilot's own choice (PX4 reads it as above mean
  sea level, ArduPilot as relative to home): the takeoff altitude is not
  frame-explicit the way a waypoint is.
- GOTO_WAYPOINT -> MAV_CMD_DO_REPOSITION (192), the real, documented
  command for an immediate GUIDED-mode "go here now" - MAV_CMD_NAV_WAYPOINT
  is a mission-item type for a pre-planned mission, not a real-time
  command; DO_REPOSITION is the correct command for this coordinator's
  own real-time, single-target semantics. It is sent as a COMMAND_INT: the
  position is in degrees * 1e7 (no float32 rounding) and the altitude frame
  is explicit - `altitude_frame="relative"` (the default, metres above the
  takeoff point, MAV_FRAME_GLOBAL_RELATIVE_ALT_INT) or `"amsl"` (metres above
  mean sea level, MAV_FRAME_GLOBAL_INT). The yaw is NaN (keep the current
  heading) rather than 0 (turn north).
- HOVER_AND_CAPTURE -> MAV_CMD_NAV_LOITER_UNLIM (17) followed by
  MAV_CMD_IMAGE_START_CAPTURE (2000, one image) - two real, separate
  commands, sent in that order (hold position, then capture). The capture
  goes to `camera_component` when one is given, else to the target component.
- RETURN_TO_LAUNCH -> MAV_CMD_NAV_RETURN_TO_LAUNCH (20).
- LAND -> MAV_CMD_NAV_LAND (21) - distinct from RTL, see coordinator.py's
  own comment on the LAND constant.

A return to launch, a landing and a disarm are the requests that bring the
vehicle to a safe state: they are sent even when the dispatch carries a
refusal, exactly like the simulated vehicle does.

Real-time stabilization and the actual failsafe execution remain the
flight controller's own independent authority, unchanged. Nothing here has
been run against a real autopilot, PX4 or ArduPilot SITL: the wire format
is checked against the emulator in tests/ only.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .coordinator import UavCoordinator, UavDispatch

# Real, universal geographic bounds - never a per-deployment "profile"
# choice, unlike a maximum operating altitude (jurisdiction/regulatory,
# e.g. FAA Part 107's ~122m AGL cap for small UAS - deliberately NOT
# hardcoded here, since inventing one real number would misrepresent it
# as a universal physical limit the way LATITUDE/LONGITUDE genuinely
# are). A deployment passes its own `FlightEnvelope` instead.
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

# Real MAV_FRAME numeric IDs (common.xml) for the altitude of a COMMAND_INT.
_MAV_FRAME_GLOBAL_INT = 5
_MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6
ALTITUDE_FRAMES = {"relative": _MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, "amsl": _MAV_FRAME_GLOBAL_INT}

# Real MAV_RESULT numeric IDs (common.xml) - only ACCEPTED (0) means the
# autopilot actually ran the command; IN_PROGRESS (5) means it is still
# running it and a final ACK follows; every other value (TEMPORARILY_
# REJECTED=1, DENIED=2, UNSUPPORTED=3, FAILED=4, ...) means it did not.
_MAV_RESULT_ACCEPTED = 0
_MAV_RESULT_IN_PROGRESS = 5

# How long _send_one() waits for a real COMMAND_ACK before giving up.
_DEFAULT_ACK_TIMEOUT_SECONDS = 2.0
# How long open_mavlink_connection() waits for the first HEARTBEAT.
_DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 10.0

_EARTH_RADIUS_M = 6_371_000.0


class MavlinkCommandSink(Protocol):
    """The minimal real interface this module depends on - matches
    pymavlink's own real `MAVLink.command_long_send()`/`command_int_send()`/
    `recv_match()` signatures.

    `recv_match()` matches pymavlink's real `mavutil.mavlink_connection`
    interface (not `MAVLink.command_long_send()`'s own object - a real
    link's send/receive sides are two separate real pymavlink objects, but
    this bridge only ever needs these methods, so one Protocol covers
    both without inventing an extra seam)."""

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

    def command_int_send(
        self,
        target_system: int,
        target_component: int,
        frame: int,
        command: int,
        current: int,
        autocontinue: int,
        param1: float,
        param2: float,
        param3: float,
        param4: float,
        x: int,
        y: int,
        z: float,
    ) -> object: ...

    def recv_match(self, type: str, blocking: bool, timeout: float) -> object | None: ...


class _PymavlinkConnectionSink:
    """Adapts a real `mavutil.mavlink_connection(...)` object onto this
    module's own `MavlinkCommandSink` seam.

    A real pymavlink connection splits sending and receiving across two
    different real objects: `connection.mav.command_long_send(...)` (the
    MAVLink message encoder) sends, while `connection.recv_match(...)`
    (the connection itself) receives. This wrapper exposes the real
    methods on the one object this module's own `MavlinkCommandSink`
    Protocol expects, and the system/component the vehicle's heartbeat came
    from (`target_system` / `target_component`)."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @property
    def target_system(self) -> int:
        return int(self._connection.target_system)

    @property
    def target_component(self) -> int:
        return int(self._connection.target_component)

    def command_long_send(self, *args: float | int, **kwargs: float | int) -> object:
        return self._connection.mav.command_long_send(*args, **kwargs)

    def command_int_send(self, *args: float | int, **kwargs: float | int) -> object:
        return self._connection.mav.command_int_send(*args, **kwargs)

    def recv_match(self, type: str, blocking: bool, timeout: float) -> object | None:
        return self._connection.recv_match(type=type, blocking=blocking, timeout=timeout)

    def heartbeat_send(self, type: int, autopilot: int, base_mode: int, custom_mode: int, system_status: int) -> object:
        """The bridge's own HEARTBEAT as a ground station (see gcs_heartbeat.py)."""
        return self._connection.mav.heartbeat_send(type, autopilot, base_mode, custom_mode, system_status)


SIGNING_KEY_BYTES = 32


def load_signing_key(path: str) -> bytes:
    """Read a MAVLink 2 signing key from a file: 32 raw bytes, or 64 hexadecimal characters (optionally followed by a line end).

    The key is a secret: it is never printed, and the error for a bad file does not repeat any of its content."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(256)
    except OSError as error:
        raise ValueError(f"the MAVLink signing key file cannot be read: {error.strerror or error}") from error
    text = data.strip()
    if len(text) == SIGNING_KEY_BYTES * 2:
        try:
            return bytes.fromhex(text.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            pass
    if len(data) == SIGNING_KEY_BYTES:
        return data
    raise ValueError(f"the MAVLink signing key file must hold {SIGNING_KEY_BYTES} bytes or {SIGNING_KEY_BYTES * 2} hexadecimal characters")


def open_mavlink_connection(
    connection_string: str,
    *,
    heartbeat_timeout_seconds: float = _DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    signing_key: bytes | None = None,
    allow_unsigned: bool = False,
) -> MavlinkCommandSink:
    """Open a real MAVLink link. The only place this module imports pymavlink.

    `connection_string` is pymavlink's own real connection URL, e.g.
    "udp:127.0.0.1:14550" (SITL/companion link) or "/dev/ttyUSB0" (serial
    telemetry radio). Raises RuntimeError with a clear message if pymavlink
    isn't installed, or if no HEARTBEAT arrives within
    `heartbeat_timeout_seconds` (no vehicle on the other end), rather than
    letting an ImportError surface from deep inside this module or waiting
    for ever. The returned object carries the `target_system` /
    `target_component` the heartbeat came from.

    With a `signing_key` (32 bytes, see `load_signing_key`) every message the bridge sends is signed with MAVLink 2
    signing, and the messages received are accepted only when they are signed too, unless `allow_unsigned` is True
    (a vehicle that does not sign yet). A vehicle that does not sign is then refused with the "no MAVLink HEARTBEAT"
    error rather than accepted unsigned. Signing authenticates the link; it does not encrypt it.
    """
    if signing_key is not None and len(signing_key) != SIGNING_KEY_BYTES:
        raise ValueError(f"a MAVLink signing key must be {SIGNING_KEY_BYTES} bytes")

    try:
        from pymavlink import mavutil  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "pymavlink is not installed - install it to send real MAVLink commands "
            "(this module's command-building/gating logic works and is tested without it)"
        ) from error
    connection = mavutil.mavlink_connection(connection_string)
    if signing_key is not None:
        connection.setup_signing(signing_key, sign_outgoing=True, allow_unsigned_callback=(lambda *_args: True) if allow_unsigned else (lambda *_args: False))
    heartbeat = connection.wait_heartbeat(timeout=heartbeat_timeout_seconds)
    if heartbeat is None:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
        raise RuntimeError(
            f"no MAVLink HEARTBEAT from {connection_string} within {heartbeat_timeout_seconds}s - is a vehicle connected?"
        )
    return _PymavlinkConnectionSink(connection)


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
    the real number `1.0`."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive_finite(value: object) -> bool:
    return _is_real_number(value) and math.isfinite(value) and value > 0  # type: ignore[arg-type,operator]


def _distance_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Great-circle distance in metres (haversine)."""
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    d_phi, d_lambda = phi_b - phi_a, math.radians(lon_b - lon_a)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class FlightEnvelope:
    """The volume the deployment allows this bridge to send the vehicle into.

    `max_altitude_m` is metres above the takeoff point and `max_radius_m` is the
    horizontal distance from (`home_lat`, `home_lon`). The numbers are the
    deployment's own (they depend on the site, the vehicle and the
    jurisdiction); none is assumed here. It is a coordination-layer check, not
    a replacement for a geofence loaded into the autopilot itself.
    """

    max_altitude_m: float
    max_radius_m: float
    home_lat: float
    home_lon: float

    def __post_init__(self) -> None:
        if not _positive_finite(self.max_altitude_m) or not _positive_finite(self.max_radius_m):
            raise ValueError("max_altitude_m and max_radius_m must be finite, positive numbers")
        for value, bounds, name in (
            (self.home_lat, _LATITUDE_RANGE_DEG, "home_lat"),
            (self.home_lon, _LONGITUDE_RANGE_DEG, "home_lon"),
        ):
            if not _is_real_number(value) or not math.isfinite(value) or not (bounds[0] <= value <= bounds[1]):
                raise ValueError(f"{name} must be a finite number in [{bounds[0]:g}, {bounds[1]:g}]")

    def refusal_for_altitude(self, altitude_m: float) -> str | None:
        if altitude_m > self.max_altitude_m:
            return f"altitude {altitude_m:g} m is above the flight envelope ceiling of {self.max_altitude_m:g} m"
        return None

    def refusal_for_position(self, lat: float, lon: float) -> str | None:
        distance = _distance_m(self.home_lat, self.home_lon, lat, lon)
        if distance > self.max_radius_m:
            return f"position is {distance:.0f} m from home, beyond the flight envelope radius of {self.max_radius_m:g} m"
        return None


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
        altitude_frame: str = "relative",
        envelope: FlightEnvelope | None = None,
        camera_component: int | None = None,
        ack_timeout_seconds: float = _DEFAULT_ACK_TIMEOUT_SECONDS,
    ) -> MavlinkSendResult:
        # A rejected dispatch (the shared SDK gate already said no) must
        # never reach the network - the transport layer is not a second
        # place to reconsider a safety decision already made. The one
        # exception is a request that brings the vehicle to a safe state
        # (return to launch, land, disarm): refusing it would leave the
        # vehicle where it is, and the simulated vehicle sends it too.
        if not dispatch.accepted and dispatch.request not in UavCoordinator.SAFE_STATE_REQUESTS:
            return MavlinkSendResult(False, dispatch.reason)

        if dispatch.request == "ARM" and confirm_arm is not True:
            # - a real arm command needs its own explicit,
            # deliberate opt-in from the caller, never just the same
            # shared cell/machine gate every other request already passes
            # through. Defaults to refusing: confirm_arm must be True.
            #
            # this
            # used to be `not confirm_arm`, which Python's own truthiness
            # rules make dangerously permissive - a real caller passing
            # the STRING `'false'` (a non-empty string, therefore truthy)
            # made `not confirm_arm` evaluate to `False`, letting a real
            # arm command through despite never receiving a real `True`.
            # `is not True` accepts nothing but the literal boolean.
            return MavlinkSendResult(False, "arming a real vehicle requires confirm_arm=True - refusing by default")

        if dispatch.request == "ARM":
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_COMPONENT_ARM_DISARM,
                ack_timeout_seconds=ack_timeout_seconds, param1=1,
            )
        if dispatch.request == "DISARM":
            # param2=0: not forced - an autopilot refuses a disarm in flight by itself.
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_COMPONENT_ARM_DISARM,
                ack_timeout_seconds=ack_timeout_seconds, param1=0, param2=0,
            )
        if dispatch.request == "TAKEOFF":
            # a non-finite (NaN/+-inf) or non-positive altitude
            # used to sail straight through into a real MAV_CMD_NAV_TAKEOFF
            # param7 with no check at all - a fake sink still reported
            # sent=True for it. A takeoff altitude of 0 or below is
            # physically meaningless for this command's own real purpose.
            if not _positive_finite(takeoff_altitude_m):
                return MavlinkSendResult(False, f"takeoff_altitude_m must be a finite, positive number, got {takeoff_altitude_m!r}")
            if envelope is not None:
                refusal = envelope.refusal_for_altitude(takeoff_altitude_m)
                if refusal is not None:
                    return MavlinkSendResult(False, refusal)
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_NAV_TAKEOFF,
                ack_timeout_seconds=ack_timeout_seconds, param7=takeoff_altitude_m,
            )
        if dispatch.request == "GOTO_WAYPOINT":
            if waypoint_lat is None or waypoint_lon is None or waypoint_alt_m is None:
                return MavlinkSendResult(False, "GOTO_WAYPOINT requires waypoint_lat/waypoint_lon/waypoint_alt_m")
            # - same real gap as TAKEOFF above, for all 3 real
            # coordinates a MAV_CMD_DO_REPOSITION actually carries.
            # Latitude/longitude are checked against real, universal
            # geographic bounds (never a "profile" choice); altitude only
            # against finiteness/positivity - a ceiling is the deployment's
            # `FlightEnvelope`.
            if not _is_real_number(waypoint_lat) or not math.isfinite(waypoint_lat) or not (_LATITUDE_RANGE_DEG[0] <= waypoint_lat <= _LATITUDE_RANGE_DEG[1]):
                return MavlinkSendResult(False, f"waypoint_lat must be a finite number in [-90, 90], got {waypoint_lat!r}")
            if not _is_real_number(waypoint_lon) or not math.isfinite(waypoint_lon) or not (_LONGITUDE_RANGE_DEG[0] <= waypoint_lon <= _LONGITUDE_RANGE_DEG[1]):
                return MavlinkSendResult(False, f"waypoint_lon must be a finite number in [-180, 180], got {waypoint_lon!r}")
            if not _positive_finite(waypoint_alt_m):
                return MavlinkSendResult(False, f"waypoint_alt_m must be a finite, positive number, got {waypoint_alt_m!r}")
            frame = ALTITUDE_FRAMES.get(altitude_frame) if isinstance(altitude_frame, str) else None
            if frame is None:
                return MavlinkSendResult(False, f"altitude_frame must be one of {sorted(ALTITUDE_FRAMES)}, got {altitude_frame!r}")
            if envelope is not None:
                if altitude_frame != "relative":
                    return MavlinkSendResult(False, "a flight envelope can only be checked against a relative altitude")
                refusal = envelope.refusal_for_altitude(waypoint_alt_m) or envelope.refusal_for_position(waypoint_lat, waypoint_lon)
                if refusal is not None:
                    return MavlinkSendResult(False, refusal)
            return self._send_one_int(
                sink,
                target_system,
                target_component,
                frame,
                _MAV_CMD_DO_REPOSITION,
                ack_timeout_seconds=ack_timeout_seconds,
                param1=-1,  # no ground-speed change requested
                param4=math.nan,  # keep the current heading (0 would turn the vehicle north)
                x=round(waypoint_lat * 1e7),
                y=round(waypoint_lon * 1e7),
                z=waypoint_alt_m,
            )
        if dispatch.request == "HOVER_AND_CAPTURE":
            loiter = self._send_one(
                sink, target_system, target_component, _MAV_CMD_NAV_LOITER_UNLIM,
                ack_timeout_seconds=ack_timeout_seconds, param4=math.nan,
            )
            if not loiter.sent:
                return loiter
            # one image: param2 = interval 0, param3 = total images 1 (0 would mean "until stopped")
            capture = self._send_one(
                sink, target_system, target_component if camera_component is None else camera_component,
                _MAV_CMD_IMAGE_START_CAPTURE,
                ack_timeout_seconds=ack_timeout_seconds, param1=0, param2=0, param3=1,
            )
            return MavlinkSendResult(
                capture.sent, capture.reason, (_MAV_CMD_NAV_LOITER_UNLIM, _MAV_CMD_IMAGE_START_CAPTURE)
            )
        if dispatch.request == "RETURN_TO_LAUNCH":
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_NAV_RETURN_TO_LAUNCH,
                ack_timeout_seconds=ack_timeout_seconds,
            )
        if dispatch.request == "LAND":
            return self._send_one(
                sink, target_system, target_component, _MAV_CMD_NAV_LAND,
                ack_timeout_seconds=ack_timeout_seconds,
            )
        return MavlinkSendResult(False, f"no real MAV_CMD mapped for request {dispatch.request!r}")

    @staticmethod
    def _send_one(
        sink: MavlinkCommandSink,
        target_system: int,
        target_component: int,
        command: int,
        *,
        ack_timeout_seconds: float = _DEFAULT_ACK_TIMEOUT_SECONDS,
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
        return MavlinkFlightControl._await_ack(sink, target_system, command, ack_timeout_seconds)

    @staticmethod
    def _send_one_int(
        sink: MavlinkCommandSink,
        target_system: int,
        target_component: int,
        frame: int,
        command: int,
        *,
        ack_timeout_seconds: float = _DEFAULT_ACK_TIMEOUT_SECONDS,
        param1: float = 0,
        param2: float = 0,
        param3: float = 0,
        param4: float = 0,
        x: int = 0,
        y: int = 0,
        z: float = 0,
    ) -> MavlinkSendResult:
        try:
            sink.command_int_send(
                target_system, target_component, frame, command, 0, 0, param1, param2, param3, param4, x, y, z
            )
        except OSError as error:
            return MavlinkSendResult(False, f"MAVLink send failed: {error}", (command,))
        return MavlinkFlightControl._await_ack(sink, target_system, command, ack_timeout_seconds)

    @staticmethod
    def _await_ack(sink: MavlinkCommandSink, target_system: int, command: int, ack_timeout_seconds: float) -> MavlinkSendResult:
        # A sent command is only confirmed once its matching COMMAND_ACK
        # comes back, carrying a real MAV_RESULT. The link is shared (a
        # ground station, a camera, a late ACK of an earlier command), so an
        # ACK for another command or from another system is skipped and the
        # wait goes on until the deadline; IN_PROGRESS means the autopilot
        # is still running the command and the final ACK is still to come.
        deadline = time.monotonic() + ack_timeout_seconds
        stale_command: object = None
        saw_stale = False
        in_progress = False
        while True:
            try:
                ack = sink.recv_match(type="COMMAND_ACK", blocking=True, timeout=max(0.0, deadline - time.monotonic()))
            except OSError as error:
                return MavlinkSendResult(False, f"MAVLink COMMAND_ACK read failed: {error}", (command,))
            if ack is None:
                break
            source_system = getattr(ack, "get_srcSystem", None)
            if callable(source_system) and source_system() != target_system:
                # an ACK from some other system on the same link; it must not extend the wait past the deadline
                if time.monotonic() >= deadline:
                    break
                continue
            # A real COMMAND_ACK is a pymavlink message object with real
            # `.command`/`.result` attributes (not a dict) - `getattr()` here
            # also tolerates a test double that returns something shaped
            # differently, failing closed instead of raising AttributeError.
            ack_command = getattr(ack, "command", None)
            ack_result = getattr(ack, "result", None)
            if ack_command != command:
                saw_stale, stale_command = True, ack_command
            elif ack_result == _MAV_RESULT_IN_PROGRESS:
                in_progress = True
            elif ack_result != _MAV_RESULT_ACCEPTED:
                return MavlinkSendResult(
                    False, f"autopilot did not accept command {command} (MAV_RESULT={ack_result!r})", (command,)
                )
            else:
                return MavlinkSendResult(True, "sent and acknowledged by the autopilot", (command,))
            if time.monotonic() >= deadline:
                break
        if in_progress:
            return MavlinkSendResult(
                False,
                f"command {command} is in progress but no final COMMAND_ACK arrived within {ack_timeout_seconds}s",
                (command,),
            )
        if saw_stale:
            return MavlinkSendResult(
                False,
                f"COMMAND_ACK was for command {stale_command!r}, not the sent command {command} - stale or mismatched ack",
                (command,),
            )
        return MavlinkSendResult(
            False,
            f"no COMMAND_ACK received within {ack_timeout_seconds}s - the autopilot may not have executed command {command}",
            (command,),
        )
