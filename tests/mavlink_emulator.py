# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Realistic MAVLink flight-controller emulator (fixture)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A PX4/ArduPilot-shaped flight controller on the exact ``MavlinkCommandSink``
seam (`command_long_send(...)`) this bridge's real ``mavlink_transport.py``
talks to.

Until now the only sink double here was ``FakeMavlinkSink`` - it records
`(command, params)` tuples and answers nothing. A real autopilot answers
every ``COMMAND_LONG`` with a ``COMMAND_ACK`` carrying a real ``MAV_RESULT``,
runs its own pre-arm gate before accepting ``MAV_CMD_COMPONENT_ARM_DISARM``,
refuses ``MAV_CMD_NAV_TAKEOFF`` while disarmed, and moves through a real
armed/flying/RTL/landed state machine.

This emulator does all of that, against the authoritative common.xml
numeric IDs (github.com/mavlink/mavlink common message set):

  * ``COMMAND_ARM_DISARM`` (400) param1=1 -> ARMED, but only if the pre-arm
    gate (`gps_fix`, battery, `prearm_ok`) passes - otherwise
    ``MAV_RESULT_DENIED`` (2) or ``TEMPORARILY_REJECTED`` (1), exactly what
    a real autopilot returns for a failed pre-arm check.
  * ``NAV_TAKEOFF`` (22) -> climbing to param7 metres, but ``DENIED`` while
    disarmed.
  * ``DO_REPOSITION`` (192) / ``NAV_LOITER_UNLIM`` (17) -> moving / hovering,
    ``TEMPORARILY_REJECTED`` while still on the ground.
  * ``NAV_RETURN_TO_LAUNCH`` (20) / ``NAV_LAND`` (21) -> returning / landing;
    ``step()`` finishes the descent and auto-disarms on the ground.
  * ``IMAGE_START_CAPTURE`` (2000) -> accepted (payload command).
  * any other command id -> ``MAV_RESULT_UNSUPPORTED`` (3).

