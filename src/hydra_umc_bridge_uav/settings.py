# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Values of the mission upload, the camera and the twin feed
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The tuned values of the mission upload, the camera capture and the twin feed, read from `config/uav.json`.

Nothing is fixed in the code. A missing file gives the conservative defaults (the mission upload is OFF); an unknown key, a wrong type or an out-of-range value is an
error, never a silent default, because these are safety values.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields
from pathlib import Path


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class MissionSettings:
    upload_enabled: bool = False
    max_items: int = 100
    request_timeout_s: float = 2.0
    total_timeout_s: float = 60.0

    def problems(self) -> list[str]:
        found = []
        if not isinstance(self.upload_enabled, bool):
            found.append("mission.upload_enabled must be true or false")
        if type(self.max_items) is not int or not 1 <= self.max_items <= 1000:
            found.append("mission.max_items must be a whole number from 1 to 1000")
        for name in ("request_timeout_s", "total_timeout_s"):
            value = getattr(self, name)
            if not (_number(value) and value > 0):
                found.append(f"mission.{name} must be a finite, positive number")
        return found


@dataclass(frozen=True)
class CameraSettings:
    wait_for_image_s: float = 5.0
    datalake_source_id: str = "uav"

    def problems(self) -> list[str]:
        found = []
        if not (_number(self.wait_for_image_s) and self.wait_for_image_s > 0):
            found.append("camera.wait_for_image_s must be a finite, positive number")
        if not isinstance(self.datalake_source_id, str) or not self.datalake_source_id.strip():
            found.append("camera.datalake_source_id must be a non-empty name")
        return found


@dataclass(frozen=True)
class TwinSettings:
    robot_id: str = "uav-1"
    source: str = "real"
    min_interval_s: float = 0.2

    def problems(self) -> list[str]:
        found = []
        if not isinstance(self.robot_id, str) or not self.robot_id.strip():
            found.append("twin.robot_id must be a non-empty name")
        if self.source not in ("real", "simulated"):
            found.append("twin.source must be 'real' or 'simulated'")
        if not (_number(self.min_interval_s) and self.min_interval_s >= 0):
            found.append("twin.min_interval_s must be a finite number, zero or more")
        return found


@dataclass(frozen=True)
class UavSettings:
    mission: MissionSettings = field(default_factory=MissionSettings)
    camera: CameraSettings = field(default_factory=CameraSettings)
    twin: TwinSettings = field(default_factory=TwinSettings)

    def problems(self) -> list[str]:
        return self.mission.problems() + self.camera.problems() + self.twin.problems()


def load_settings(path: str | Path | None) -> UavSettings:
    """Read `config/uav.json`; no path or a missing file gives the defaults. An unknown key or an invalid value is a ValueError."""
    if path is None or not Path(path).is_file():
        return UavSettings()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("the settings must be a JSON object")
    sections = {"mission": MissionSettings, "camera": CameraSettings, "twin": TwinSettings}
    unknown = sorted(set(raw) - set(sections))
    if unknown:
        raise ValueError(f"unknown settings sections: {', '.join(unknown)}")
    built = {}
    for name, kind in sections.items():
        body = raw.get(name, {})
        if not isinstance(body, dict):
            raise ValueError(f"{name} must be a JSON object")
        bad = sorted(set(body) - {item.name for item in fields(kind)})
        if bad:
            raise ValueError(f"unknown keys in {name}: {', '.join(bad)}")
        built[name] = kind(**body)
    settings = UavSettings(**built)
    problems = settings.problems()
    if problems:
        raise ValueError("; ".join(problems))
    return settings
