# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - One receiver thread for a MAVLink connection
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================

"""A single reader of a MAVLink connection that feeds the link watchdog.

Reading a connection from two places (the heartbeat watchdog and the wait for
a COMMAND_ACK) makes them steal each other's messages. `MavlinkLink` is the one
reader: a thread takes every message from the connection, every vehicle
HEARTBEAT refreshes the `HeartbeatMonitor`, and every other message is kept per
type so `recv_match()` (the same signature `MavlinkFlightControl` already waits
on) can hand it out. If the connection fails, the thread stops and the monitor
stops being refreshed, so the link goes LOST: it never reports a link it can
no longer read.
"""

from __future__ import annotations

from collections import defaultdict, deque
import threading
import time
from typing import Callable, Protocol

from .heartbeat import HeartbeatMonitor

# MAV_TYPE_GCS: a ground station's own heartbeat is not the vehicle's.
_MAV_TYPE_GCS = 6
_QUEUE_DEPTH = 64


class _Reader(Protocol):
    def recv_match(self, type: str | None, blocking: bool, timeout: float) -> object | None: ...


class MavlinkLink:
    """Reads a MAVLink connection on one thread and routes what it hears."""

    def __init__(
        self,
        connection: _Reader,
        monitor: HeartbeatMonitor,
        clock: Callable[[], float] = time.monotonic,
        poll_s: float = 0.1,
    ) -> None:
        if poll_s <= 0:
            raise ValueError("poll_s must be positive")
        self._connection = connection
        self._monitor = monitor
        self._clock = clock
        self._poll_s = poll_s
        self._queues: dict[str, deque[object]] = defaultdict(lambda: deque(maxlen=_QUEUE_DEPTH))
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("the link is already started")
        self._thread = threading.Thread(target=self._run, name="mavlink-link", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._connection.recv_match(type=None, blocking=True, timeout=self._poll_s)
            except Exception as error:  # a failed link must end the thread, not be retried silently
                self.error = error
                with self._cond:
                    self._cond.notify_all()
                return
            if message is None:
                continue
            self._dispatch(message)

    def _dispatch(self, message: object) -> None:
        get_type = getattr(message, "get_type", None)
        kind = get_type() if callable(get_type) else None
        if not isinstance(kind, str):
            return
        if kind == "HEARTBEAT":
            if getattr(message, "type", None) != _MAV_TYPE_GCS:
                self._monitor.observe(self._clock())
            return
        with self._cond:
            self._queues[kind].append(message)
            self._cond.notify_all()

    def recv_match(self, type: str, blocking: bool, timeout: float) -> object | None:
        """The oldest kept message of `type`, waiting up to `timeout` seconds when `blocking`."""

        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                queue = self._queues.get(type)
                if queue:
                    return queue.popleft()
                if not blocking or self.error is not None or self._stop.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
