#!/usr/bin/env python3
# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - tools/sitl_smoke.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# Smoke test against an autopilot simulator (PX4 or ArduPilot SITL) that is already running: the bridge opens the link,
# keeps its ground-station heartbeat going with the link monitor on, and the check passes when the heartbeat of the simulator
# was heard (the link reported OK at least once) and the bridge sent its own beats. It sends nothing else to the vehicle.
# The optional CI job (.github/workflows/sitl.yml) starts the simulator and runs this; it is not part of the normal checks.
#
#   py -3 tools/sitl_smoke.py --connection udpin:0.0.0.0:14550 [--seconds 15]
# =============================================================================
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Sequence


def judge(output: str) -> tuple[bool, str]:
    """(passed, reason) from the output of `run --link-monitor`: at least one status line with the link OK, and beats sent."""
    links: list[str] = []
    sent = 0
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        try:
            document = json.loads(line)
        except ValueError:
            continue
        link = document.get("link")
        if isinstance(link, dict) and isinstance(link.get("status"), str):
            links.append(link["status"])
        heartbeat = document.get("heartbeat")
        if isinstance(heartbeat, dict) and isinstance(heartbeat.get("sent"), int):
            sent = max(sent, heartbeat["sent"])
    if not links:
        return False, "no status line with a link: the run did not start the link monitor"
    if "OK" not in links:
        return False, f"the heartbeat of the simulator was never heard (link states: {sorted(set(links))})"
    if sent <= 0:
        return False, "the bridge sent no heartbeat"
    return True, f"the link was OK and {sent} heartbeat(s) were sent"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test of the bridge against a running autopilot simulator.")
    parser.add_argument("--connection", required=True, help="pymavlink connection URL of the simulator, e.g. udpin:0.0.0.0:14550")
    parser.add_argument("--seconds", type=float, default=15.0, help="how long to run (default 15)")
    parser.add_argument("--open-timeout", type=float, default=30.0, help="seconds to wait for the first heartbeat of the simulator (default 30)")
    args = parser.parse_args(argv)
    if args.seconds <= 0 or args.open_timeout <= 0:
        print("error: --seconds and --open-timeout must be positive", file=sys.stderr)
        return 2
    command = [
        sys.executable, "-m", "hydra_umc_bridge_uav", "run", "--connection", args.connection, "--link-monitor", "--heartbeat-hz", "2",
        "--status-every", "1", "--duration", str(args.seconds), "--heartbeat-timeout", str(args.open_timeout),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=args.seconds + args.open_timeout + 30, check=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print(f"SITL_SMOKE=FAIL the run ended with code {result.returncode}")
        return 1
    passed, reason = judge(result.stdout)
    print(f"SITL_SMOKE={'PASS' if passed else 'FAIL'} {reason}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
