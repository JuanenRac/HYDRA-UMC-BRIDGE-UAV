# Changelog

## [0.2.2] - Mission upload fixes and a coverage floor

- **README: the platform statement:** a note says that where the README speaks of the CM5 it means the pilot bench that runs today, and that the target platform of HYDRA-UMC, an NVIDIA Jetson Orin NX 16 GB, is a design that has not been built or tested.
- **The tests have a coverage floor of 94 %:** `tools/coverage_floor.json` holds the measured line and branch coverage of the bridge, rounded down (94.5 % measured in the CI environment), and `python tools/hydra_project_tool.py coverage` warns when it falls below it (a warning, not a CI check). Tooling only: no behaviour changes.
- **The address test compares the parsed address:** the datalake sender test checks the scheme, host and port of the accepted address with `urllib.parse` instead of a substring of the text.
- **The mission upload also serves the older request of the vehicle:** an autopilot that has not yet seen an integer mission item (PX4) asks for the next item with `MISSION_REQUEST` instead of `MISSION_REQUEST_INT`; the upload ignored it and ended with "the vehicle did not ask for the next item in time". Both requests are served the same way now (the item always goes out as an integer item).
- **The mission upload waits for the next request and for the acknowledgement in one wait:** it used to look for the acknowledgement first and for the next request afterwards, and a real MAVLink link keeps only the kind of message it is asked for and drops the rest, so a request that arrived while the acknowledgement was being looked for was lost and the upload ended with "the vehicle did not ask for the next item in time" against a simulator. The emulator of the autopilot in the tests now drops the other kinds the same way, and a test keeps both answers from being lost.

- **A heartbeat that does not name a component no longer sends the mission and command messages to component 0:** pymavlink leaves the target component at 0 (all components) when the heartbeat it learned the vehicle from did not name one, and the simulator ignored the mission count sent that way. The link now addresses the first autopilot (component 1) in that case and keeps any component the heartbeat did name.

- **The simulator job also uploads a mission:** `tools/sitl_mission.py` reads the position the simulator reports, builds a takeoff, two waypoints and a landing around it inside a flight envelope and sends it with the bridge's own mission uploader; the check passes only when the simulator accepts every item, and a second mission with a point far outside the envelope must be refused before a single item is sent. Nothing is armed and the mission is never started. The optional workflow `sitl.yml` runs it after the link check; the flow is tested here against the autopilot emulator (20 tests).

All notable changes to this project are documented here.

## [Unreleased]

- **The shared tools and the CI jobs have time limits:** the project tool, the validators and the build test pass a timeout to every child process (`HYDRA_UMC_GIT_TIMEOUT_S`, 120 s by default, and `HYDRA_UMC_COMMAND_TIMEOUT_S`, 3600 s by default; a value that is not a positive number means the default), and the baseline and notify jobs of the workflows carry `timeout-minutes`, so a stuck step ends with a message instead of holding a runner.
- **The flight-policy switches can be set from Server Admin, in the tightening direction only:** `bridges.uav.envelopeRequired` and `confirmationRequired` can require the flight envelope and the human confirmation, and `chainInFlight` and `uploadGeofence` can switch chaining in flight and the geofence upload off; a value that would relax the host's own file is ignored. They are read from the server configuration file under the same host switch as the mission upload (`HYDRA_UMC_BRIDGE_UAV_REMOTE_SETTINGS=1`), a damaged file takes the stricter side of every policy, and `run` now applies them to the job entry. 5 tests.
- **Flight requests need the flight envelope, on by default:** the job entry refuses a request to arm, take off, go to a point or hover unless an envelope is configured (`envelope` in `config/uav.json`: home point, a ceiling of 30 m and a radius of 100 m as editable defaults) and the altitude and position the job names are inside it; the requests that bring the vehicle home (return to launch, land, disarm) are never held back. `send(..., require_envelope=True)` does the same in the MAVLink transport, and `MissionUploader.upload_geofence()` can load the radius into the autopilot as a circular inclusion fence (`envelope.upload_geofence`, off by default).
- **A human confirmation signed by the server, valid once, is required for those requests, on by default:** the job carries `operator_confirmation`, a short HS256 token for exactly that request and job (`confirmation` in `config/uav.json`, key read from a file, lifetime limit, replay refused); a request refused for another reason does not use the token up. Pure standard library.
- **Productive requests can follow each other in the air, off by default (`phases.chain_in_flight`):** a go-to or a hover is accepted with the vehicle flying only when the previous request of the flight was accepted and sent, nothing to come home came after it, the gap is within `phases.chain_max_gap_s` and the envelope and the confirmation are in force. Arming and taking off always start from the ground.
- 30 new tests against the autopilot emulator (simulator evidence only; nothing has flown).
- **More tests on the corners of the bridge (coverage 93% to 96%):** the MQTT connection is now run against a stand-in for paho-mqtt (subscription after every connect and reconnect, only handled topics reach the job entry with their retain flag, a refused login, a broker that does not answer, a connection that never opens, a missing paho) and the mission upload is run against a scripted vehicle (items that are not a mission, every refusal before anything is sent, a request for an item that does not exist, a silent or late vehicle, the total time limit, a lost link, and 200 random scripts from a fixed seed that must always end with a verdict). 30 new tests; no behaviour changed.