`fail_prearm()`, `set_gps_fix()`, `set_battery()` and
`trigger_link_loss_failsafe()` drive the physical side; `emit_heartbeat()`
returns a real ``HEARTBEAT`` dict with the ``MAV_MODE_FLAG_SAFETY_ARMED``
bit and a real ``MAV_STATE`` in ``system_status``.
"""

from __future__ import annotations

# --- authoritative common.xml numeric IDs --------------------------------
_CMD_NAV_LOITER_UNLIM = 17
_CMD_NAV_RETURN_TO_LAUNCH = 20
_CMD_NAV_LAND = 21
_CMD_NAV_TAKEOFF = 22
_CMD_DO_REPOSITION = 192
_CMD_COMPONENT_ARM_DISARM = 400
_CMD_IMAGE_START_CAPTURE = 2000

# MAV_RESULT
RESULT_ACCEPTED = 0
RESULT_TEMPORARILY_REJECTED = 1
RESULT_DENIED = 2
RESULT_UNSUPPORTED = 3
RESULT_FAILED = 4

# MAV_STATE
STATE_STANDBY = 3
STATE_ACTIVE = 4
STATE_CRITICAL = 5

# MAV_MODE_FLAG
MODE_FLAG_CUSTOM_MODE_ENABLED = 1
MODE_FLAG_SAFETY_ARMED = 128

_MIN_ARM_BATTERY_PCT = 25.0


class MavlinkFlightControllerEmulator:
    """A ``MavlinkCommandSink`` PX4-shaped autopilot. Single link, single
    vehicle - not thread-safe, exactly like a real serial/UDP MAVLink link."""

    def __init__(self, system_id: int = 1, component_id: int = 1) -> None:
        self.system_id = system_id
        self.component_id = component_id
        self.armed = False
        self.flight_phase = "ON_GROUND"  # ON_GROUND | CLIMBING | FLYING | HOVERING | RETURNING | LANDING
        self.altitude_m = 0.0
        self.target_altitude_m = 0.0
        self.gps_fix = True
        self.battery_pct = 92.0
        self.prearm_ok = True
        self.failsafe_active = False
        self.acks: list[dict] = []
        self.commands: list[tuple[int, tuple[float, ...]]] = []

    # ---- physical-side drivers ----------------------------------------
    def fail_prearm(self, ok: bool = False) -> None:
        self.prearm_ok = ok

    def set_gps_fix(self, has_fix: bool) -> None:
        self.gps_fix = has_fix

    def set_battery(self, pct: float) -> None:
        self.battery_pct = pct

    def trigger_link_loss_failsafe(self) -> None:
        """Autopilot's own firmware-level failsafe: switch to RTL, flag CRITICAL."""
        self.failsafe_active = True
        if self.armed:
            self.flight_phase = "RETURNING"

    def step(self) -> None:
        """Advance the flight one tick."""
        if self.flight_phase == "CLIMBING":
            self.altitude_m = self.target_altitude_m
            self.flight_phase = "FLYING"
        elif self.flight_phase in ("RETURNING", "LANDING"):
            self.altitude_m = 0.0
            self.flight_phase = "ON_GROUND"
            self.armed = False  # real autopilots auto-disarm after a confirmed ground landing

    # ---- MavlinkCommandSink ------------------------------------------------
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
    ) -> object:
        params = (param1, param2, param3, param4, param5, param6, param7)
        self.commands.append((command, params))
        result = self._process(command, params)
        ack = {"command": command, "result": result}
        self.acks.append(ack)
        return ack  # a real link returns nothing here; the ACK arrives as its own message

    @property
    def last_ack(self) -> dict:
        return self.acks[-1]

    def _process(self, command: int, params: tuple[float, ...]) -> int:
        if command == _CMD_COMPONENT_ARM_DISARM:
            arming = params[0] == 1
            if not arming:
                if self.flight_phase != "ON_GROUND":
                    return RESULT_DENIED  # a real autopilot refuses to disarm in flight
                self.armed = False
                return RESULT_ACCEPTED
            if self.armed:
                return RESULT_ACCEPTED  # idempotent
            if not self.gps_fix or self.battery_pct < _MIN_ARM_BATTERY_PCT:
                return RESULT_TEMPORARILY_REJECTED  # a transient pre-arm condition
            if not self.prearm_ok:
                return RESULT_DENIED  # a hard pre-arm check failure
            self.armed = True
            return RESULT_ACCEPTED

        if command == _CMD_NAV_TAKEOFF:
            if not self.armed:
                return RESULT_DENIED
            if self.flight_phase != "ON_GROUND":
                return RESULT_TEMPORARILY_REJECTED
            self.target_altitude_m = params[6] or 10.0
            self.flight_phase = "CLIMBING"
            return RESULT_ACCEPTED

        if command == _CMD_DO_REPOSITION:
            if self.flight_phase not in ("FLYING", "HOVERING"):
                return RESULT_TEMPORARILY_REJECTED
            self.flight_phase = "FLYING"
            return RESULT_ACCEPTED

        if command == _CMD_NAV_LOITER_UNLIM:
            if self.flight_phase not in ("FLYING", "HOVERING"):
                return RESULT_TEMPORARILY_REJECTED
            self.flight_phase = "HOVERING"
            return RESULT_ACCEPTED

        if command == _CMD_IMAGE_START_CAPTURE:
            return RESULT_ACCEPTED

        if command == _CMD_NAV_RETURN_TO_LAUNCH:
            if not self.armed:
                return RESULT_DENIED
            self.flight_phase = "RETURNING"
            return RESULT_ACCEPTED

        if command == _CMD_NAV_LAND:
            if self.flight_phase == "ON_GROUND":
                return RESULT_TEMPORARILY_REJECTED
            self.flight_phase = "LANDING"
            return RESULT_ACCEPTED

        return RESULT_UNSUPPORTED

    # ---- HEARTBEAT ---------------------------------------------------
    def emit_heartbeat(self) -> dict:
        base_mode = MODE_FLAG_CUSTOM_MODE_ENABLED
        if self.armed:
            base_mode |= MODE_FLAG_SAFETY_ARMED
        if self.failsafe_active:
            system_status = STATE_CRITICAL
        elif self.armed and self.flight_phase != "ON_GROUND":
            system_status = STATE_ACTIVE
        else:
            system_status = STATE_STANDBY
        return {
            "type": 2,           # MAV_TYPE_QUADROTOR
            "autopilot": 12,     # MAV_AUTOPILOT_PX4
            "base_mode": base_mode,
            "custom_mode": 0,
            "system_status": system_status,
            "mavlink_version": 3,
        }
