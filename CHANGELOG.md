<!-- =============================================================================
HYDRA-UMC-BRIDGE-UAV - Change history
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Changelog

## [0.0.4] - Real MAVLink command transport (pre-real: connected, not simulated)

- **`mavlink_transport.py`** (new) - this bridge's first real transport:
  `MavlinkFlightControl.send()` sends an already-gated `UavDispatch` as a
  real MAVLink `COMMAND_LONG`, mapped to a real, numbered `MAV_CMD` from the
  authoritative spec
  ([common.xml](https://github.com/mavlink/mavlink/blob/master/message_definitions/v1.0/common.xml)),
  never an invented or guessed ID: `PRE_FLIGHT_CHECK` ->
  `MAV_CMD_COMPONENT_ARM_DISARM` (400, arm - PX4/ArduPilot both run their
  own real pre-arm checks as part of processing an arm request);
  `TAKEOFF` -> `MAV_CMD_NAV_TAKEOFF` (22); `GOTO_WAYPOINT` ->
  `MAV_CMD_DO_REPOSITION` (192, the real command for an immediate
  GUIDED-mode "go here now" - not the mission-item `MAV_CMD_NAV_WAYPOINT`);
  `HOVER_AND_CAPTURE` -> `MAV_CMD_NAV_LOITER_UNLIM` (17) followed by
  `MAV_CMD_IMAGE_START_CAPTURE` (2000); `RETURN_TO_LAUNCH` ->
  `MAV_CMD_NAV_RETURN_TO_LAUNCH` (20); `LAND` -> `MAV_CMD_NAV_LAND` (21).
  Only an already-gated dispatch is ever sent - a rejected `UavDispatch`
  never reaches the network. `open_mavlink_connection()` is the one place
  `pymavlink` (new optional `[mavlink]` extra) is imported, lazily,
  degrading to a clear `RuntimeError` instead of a bare `ImportError` when
  it isn't installed.
- 11 new regression tests against an in-memory fake command sink (no real
  flight controller/SITL needed) - 30/30 tests passing.

## [0.0.3] - Real, standalone LAND request (MAV_CMD_NAV_LAND)

- **`coordinator.py`** - added `LAND`, a real, genuinely distinct flight
  request this coordinator never modeled at all before. Researched
  against the
  [official MAVLink common message set](https://mavlink.io/en/messages/common.html):
  `MAV_CMD_NAV_LAND` (land in place) is a real, separate command from
  `MAV_CMD_NAV_RETURN_TO_LAUNCH` (fly home, then land) - the only
  descent this bridge previously exposed. A real operational gap RTL
  alone can't cover: low battery over unlandable terrain between here
  and home, or a lost/unsafe RTL path, both call for landing now rather
  than attempting the flight home.
- `emergency_land_request()` (new) exposes it as a standalone request,
  deliberately outside the `JobPhase`-driven `dispatch()` flow - the
  same reasoning `HeartbeatMonitor` already applies to its own separate
  link-loss signal: which descent is safe is an operator/failsafe
  policy decision, not something a job phase encodes. Always accepted,
  same as `ABORT`/`RETURN_TO_LAUNCH`.
- `request_plan()`'s static schema bumped `1.0` -> `1.1` (now includes
  `LAND`, reachable through no `JobPhase`).
- 2 new regression tests - 19/19 tests (9 subtests) passing.

## [0.0.2] - Reject non-finite heartbeat deadlines and timestamps

- **`HeartbeatMonitor`** - `timeout_s <= 0` alone never caught `NaN`
  (`nan <= 0` is always `False` in Python) - a `NaN` timeout previously
  passed the old check silently, then made every later `elapsed >
  timeout_s` comparison in `state()` also always `False`, which could
  leave a genuinely lost link reported as healthy. `timeout_s`, and the
  `now` passed to `observe()`/`state()`, must now be finite.
- 17/17 tests passing.

## [0.0.1]

- Added a dependency-free UAV flight-request coordination core
  (`UavCoordinator`): a real, named vocabulary (`PRE_FLIGHT_CHECK`/
  `TAKEOFF`/`GOTO_WAYPOINT`/`HOVER_AND_CAPTURE`/`RETURN_TO_LAUNCH`)
  gated through the shared `HYDRA-UMC-SDK` safety contract.
- Added the required link-loss watchdog: `HeartbeatMonitor`, a real,
  deterministic (explicit `now`, no real sleeps) failsafe state machine
  with its own real timeout-boundary tests - never-observed, fresh,
  exactly-at-timeout, one instant past timeout, and recovery after a
  later heartbeat.
- Added non-mutating build-test scripts and CI SDK checkout, matching
  the rest of the External Automation / Mobile Bridges family.
- Standardized README in all 7 ecosystem languages (English, Spanish,
  French, Italian, German, Simplified Chinese, Japanese), project banner
  and manifest to match the ecosystem's established-project structure.
- No real MAVLink/DJI OSDK transport adapter or physical UAV validated
  yet - this is a plan-only coordination boundary.