## [0.2.1] - The job entry can follow the global stop of the server, off by default: with `HYDRA_UMC_UAV_GLOBAL_STOP=1` (topic `HYDRA_UMC_UAV_GLOBAL_STOP_TOPIC`, `HYDRA_UMC_UAV_GLOBAL_STOP_STRICT=1` to also refuse before the first announcement) the entry reads the retained

- **The job entry can follow the global stop of the server, off by default:** with `HYDRA_UMC_UAV_GLOBAL_STOP=1` (topic `HYDRA_UMC_UAV_GLOBAL_STOP_TOPIC`, `HYDRA_UMC_UAV_GLOBAL_STOP_STRICT=1` to also refuse before the first announcement) the entry reads the retained announcement of the server on `hydra/swarm/safety/estop`. While it says `SAFE_STOP`, or cannot be read, every job that asks the vehicle to do something (arm, take off, go to a point, hover and capture) is refused with the reason and keeps its idempotency key free; return to launch, land and disarm still go through, so a vehicle in the air can always come home. Only a clear `READY` ends the refusal. 16 more tests.
- **Jobs over MQTT, off by default (`job_entry.enabled`):** `job_entry.py` receives one job on `hydra/bridges/uav/cmd/job`, ignores a retained replay, refuses an oversized, unreadable or malformed payload, does not run a repeated idempotency key twice (a return to launch is forwarded again) and decides it with `UavCoordinator` using the state the autopilot reported. It is plan-only: without a sender the answer on `cmd/job/result` says `sent: false`, and `ARM` is never confirmed by the entry. `run --mqtt-host` starts it through `mqtt_transport.py` (lazy paho-mqtt, retried first connect, subscription renewed after every reconnect) only when the settings or `HYDRA_UMC_BRIDGE_UAV_JOB_ENTRY=1` switch it on.
- **The position of every picture can go to the data lake, off by default (`datalake.send_enabled`):** `datalake_sender.py` posts the numbers-only sample of a successful capture to `POST /ingest` with the bearer token read from `datalake.token_file`; an `http://` address must be loopback unless `datalake.allow_remote` says otherwise, a redirect is never followed, a failed capture sends nothing and a data lake that is down or says no costs a counter, never an exception.
- **The optional simulator check now runs and passes against PX4 SITL:** `sitl.yml` failed before reaching the simulator because pip could not resolve the pinned SDK twice, then heard no heartbeat because the simulator sent its MAVLink nowhere the check listened. The SDK and `pymavlink` are installed first, the simulator is told to send to the runner and waited for until it is ready, and the check listens on its onboard link (UDP 14540): in Actions the link stayed OK for the whole run and the bridge sent its heartbeat. That is a simulator, not an autopilot. The new test modules run in the simulation bench.
- 31 more tests: the job entry (switch, retained, oversized, malformed, repeated key, safe-state requests, unknown autopilot state, sender, broker gone, CLI wiring) and the data lake sender on a real socket (token, sequence, 4xx/5xx, gone, redirect, address rules, settings).
- **Whole sorties with injected faults:** `tests/test_sortie_scenarios.py` flies the real flight control from arming to touchdown against the autopilot emulator and injects a low battery, a lost GPS fix, the link-loss failsafe, a silent camera, a second takeoff in the air, a cell that stops and a vehicle that does not know the signing key of the station (wrong key, unsigned with and without acceptance of unsigned messages). Simulator evidence only; no PX4, ArduPilot or aircraft was involved.
## [0.2.0] - A visible simulation bench: the workflow "Simulation bench" runs the emulators and fakes of the bridge without hardware on every push, by hand and once a week; docs/SIMULATION.md says what it exercises and what it cannot show. (and 1 more changes)

