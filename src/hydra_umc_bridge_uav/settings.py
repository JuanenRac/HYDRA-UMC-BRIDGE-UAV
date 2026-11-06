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

import dataclasses
import json
import math
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping


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
    # The hand-over to HYDRA-UMC-TWIN (`serve --uav-input` there), off by default: where to post, the file with the token of its interlock routes, how long to wait.
    send_enabled: bool = False
    url: str = ""
    token_file: str = ""
    timeout_s: float = 2.0
    # With false (the default) an http:// address must be a loopback one: the token would travel in clear text.
    allow_remote: bool = False

    def problems(self) -> list[str]:
        found = []
        if not isinstance(self.robot_id, str) or not self.robot_id.strip():
            found.append("twin.robot_id must be a non-empty name")
        if self.source not in ("real", "simulated"):
            found.append("twin.source must be 'real' or 'simulated'")
        if not (_number(self.min_interval_s) and self.min_interval_s >= 0):
            found.append("twin.min_interval_s must be a finite number, zero or more")
        for name in ("send_enabled", "allow_remote"):
            if not isinstance(getattr(self, name), bool):
                found.append(f"twin.{name} must be true or false")
        for name in ("url", "token_file"):
            if not isinstance(getattr(self, name), str):
                found.append(f"twin.{name} must be text")
        if not (_number(self.timeout_s) and 0 < self.timeout_s <= 60):
            found.append("twin.timeout_s must be a number above zero and at most 60")
        if self.send_enabled is True and isinstance(self.url, str) and isinstance(self.token_file, str):
            if not self.token_file.strip():
                found.append("twin.token_file must name the file with the token when twin.send_enabled is true")
            try:
                from .twin_sender import check_url

                check_url(self)
            except ValueError as error:
                found.append(str(error))
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


REMOTE_ENVIRONMENT = "HYDRA_UMC_BRIDGE_UAV_REMOTE_SETTINGS"
_TRUE = {"1", "true", "yes", "on"}


def remote_settings_allowed(environ: Mapping[str, str] | None = None) -> bool:
    """True only when the host of the bridge switched remote settings on (`HYDRA_UMC_BRIDGE_UAV_REMOTE_SETTINGS=1`, off by default)."""
    source = os.environ if environ is None else environ
    return source.get(REMOTE_ENVIRONMENT, "").strip().lower() in _TRUE


def read_server_mission_upload(path: str) -> bool | None:
    """`bridges.uav.missionUploadEnabled` of the Server's `data/server-config.json` (set in Server Admin); None when the file does not carry it.

    This bridge has no broker connection, so the switch reaches it through the same file that already gives it its MAVLink signing options. A file that cannot
    be read, or a value that is not true or false, is a ValueError.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"the server configuration file cannot be read: {getattr(error, 'strerror', None) or error}") from error
    section = document.get("bridges") if isinstance(document, dict) else None
    uav = section.get("uav") if isinstance(section, dict) else None
    if not isinstance(uav, dict) or "missionUploadEnabled" not in uav:
        return None
    value = uav["missionUploadEnabled"]
    if not isinstance(value, bool):
        raise ValueError("bridges.uav.missionUploadEnabled in the server configuration must be true or false")
    return value


def with_server_switches(settings: UavSettings, server_config: str | None, environ: Mapping[str, str] | None = None) -> UavSettings:
    """The settings with the mission upload switch of Server Admin, when the host allows remote settings and names the server configuration file.

    Without the host switch or the file name, `settings` come back unchanged. When both are given and the file cannot be read or its value is damaged, the
    mission upload is switched OFF: a bridge told to follow the server that cannot hear it does not keep a switch that was on.
    """
    if not server_config or not remote_settings_allowed(environ):
        return settings
    try:
        value = read_server_mission_upload(server_config)
    except ValueError:
        value = False
    if value is None:
        return settings
    return dataclasses.replace(settings, mission=dataclasses.replace(settings.mission, upload_enabled=value))
