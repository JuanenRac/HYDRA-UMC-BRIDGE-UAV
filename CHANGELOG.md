<!-- =============================================================================
HYDRA-UMC-BRIDGE-UAV - Change history
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Changelog

## [0.0.8] - A sent MAVLink command is now confirmed by a real COMMAND_ACK, not just a socket call that didn't throw

`mavlink_transport.py`'s `_send_one()` used to report `sent=True` purely because `command_long_send()`
itself raised no exception - that only proves the bytes left this process, never that the autopilot
received or accepted the command. A dropped packet, a wrong `target_system`, a busy autopilot, or a
real pre-arm/pre-takeoff refusal all used to come back as a false confirmed send. `_send_one()` now
waits for the matching real `COMMAND_ACK` (a configurable timeout, 2 seconds by default) before
reporting success, checks that the ack's own `command` field actually matches what was just sent (a
stale ack for a previous command is never mistaken for this one), and only accepts a real
`MAV_RESULT_ACCEPTED`. `open_mavlink_connection()` now returns a small adapter exposing both the real
`command_long_send()` and `recv_match()` methods a genuine `pymavlink` connection splits across two
different objects - the previous version only ever returned the sending half, so a real `COMMAND_ACK`
could never actually have been read back through it even if the code had tried. 5 new tests cover a
missing ack (timeout), a read failure, a rejected result, and a mismatched/stale ack - the existing 47
tests still pass unchanged against the same fake sink, now answering with a real accepted ack by
default.

## [0.0.7] - A PX4/ArduPilot-shaped MAVLink autopilot emulator, not a record-only sink

Until now the only `MavlinkCommandSink` double here was `FakeMavlinkSink`
- it records `(command, params)` and answers nothing. A real autopilot
answers every `COMMAND_LONG` with a `COMMAND_ACK` carrying a real
`MAV_RESULT`, runs its own pre-arm gate, refuses `NAV_TAKEOFF` while
disarmed, and moves through a real armed/flying/RTL/landed state machine.
New `tests/mavlink_emulator.py` does all of that, against the
authoritative common.xml numeric IDs: `COMMAND_ARM_DISARM` (400) ->
ARMED only if the pre-arm gate (gps_fix, battery, prearm_ok) passes, else
`DENIED` (2) / `TEMPORARILY_REJECTED` (1); `NAV_TAKEOFF` (22) -> climbing
to param7 m, `DENIED` while disarmed; `DO_REPOSITION` (192) /
`NAV_LOITER_UNLIM` (17) -> moving / hovering, `TEMPORARILY_REJECTED` on
the ground; `NAV_RETURN_TO_LAUNCH` (20) / `NAV_LAND` (21) -> returning /
landing with `step()` auto-disarming on touchdown; `IMAGE_START_CAPTURE`
(2000) -> accepted; any other id -> `UNSUPPORTED` (3). `fail_prearm()`,
`set_gps_fix()`, `set_battery()`, `trigger_link_loss_failsafe()` drive
the physical side; `emit_heartbeat()` returns a real `HEARTBEAT` dict
with the `MAV_MODE_FLAG_SAFETY_ARMED` bit and a real `MAV_STATE`
(STANDBY/ACTIVE/CRITICAL) in `system_status`. New
`tests/test_mavlink_emulator.py` runs this bridge's real
`MavlinkFlightControl.send()` end to end against it (11 tests): the full
arm -> takeoff -> reposition -> hover+capture -> RTL -> auto-disarm
lifecycle, a hard pre-arm failure denying the arm, no-GPS temporarily
rejecting it, an in-flight disarm denied, and a link-loss failsafe
switching to RTL and flagging CRITICAL. 47 tests total.

## [0.0.6] - real regressions

Closer review reproduced 2 real regressions (each
with a real fake-sink probe, no vehicle involved). Both fixed here,
each with new regression tests:

- the arm gate used `not confirm_arm`, and Python's
  own truthiness rules made this dangerously permissive - a caller
  passing the STRING `'false'` (non-empty, therefore truthy) made
  `not confirm_arm` evaluate to `False`, arming a real vehicle despite
  never receiving a real `True`. Separately, `bool` is a real subclass
  of `int` in Python, so `takeoff_altitude_m=True`/a boolean waypoint
  coordinate sailed through every finite/range check as if it were the
  real number `1.0`. Fixed: arming now requires `confirm_arm is True`
  (nothing else); every numeric flight parameter (altitude, latitude,
  longitude) is now explicitly checked to be a real `int`/`float` and
  not a `bool` before any finite/range check runs.
- **Shared with HYDRA-UMC-SDK and 4 other bridges:**
  HYDRA-UMC-SDK's own `BridgeJob` constructor now correctly rejects an
  unrecognised `phase` at construction time (its own real fix) -
  this project's own coordinator test used to construct one directly
  with a raw string to prove the coordinator's dispatch-level fallback,
  which the SDK's own hardened constructor no longer allows at all.
  Fixed by updating the test, not the SDK: one test now proves the SDK
  itself refuses construction; a second exercises the coordinator's own
  defensive fallback via an explicit minimal double, never a real
  `BridgeJob` the SDK would refuse to build.
- 5 new regression tests (36 total), each reproducing its own
  exact scenario before the fix and passing after it.

## [0.0.5] - Finite/range validation and a real arm gate

- `TAKEOFF`'s `takeoff_altitude_m` and `GOTO_WAYPOINT`'s
  `waypoint_lat`/`waypoint_lon`/`waypoint_alt_m` reached a real
  `COMMAND_LONG` with zero validation - a `NaN` altitude sailed straight
  through with `sent=True` reported by a fake sink. Fixed: altitude must
  be finite and positive; latitude/longitude are checked against the
  real, universal +/-90/+/-180 geographic range. A maximum operating
  altitude is deliberately NOT enforced - that is a real, jurisdiction/
  deployment-specific profile parameter, not a universal physical
  constant, and stays real, deferred future work rather than an invented
  number.
- The request named
  `PRE_FLIGHT_CHECK` genuinely arms the vehicle
  (`MAV_CMD_COMPONENT_ARM_DISARM`, param1=1) - PX4/ArduPilot run their
  own pre-arm check suite as part of processing an arm request, so there
  is no separate, real, standard MAVLink command that runs those checks
  WITHOUT arming. Naming it "check" made a real arm command sound like
  an inert, read-only query. Renamed to `ARM` throughout (coordinator,
  transport, docs, README x7); `MavlinkFlightControl.send()` now also
  requires an explicit `confirm_arm=True` before actually sending it -
  a real arm command needs its own deliberate opt-in, not just the same
  shared cell/machine gate every other request already passes through.
- 6 tests added (33 total, up from 30 base + 9 subtests) - explicit
  regressions for both findings. `python -m unittest discover -s
  tests`: all passing.
- **`hydra-umc.project.json`** - `maturity` raised from `functional` to
  `established`, matching the real substance already shipped in 0.0.4
  (real, gated coordination logic plus a real MAVLink command sender,
  lazily imported, same rigor and scope as sibling bridges already
  marked `established` - e.g. HYDRA-UMC-BRIDGE-CNC).

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