- **A visible simulation bench:** the workflow "Simulation bench" runs the emulators and fakes of the bridge without hardware on every push, by hand and once a week; `docs/SIMULATION.md` says what it exercises and what it cannot show.

- **The state of the vehicle can be handed to HYDRA-UMC-TWIN, off by default (`twin.send_enabled`):** `twin_sender.py` posts each `TwinState` that the twin feed builds to `POST /twin/uav` of the twin (started there with `serve --uav-input`), with the bearer token read from `twin.token_file`. An `http://` address must be a loopback one unless `twin.allow_remote` says otherwise, an `https://` address is checked against the system certificates, a redirect is never followed (it would carry the token away), and a twin that is down or says no costs a counter (`failed`, `refused`) and never stops the bridge. The address, the token file and the timeout are in `config/uav.json`. 11 tests.

## [0.1.9] - The build test of the repository compiles only on CI or when allowed: for the stacks that compile (rust, go, android, flutter, firmware-c), tools/buildtest.py prints BUILDTEST=SKIPPED with exit status 2 on a plain PC, never a... (and 5 more changes)

- **The build test of the repository compiles only on CI or when allowed:** for the stacks that compile (`rust`, `go`, `android`, `flutter`, `firmware-c`), `tools/build_test.py` prints `BUILD_TEST=SKIPPED` with exit status 2 on a plain PC, never a pass; `ALLOW_LOCAL_BUILD=1` allows it and a CI runner always may. The `python` and `node` stacks are not locked.

- **The mission upload switch can follow Server Admin:** when the host of the bridge allows it (`HYDRA_UMC_BRIDGE_UAV_REMOTE_SETTINGS=1`, off by default) and names the server configuration file it already reads for the signing options (`HYDRA_UMC_BRIDGE_UAV_SERVER_CONFIG`), `with_server_switches()` takes `mission.upload_enabled` from `bridges.uav.missionUploadEnabled` of that file, in either direction. Without the host switch or the file name the settings stay as `config/uav.json` says; a file that cannot be read or holds a damaged value switches the upload off. 6 more tests.
- **A whole mission can be uploaded, after a geofence check:** `mavlink_mission.py` runs the MAVLink mission protocol (count, one item per request, acknowledgement) for takeoff, waypoint, landing and return-to-launch items. Nothing leaves the bridge unless the upload is switched on (`mission.upload_enabled` in the new `config/uav.json`, off by default), a flight envelope is given, the item count is within `mission.max_items` and every waypoint, takeoff and landing point is inside the envelope; one item outside stops the whole mission before the first message. A refusal, a silent vehicle and a closed link come back as a reason, and the mission is never started by the upload.
- **A picture comes back with its place:** `camera_capture.py` turns the camera's image-captured message into an event (position, altitude, attitude, image index, file URL, success flag) and into a numbers-only sample for the data lake. A failed capture or an unreadable position is never reported as a good picture.
- **Position and attitude for the digital twin:** `twin_telemetry.py` builds a state of the twin contract of the shared SDK from the position and attitude of the vehicle (metres east, north and up of home, unit quaternion), marked real or simulated as the configuration says, checked with the SDK's own validator and limited to one state per `twin.min_interval_s`. The values of all three live in `config/uav.json`.
- The emulator in the tests now answers the mission protocol and the image capture; 30 more tests.

