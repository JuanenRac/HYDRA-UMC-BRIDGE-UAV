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
| `tests/test_mavlink_link.py` | the vehicle heartbeat on the link: ok, lost and the ground station that is not the vehicle |
| `tests/test_mavlink_transport.py` | the transport against the emulator |
| `tests/test_simulated_uav.py` | the simulated UAV and its transitions |
| `tests/test_gcs_heartbeat.py` | the heartbeat the bridge sends as a ground station |
| `tests/test_heartbeat.py` | the heartbeat monitor: never heard is lost, the timeout boundary and recovery |
| `tests/test_mission_camera_twin.py` | the settings, the mission upload item by item (never started) and the camera and twin features with simulated input |
| `tests/test_sitl_smoke.py` | the judge and the options of the software-in-the-loop smoke script; the run itself against an ArduPilot SITL is started by hand and is not part of this job |

The broker scenarios start a real MQTT broker from a checkout of HYDRA-UMC-MQTT-BROKER (`HYDRA_UMC_TEST_BROKER_DIR`); the simulation job does not install it, so
those cases skip themselves there and run in the normal test run on a machine that has the broker.

## What it cannot show

An ArduPilot or PX4 SITL in CI (it is started by hand with `tools/sitl_smoke.py`), a real flight controller, radio link and aircraft. The hardware row of the status table therefore stays at "simulated" until a person has run the bridge on a real setup.
