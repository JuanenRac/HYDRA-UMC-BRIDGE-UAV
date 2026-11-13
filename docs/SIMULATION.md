# Simulation bench

The UAV bridge speaks MAVLink. The emulator is a vehicle on the other end of the link: heartbeats, arming, mode changes, missions, rejected commands and a link that drops.

The simulation bench replaces the hardware with a MAVLink vehicle emulator (`tests/mavlink_emulator.py`), a simulated UAV and the GCS heartbeat. It proves the parsing, the gating, the transports and the failure paths of the bridge; it says nothing about
a real machine, real timing or real safety functions.

## Run it

```
python -m unittest -v <modules>
```

from the `tests` folder, with `src` of this repository and `clients/python/src` of the SDK on `PYTHONPATH` (the same layout `python tools/build_test.py` uses). The
workflow named "Simulation bench" does exactly this on a clean Linux runner on every push and pull request, by hand and once a week, with nothing but Python.

## What it runs

| File | What it exercises |
|------|-------------------|
| `tests/test_mavlink_emulator.py` | the emulator vehicle on the other end of the link |
| `tests/test_sortie_scenarios.py` | a whole sortie against the autopilot emulator with a low battery, a lost GPS fix, the link-loss failsafe, a silent camera and a station signing key the vehicle does not know |
| `tests/test_mavlink_link.py` | the vehicle heartbeat on the link: ok, lost and the ground station that is not the vehicle |
| `tests/test_mavlink_transport.py` | the transport against the emulator |
| `tests/test_simulated_uav.py` | the simulated UAV and its transitions |
| `tests/test_gcs_heartbeat.py` | the heartbeat the bridge sends as a ground station |
| `tests/test_heartbeat.py` | the heartbeat monitor: never heard is lost, the timeout boundary and recovery |
| `tests/test_mission_camera_twin.py` | the settings, the mission upload item by item (never started) and the camera and twin features with simulated input |
| `tests/test_flight_policy.py` | the required envelope, the signed one-use human confirmation (forged, expired, replayed, wrong request or job), chaining of requests in the air, the settings, the transport check and the geofence upload against the emulator |
| `tests/test_job_entry.py` | the MQTT job entry: off by default, retained, oversized and malformed payloads, a repeated key, requests that bring the vehicle home, the unknown autopilot state, a broker that is gone, and its wiring in `run` |
| `tests/test_datalake_sender.py` | the sample of a successful capture posted to a stand-in for the data lake on a real socket: token, sequence, refusals, a data lake that is gone, a redirect, the address rules and the settings |
| `tests/test_global_stop.py` | the job entry that follows the global stop of the server: off by default, every phase that asks for work refused while stopped or unreadable, return to launch and abort still passing, the idempotency key kept free, a seeded fuzz of payloads |
| `tests/test_human_confirmation_edges.py` | the signed one-use confirmation: every missing or mistyped claim, the clock limits, racing threads and the bounded memory of used tokens |
| `tests/test_sitl_smoke.py` | the judge and the options of the software-in-the-loop smoke script; the run itself against a PX4 SITL container is `sitl.yml`, started by hand, and is not part of this job |
| `tests/test_sitl_mission.py` | the judgement and the flow of the mission upload check against the autopilot emulator (accepted, refused, silent vehicle, a mission outside the envelope); the run against the simulator is `sitl.yml` |

The broker scenarios start a real MQTT broker from a checkout of HYDRA-UMC-MQTT-BROKER (`HYDRA_UMC_TEST_BROKER_DIR`); the simulation job does not install it, so
those cases skip themselves there and run in the normal test run on a machine that has the broker.

## What it cannot show

A real flight controller, radio link and aircraft. The hardware row of the status table therefore stays at "simulated" until a person has run the bridge on a real setup.
