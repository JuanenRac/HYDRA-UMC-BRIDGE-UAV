<!-- =============================================================================
HYDRA-UMC-BRIDGE-UAV - Contribution guide
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Contributing

Keep this bridge a coordination layer: it sends only high-level flight
requests and reads back telemetry/heartbeat - real-time flight control,
stabilization and the actual RTL/Hover failsafe trigger remain the flight
controller's own authority (Pixhawk/PX4 MAVLink, or DJI OSDK), never this
repository. `HeartbeatMonitor`'s own failsafe transition is a
coordination-layer signal for this bridge's own state, not a replacement for
the flight controller's independent link-loss failsafe.

Before opening a change, run `build-test.bat` on Windows or `bash build-test.sh`
on Linux. Add a focused test for each state mapping, admission rule or
heartbeat-timeout transition changed. Hardware-dependent behavior must state
its tested flight controller/SDK, transport and safe failure mode;
unverified hardware support must not be presented as ready.
