# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Command line: cli.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""`hydra-umc-bridge-uav run`: open the MAVLink link and keep the bridge's ground-station heartbeat going.

The heartbeat is what lets the autopilot's own failsafe (ArduPilot `FS_GCS_ENABLE`, PX4 `NAV_DLL_ACT`) tell a dead
bridge from a quiet one, so it has to be running whenever the bridge is. `run` does only that: it opens the link
(`--connection`, pymavlink's own URL), starts the heartbeat at the configured rate, prints one status line every
few seconds and stops cleanly on Ctrl-C or SIGTERM (or after `--duration` seconds). It sends nothing else to the
vehicle: commands still only leave through the gate in front of `MavlinkFlightControl`.

Exit codes: 0 stopped normally, 1 the link could not be opened or no heartbeat could be sent, 2 a bad option.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from typing import Callable, Sequence, TextIO

from .gcs_heartbeat import ENVIRONMENT_VARIABLE, GcsHeartbeat, parse_rate_hz, rate_from_environment

EXIT_OK = 0
EXIT_LINK = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydra-umc-bridge-uav", description="Fail-safe HYDRA-UMC to UAV coordination boundary.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="open the MAVLink link and keep the bridge's own ground-station heartbeat going")
    run.add_argument("--connection", required=True, help="pymavlink connection URL, e.g. udpin:0.0.0.0:14550 or /dev/ttyUSB0")
    run.add_argument(
        "--heartbeat-hz", default=None,
        help=f"heartbeat rate, 0.1 to 10 Hz (default: {ENVIRONMENT_VARIABLE}, else 1); 0 would send nothing and is refused here",
    )
    run.add_argument("--heartbeat-timeout", type=float, default=10.0, help="seconds to wait for the vehicle's first heartbeat (default 10)")
    run.add_argument("--status-every", type=float, default=10.0, help="seconds between status lines; 0 prints none (default 10)")
    run.add_argument("--duration", type=float, default=0.0, help="stop after this many seconds; 0 runs until stopped (default 0)")
    return parser


def _status_line(heartbeat: GcsHeartbeat, out: TextIO) -> None:
    status = heartbeat.status()
    out.write(json.dumps({
        "heartbeat": {"running": status.running, "rate_hz": status.rate_hz, "sent": status.sent, "failed": status.failed, "last_error": status.last_error},
    }) + "\n")
    out.flush()


def run(
    args: argparse.Namespace,
    *,
    opener: Callable[..., object] | None = None,
    stop: threading.Event | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    install_signals: bool = True,
) -> int:
    """The `run` command. `opener`, `stop`, `out` and `err` are injectable for the tests."""
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        rate = parse_rate_hz(args.heartbeat_hz) if args.heartbeat_hz is not None else rate_from_environment()
    except ValueError as error:
        err.write(f"error: {error}\n")
        return EXIT_USAGE
    if rate == 0:
        err.write("error: the heartbeat rate is 0 (off); `run` has nothing to do. Unset it or give a rate between 0.1 and 10 Hz.\n")
        return EXIT_USAGE
    if args.duration < 0 or args.status_every < 0 or args.heartbeat_timeout <= 0:
        err.write("error: --duration and --status-every cannot be negative and --heartbeat-timeout must be positive\n")
        return EXIT_USAGE

    if opener is None:
        from .mavlink_transport import open_mavlink_connection as opener  # lazy: pymavlink is optional

    stop = stop or threading.Event()
    if install_signals and threading.current_thread() is threading.main_thread():
        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_a: stop.set())

    try:
        sink = opener(args.connection, heartbeat_timeout_seconds=args.heartbeat_timeout)
    except Exception as error:  # noqa: BLE001 - any failure to open the link is reported, not a traceback
        err.write(f"error: the MAVLink link could not be opened: {error}\n")
        return EXIT_LINK

    heartbeat = GcsHeartbeat(sink, rate)
    heartbeat.start()
    out.write(f"HEARTBEAT_RUNNING connection={args.connection} rate_hz={rate:g}\n")
    out.flush()
    waited = 0.0
    step = min([1.0] + [value for value in (args.status_every, args.duration) if value > 0])
    since_status = 0.0
    try:
        while not stop.is_set():
            if stop.wait(step):
                break
            waited += step
            since_status += step
            if args.status_every > 0 and since_status >= args.status_every:
                _status_line(heartbeat, out)
                since_status = 0.0
            if args.duration > 0 and waited >= args.duration:
                break
    finally:
        heartbeat.stop()
        close = getattr(sink, "close", None)
        if callable(close):
            close()
    _status_line(heartbeat, out)
    final = heartbeat.status()
    out.write("HEARTBEAT_STOPPED\n")
    out.flush()
    return EXIT_OK if final.sent > 0 else EXIT_LINK


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exit_request:
        return int(exit_request.code or 0)
    if args.command == "run":
        return run(args)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
