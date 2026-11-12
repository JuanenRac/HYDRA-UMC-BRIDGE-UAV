# Bench tests

Every test below is **not executed** on a vehicle; steps 1 and 2 were executed against a PX4 simulator in Actions (marked). The bridge has run only against the MAVLink emulator in `tests/mavlink_emulator.py` and the in-memory simulated vehicle; it has never talked to a flight controller, a PX4 or ArduPilot simulator, or an aircraft. Write a result only after a person ran the step.

## What the software already covers (no vehicle needed)

- `python tools/build_test.py` runs the unit tests: the job intake (retained, malformed and repeated messages), the link watchdog, the single receiver thread, the observed state and the emulator.
- `.github/workflows/sitl.yml` (started by hand) starts a PX4 simulator and runs `tools/sitl_smoke.py` against its onboard link (UDP 14540); it passed against PX4 SITL 1.14.3 in Actions, which says nothing about a real autopilot. The same workflow then runs `tools/sitl_mission.py`, which uploads a small mission through the bridge's uploader and passes only when the simulator accepts every item (the mission is never started).

## Setup

- Start with a simulator (PX4 or ArduPilot software in the loop) reachable on UDP, never an aircraft. Only after every step passes there, repeat the safe steps on a vehicle with its propellers removed.
- The bridge started with `hydra-umc-bridge-uav run --link-monitor` and the connection string of the simulator.
- The shared safety gate answering "ready"; a way to publish the global stop.

## Steps

| # | Step | Expected result | Result |
|---|------|-----------------|--------|
| 1 | Run `tools/sitl_smoke.py` against the simulator. | The simulator heartbeat is heard and the bridge heartbeat is sent. | executed in PX4 SITL (Actions): passed |
| 2 | Watch the status line for a minute. | `link` shows `OK`, the seconds of silence stay below the timeout and the reader is running. | executed in PX4 SITL (Actions) for 20 seconds: passed |
| 3 | Stop the simulator. | `link` changes to `LOST` after the configured silence and names the failsafe it would ask for. | not executed |
| 4 | Send the same job twice, then a retained job and a malformed one. | The first runs once, the second is answered from the store, the retained and malformed ones are refused. | not executed |
| 5 | With the vehicle armed in the simulator, send a return-to-launch, a land and a disarm request. | Each reaches the vehicle and is acknowledged; the gate does not block these three. | not executed |
| 6 | Send an ordinary command with the gate not ready. | The command is refused and nothing is sent to the vehicle. | not executed |
| 7 | With MAVLink 2 signing configured, send a command from a station with the wrong key. | The vehicle ignores it. | not executed |
| 8 | Run `tools/sitl_mission.py` against the simulator: upload a mission of four items inside the envelope, then one with a point outside it. | The simulator asks for every item and accepts the mission; the second one is refused by the bridge before anything is sent; nothing is armed or started. | executed in PX4 SITL (Actions): passed |

## What this bench cannot show

Radio range, link loss on real hardware, the behaviour of the failsafe on a specific autopilot and any timing under load. The mission upload, the geotagged capture, the feed to the twin, the data lake sample and the MQTT job entry are built and tested against the emulator and fakes, but have no step here yet.
