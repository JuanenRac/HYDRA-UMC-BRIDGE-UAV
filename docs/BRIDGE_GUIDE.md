<!-- =============================================================================
HYDRA-UMC-BRIDGE-UAV - Technical bridge guide
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-UAV Technical Guide

## Scope and operating model

This bridge maps a validated `BridgeJob` to a **static high-level flight-request plan**, and separately tracks a real link-loss heartbeat. `UavCoordinator` has no MAVLink or vendor SDK dependency, so it can be verified on Windows, Linux or CI without a real UAV. It emits only a named flight request - `PRE_FLIGHT_CHECK`, `TAKEOFF`, `GOTO_WAYPOINT`, `HOVER_AND_CAPTURE` or `RETURN_TO_LAUNCH` - never a real attitude/throttle command.

`PREPARE`/`LOAD`/`PROCESS`/`UNLOAD`/`COMPLETE` map to their own request; `ABORT` and `COMPLETE` both resolve to `RETURN_TO_LAUNCH` - a normal mission end and an emergency abort both mean "come home", the real standard UAV failsafe action. An unknown SDK phase is rejected. The result is always `plan-only`, never a live flight command.

## The heartbeat watchdog is not optional

`HeartbeatMonitor` (`heartbeat.py`) is this project's own required safety piece, not an afterthought: a UAV bridge that cannot tell "recently confirmed reachable" from "silently gone" is unsafe by construction. It is explicit-`now`-driven (never reads a real clock internally), reports `LOST` from the very first check if no heartbeat was ever observed, and treats exactly-at-the-timeout as still `OK` (only genuinely exceeding it trips the failsafe) - see `tests/test_heartbeat.py` for the full, deterministic boundary suite. Its `failsafe_action` (default `RETURN_TO_LAUNCH`, configurable to a hover policy) is a real signal for THIS bridge's own coordination layer - it is not, and must never be presented as, a replacement for the flight controller's own independent, firmware-level link-loss failsafe.

## Compatible platforms

The planned flight-request boundary is for UAV platforms reachable through a documented high-level interface: a Pixhawk/PX4-class flight controller over MAVLink, or a DJI OSDK/Mobile SDK-class vendor interface. Compatibility means adapting that platform's own real command/telemetry interface through a separately deployed transport adapter after one is selected and tested; it does **not** mean this repository flies a UAV today.

## Scripts and verification

| Script | Purpose | Changes version/CHANGELOG? |
|---|---|---|
| `build-test.bat` / `build-test.sh` | Compile Python and run local tests | No |
| `build.bat` / `build.sh` | Run the same validation, then increment the project version | Yes, after success |

Set `HYDRA_UMC_SDK_ROOT` when the SDK is not a sibling checkout. Use `build-test` during development; it is the only safe default before a real MAVLink/OSDK adapter exists.

## Adding a new script

Keep a new script in the repository root only when it is an operator entry point. Add the standard copyright header, state whether it mutates version/CHANGELOG, print numbered steps, and end `.bat` scripts with `pause`. Put reusable Python logic under `tools/`, compile it in `tools/build_test.py`, add deterministic tests for every new state mapping or failsafe transition, and document the command in the README and this guide. A script must not open a real transport, arm a UAV or send a flight command implicitly.

## Hardware acceptance gate

Before deploying an adapter: select the real flight controller/SDK and its authentication, document every flight request's real MAVLink message or OSDK call, bind authenticated UAV identity, verify the transport adapter genuinely calls `HeartbeatMonitor.observe()` on every real telemetry packet (not a fixed timer), independently confirm the flight controller's own RTL/Hover failsafe actually fires on a real link cut, and perform a tethered/geofenced bench test before any free flight. The flight controller's own firmware remains responsible for real-time stabilization and its own independent failsafe.
