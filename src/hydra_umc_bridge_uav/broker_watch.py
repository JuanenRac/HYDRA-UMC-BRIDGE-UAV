# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Watch of the MQTT broker connection
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Since when has the MQTT broker been gone? Used to let the station heartbeat stop with it, when the settings ask for that."""

from __future__ import annotations

import threading
import time
from typing import Callable


class BrokerWatch:
    """Remembers since when the broker has been gone. `healthy()` is False once that has lasted longer than `stop_after_s` (0 never)."""

    def __init__(self, stop_after_s: float = 0.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.stop_after_s = float(stop_after_s)
        self._clock = clock
        self._lost_since: float | None = None
        self._lock = threading.Lock()

    def connected(self) -> None:
        with self._lock:
            self._lost_since = None

    def lost(self) -> None:
        with self._lock:
            if self._lost_since is None:
                self._lost_since = self._clock()

    def healthy(self) -> bool:
        with self._lock:
            since = self._lost_since
        return self.stop_after_s <= 0 or since is None or self._clock() - since <= self.stop_after_s