## [0.1.8] - The link of the vehicle can be monitored with a sink on the one reading thread, an optional simulator check is available by hand and the bench test script lists what needs a vehicle

- **Bench test script:** `docs/BENCH_TESTS.md` lists the checks that need a simulator or a vehicle (link monitor, job intake, failsafe, signing), all marked not executed.

- **`run --link-monitor` (off by default):** the link is read on the one `MavlinkLink` thread through a `LinkedSink` (sending goes to the sink, receiving to the link), so a wait for a `COMMAND_ACK` and the heartbeat watchdog no longer take messages from each other, and every status line carries `link`: `OK` or `LOST`, the seconds of silence, the failsafe it would ask for and whether the reader is running. `--link-timeout` or `HYDRA_UMC_BRIDGE_UAV_LINK_TIMEOUT_SECONDS` (5 s by default); `HYDRA_UMC_BRIDGE_UAV_LINK_MONITOR=1` switches it on from the environment.
- **An optional simulator check:** `.github/workflows/sitl.yml` (started by hand only) starts a PX4 SITL container and runs `tools/sitl_smoke.py`, which passes when the simulator's heartbeat was heard and the bridge sent its own. It has never been run.

## [0.1.7] - One receiver thread for a MAVLink connection

- **One receiver thread for a MAVLink connection (`MavlinkLink`):** a single reader takes every message from the connection, refreshes the link watchdog on each vehicle heartbeat (a ground-station heartbeat does not count), and keeps the other messages by type for `recv_match()`, so the watchdog and the wait for an acknowledgement no longer take each other's messages. If the connection fails the thread stops and the link goes LOST. It is opt-in: the existing transport is unchanged.

## [0.1.6] - A shared job intake, MAVLink 2 signing and the signing options from the Server configuration

- **A message entry point with the shared intake:** `UavCoordinator.handle_message()` takes a job as it arrives from a transport and applies the SDK's `JobIntake`: a retained message is ignored, a malformed one is refused (no exception) and a repeated idempotency key is not run a second time; the answer given is stored with the key (`UavCoordinator(IdempotencyStore(path))` keeps the keys across a restart, the default keeps them in memory). A repeated return-to-launch, land or disarm is still forwarded, since repeating a request that only brings the vehicle to a safe state is harmless. 7 tests.
- **The signing options can come from the Server configuration.** `run --server-config FILE` (or `HYDRA_UMC_BRIDGE_UAV_SERVER_CONFIG`) reads the `mavlink` section that an administrator saves from Server Admin (`signingKeyFile`, `signingAllowUnsigned`); the command-line option and the environment (`HYDRA_UMC_BRIDGE_UAV_SIGNING_ALLOW_UNSIGNED` is new) still win, a configuration that cannot be read is a usage error rather than a start without signing, and the defaults do not change. Documented in `docs/BRIDGE_GUIDE.md`. 7 new tests.
- **README in the universal layout:** the README and its seven translations follow the layout that the four families share (22 sections in a fixed order), with an architecture picture and a repository-structure picture drawn from `docs/architecture.json` and `docs/structure.json`; the CI validator checks both pictures and the file tree. Where the repository has a service or an MQTT transport, the README now says how to start it and which environment variables it reads.

- **The layout of the README is checked in the CI:** the validator runs `tools/universal_readme_check.py` and lists, as a warning, what differs from the universal layout of the README.
- **MAVLink 2 signing.** `run --signing-key-file FILE` (or `HYDRA_UMC_BRIDGE_UAV_SIGNING_KEY_FILE`) signs what the bridge sends with a 32-byte key and refuses what is received unsigned (`--signing-allow-unsigned` keeps accepting it); without a key nothing changes. The key file is read as 32 raw bytes or 64 hexadecimal characters and is never printed. Documented in `docs/BRIDGE_GUIDE.md`. 8 new tests, against a recorder of the pymavlink calls.

