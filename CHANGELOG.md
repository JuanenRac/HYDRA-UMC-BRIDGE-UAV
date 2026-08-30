<!-- =============================================================================
HYDRA-UMC-BRIDGE-UAV - Change history
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Changelog

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