## [0.1.5] - A `run` command keeps the heartbeat going and own ground-station heartbeat

- **The phase table and the gate are the shared phase coordinator of the SDK** (`hydra_umc_sdk.phase_coordinator`), the same one the AMR and droid bridges use. The requests, the order of the vocabulary and the answers are unchanged; coming home, landing and disarming are still always forwarded.
- **A `run` command keeps the heartbeat going**: `hydra-umc-bridge-uav run --connection <URL>` (also `python -m hydra_umc_bridge_uav run`) opens the MAVLink link, starts the ground-station heartbeat at `HYDRA_UAV_GCS_HEARTBEAT_HZ` or `--heartbeat-hz` (a rate of 0 is refused, there would be nothing to do), prints one JSON status line every `--status-every` seconds and stops cleanly on Ctrl-C, SIGTERM or after `--duration`; it sends nothing else to the vehicle. Exit codes: 0 stopped normally, 1 the link could not be opened or no beat was sent, 2 a bad option. 7 new tests with a recorder of the link; never run against a real autopilot or SITL.
- **Own ground-station heartbeat.** The bridge sends a MAVLink `HEARTBEAT` of type `MAV_TYPE_GCS` on a background thread (`GcsHeartbeat`), so the autopilot's lost-station failsafe fires if the bridge dies. 1 Hz by default; `HYDRA_UAV_GCS_HEARTBEAT_HZ` sets it between 0.1 and 10 Hz and `0` switches it off. A beat that cannot be sent is counted and reported, never raised. Tested against a fake link; not tried against a real autopilot.
- Building no longer changes the version; raising it is the explicit `bump` command (`python tools/hydra_project_tool.py bump`). The build scripts only report the version they built, and CI fails if a build script runs a bump.
- The banner follows the animated style of the ecosystem banners, with the name, the kind and the technologies of the project.
- Added `tools/hydra_project_tool.py`: the same `validate`, `test`, `version`, `status` and `bump` commands in every repository. The checks run with `HYDRA_UMC_CI=1` and fail if the version changes; only `bump` changes it.
- The SDK is now fetched at a release tag instead of the main branch, so a change of the SDK cannot break this repository by itself.
- The SDK is pinned to 0.3.6, whose gate refuses a productive job when the bridge did not read the state of its machine. `UavCoordinator.dispatch()` must be given the state the autopilot reported; without it every flight phase is refused and the claim written in the job message is no longer trusted. Coming home (return to launch) is still forwarded without a reading. The tests now pass the observed state explicitly and check both the refusal and the return-home exception.
- Successful incremental build: synchronized package metadata and `hydra-umc.project.json`.

## [0.1.4]

- The wait for a `COMMAND_ACK` now ends at its deadline even on a busy shared link: acknowledgements from other systems were skipped without looking at the clock, so a neighbour that kept acknowledging its own commands could keep a request (a return to launch included) waiting for ever instead of reporting that no acknowledgement came.
- Tests for the safe-state requests that go out when the gate said no, the flight envelope limits, the link adapter and the acknowledgement wait.
- Synchronized package metadata and `hydra-umc.project.json`.

## [0.1.3]

- Successful incremental build: synchronized package metadata and `hydra-umc.project.json`.
- The licence is written as GPL-3.0-or-later in every header and in the package metadata.

## [0.1.2] - Maturity lowered to functional

- Maturity is functional: the code, tests and documentation are in place, but nothing here has been verified on real hardware yet, so the manifest no longer claims the established level.

## [0.1.1] - An explicit altitude frame, a flight envelope and stops that always get through

- **`GOTO_WAYPOINT` is a `COMMAND_INT` with an explicit altitude frame.** The position goes out as degrees x 1e7 (a `COMMAND_LONG` rounds it to float32, and an autopilot may accept a positional command only as a `COMMAND_INT`), the altitude reference is named - `altitude_frame="relative"` (the default, above the takeoff point) or `"amsl"` - and the yaw is NaN (keep the heading) instead of 0, which turned the vehicle north at every reposition. Before, the reference was undefined: a `COMMAND_LONG` carries no frame, so each autopilot chose its own.
- **`FlightEnvelope`.** An optional ceiling and radius from home, set by the deployment, that `send()` checks for `TAKEOFF` and `GOTO_WAYPOINT` (it needs a relative altitude to compare). Without one only the universal range checks apply, as before.
- **A stop is never held back.** `RETURN_TO_LAUNCH`, `LAND` and the new `DISARM` form `UavCoordinator.SAFE_STATE_REQUESTS`: a `COMPLETE` return home is now accepted whatever the cell and the machine state say (a vehicle in flight is not `IDLE`, so it used to be refused exactly when it should come home), and the MAVLink transport sends them even when the dispatch carries a refusal, as the simulated vehicle always did. The productive phases stay behind the shared gate.
- **`DISARM`** (`MAV_CMD_COMPONENT_ARM_DISARM`, `param1=0`) as a standalone request, so an `ARM` followed by a refused `TAKEOFF` does not leave the vehicle armed on the ground. It is never forced: the autopilot refuses it in flight by itself, and there is deliberately no kill.
- **`COMMAND_ACK` handling.** The wait skips the acknowledgements of other commands and, when the message carries its source, of other systems on the shared link, instead of failing on the first one; `IN_PROGRESS` waits for the final result.
- **Capture and loiter parameters.** `IMAGE_START_CAPTURE` takes one image (it used to ask for images until stopped, which was never sent) and can be addressed to a camera component (`camera_component`); the loiter keeps the heading.
- **`open_mavlink_connection()`** stops waiting for a heartbeat after `heartbeat_timeout_seconds` with a clear error, and the object it returns carries the `target_system` / `target_component` of the vehicle.
- **`HeartbeatMonitor`** refuses a `failsafe_action` that the transport cannot send; the documented hover policy never existed and would have done nothing on a link loss. `RETURN_TO_LAUNCH` or `LAND`.
- **Documentation.** Units and the altitude reference are documented; the README no longer says the MAVLink transport is missing, no longer claims a bidirectional link with telemetry, and its diagram follows the real direction of the commands; the test emulator says what it does not model; `LICENSE` carries the full GPL-3.0-or-later text.
- Not run against PX4 or ArduPilot SITL, nor a real vehicle: the wire format is checked against the emulator only. The altitude of `TAKEOFF` is still the autopilot's own reference, because a `COMMAND_LONG` carries no frame, and a forced disarm, mission upload, link-loss wiring of the heartbeat monitor and the retry with an incremented `confirmation` are not implemented.
- 20 new tests (84 in total); an unused import in the CI validator removed.

## [0.1.0] - The gate can use the machine state the autopilot reports

- **Observed machine state.** `UavCoordinator.dispatch()` takes the state the autopilot reported as an optional argument; when it is given it replaces the `machine_state` a requester wrote in the job (a message anybody on the broker may publish) in the shared gate. Without it the claim is still used.
- Four new tests (tests/test_observed_state.py).

## [0.0.9] - Flight-state table and a simulated vehicle

- **Flight-state table and a simulated vehicle** (`simulated_uav.py`): `TRANSITIONS` states
  which flight request is legal from which vehicle state. Telemetry is a read-only
  snapshot; authorization is its own explicit grant, required by every request that
  leaves the ground; arming is a vehicle state reached only by an accepted `ARM`.
  `RETURN_TO_LAUNCH` and `LAND` are accepted from every state whatever the
  coordinator or authorization said. The vehicle imports no transport and cannot fly.
  Eight new tests (60 in total); the README test counts were brought up to date.

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
