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
class DatalakeSettings:
    # Where the position of every successful picture goes (`POST /ingest` of HYDRA-UMC-DATALAKE), off by default.
    send_enabled: bool = False
    url: str = ""
    token_file: str = ""
    timeout_s: float = 2.0
    allow_remote: bool = False

    def problems(self) -> list[str]:
        found = []
        for name in ("send_enabled", "allow_remote"):
            if not isinstance(getattr(self, name), bool):
                found.append(f"datalake.{name} must be true or false")
        for name in ("url", "token_file"):
            if not isinstance(getattr(self, name), str):
                found.append(f"datalake.{name} must be text")
        if not (_number(self.timeout_s) and 0 < self.timeout_s <= 60):
            found.append("datalake.timeout_s must be a number above zero and at most 60")
        if self.send_enabled is True and isinstance(self.url, str) and isinstance(self.token_file, str):
            if not self.token_file.strip():
                found.append("datalake.token_file must name the file with the token when datalake.send_enabled is true")
            try:
                from .datalake_sender import check_url

                check_url(self)
            except ValueError as error:
                found.append(str(error))
        return found


@dataclass(frozen=True)
class JobEntrySettings:
    # The MQTT entry for jobs (`hydra/bridges/uav/cmd/job`), off by default; the broker address is an option of `run`.
    enabled: bool = False

    def problems(self) -> list[str]:
        return [] if isinstance(self.enabled, bool) else ["job_entry.enabled must be true or false"]


@dataclass(frozen=True)
class EnvelopeSettings:
    # The flight envelope the job entry requires before any request that starts or continues a flight (takeoff, go to a point). Required by default: without a
    # home point the requests are refused. The numbers are conservative and are meant to be edited for the site, always inside the legal limit the operator fixes.
    required: bool = True
    max_altitude_m: float = 30.0
    max_radius_m: float = 100.0
    home_lat: float | None = None
    home_lon: float | None = None
    # Also load the radius into the autopilot as a circular inclusion fence (MAVLink fence protocol); off by default like the mission upload.
    upload_geofence: bool = False

    def problems(self) -> list[str]:
        found = []
        for name in ("required", "upload_geofence"):
            if not isinstance(getattr(self, name), bool):
                found.append(f"envelope.{name} must be true or false")
        for name in ("max_altitude_m", "max_radius_m"):
            value = getattr(self, name)
            if not (_number(value) and value > 0):
                found.append(f"envelope.{name} must be a finite, positive number")
        for name, bound in (("home_lat", 90), ("home_lon", 180)):
            value = getattr(self, name)
            if value is not None and not (_number(value) and -bound <= value <= bound):
                found.append(f"envelope.{name} must be a number in [-{bound}, {bound}] or null")
        if (self.home_lat is None) != (self.home_lon is None):
            found.append("envelope.home_lat and envelope.home_lon must be given together")
        return found


@dataclass(frozen=True)
class ConfirmationSettings:
    # The confirmation of an operator, signed by the server and valid once, that the job must carry for the listed requests (see human_confirmation.py).
    required: bool = True
    key_file: str = ""
    max_ttl_s: float = 120.0
    max_clock_skew_s: float = 5.0
    actions: tuple[str, ...] = ("ARM", "TAKEOFF", "GOTO_WAYPOINT", "HOVER_AND_CAPTURE")

    def problems(self) -> list[str]:
        found = []
        if not isinstance(self.required, bool):
            found.append("confirmation.required must be true or false")
        if not isinstance(self.key_file, str):
            found.append("confirmation.key_file must be text")
        for name, limit in (("max_ttl_s", 3600), ("max_clock_skew_s", 60)):
            value = getattr(self, name)
            if not (_number(value) and 0 < value <= limit):
                found.append(f"confirmation.{name} must be a number above zero and at most {limit}")
        known = {"ARM", "TAKEOFF", "GOTO_WAYPOINT", "HOVER_AND_CAPTURE"}
        if not isinstance(self.actions, (list, tuple)) or any(item not in known for item in self.actions):
            found.append(f"confirmation.actions must be a list of {', '.join(sorted(known))}")
        return found


@dataclass(frozen=True)
class PhaseSettings:
    # Chaining of productive phases with the vehicle already in the air. Off by default: every flight starts on the ground. When on, a go-to or hover is
    # accepted with the vehicle in flight only if the previous request of the flight was accepted, the envelope is active and the operator confirmed it.
    chain_in_flight: bool = False
    chain_max_gap_s: float = 600.0

    def problems(self) -> list[str]:
        found = []
        if not isinstance(self.chain_in_flight, bool):
            found.append("phases.chain_in_flight must be true or false")
        if not (_number(self.chain_max_gap_s) and 0 < self.chain_max_gap_s <= 86400):
            found.append("phases.chain_max_gap_s must be a number above zero and at most 86400")
        return found


@dataclass(frozen=True)
class UavSettings:
    mission: MissionSettings = field(default_factory=MissionSettings)
    camera: CameraSettings = field(default_factory=CameraSettings)
    twin: TwinSettings = field(default_factory=TwinSettings)
    datalake: DatalakeSettings = field(default_factory=DatalakeSettings)
    job_entry: JobEntrySettings = field(default_factory=JobEntrySettings)
    envelope: EnvelopeSettings = field(default_factory=EnvelopeSettings)
    confirmation: ConfirmationSettings = field(default_factory=ConfirmationSettings)
    phases: PhaseSettings = field(default_factory=PhaseSettings)

    def problems(self) -> list[str]:
        return self.mission.problems() + self.camera.problems() + self.twin.problems() + self.datalake.problems() + self.job_entry.problems() + self.envelope.problems() + self.confirmation.problems() + self.phases.problems()


def load_settings(path: str | Path | None) -> UavSettings:
    """Read `config/uav.json`; no path or a missing file gives the defaults. An unknown key or an invalid value is a ValueError."""
    if path is None or not Path(path).is_file():
        return UavSettings()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("the settings must be a JSON object")
    sections = {"mission": MissionSettings, "camera": CameraSettings, "twin": TwinSettings, "datalake": DatalakeSettings, "job_entry": JobEntrySettings, "envelope": EnvelopeSettings, "confirmation": ConfirmationSettings, "phases": PhaseSettings}
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
        if name == "confirmation" and isinstance(body.get("actions"), list):
            body = {**body, "actions": tuple(body["actions"])}
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


#: The flight-policy switches of Server Admin (`bridges.uav.<name>`) and what each one may do here. They only ever TIGHTEN what the host's own file says: a requirement
#: (`envelopeRequired`, `confirmationRequired`) can be switched on from the server but never off, and a permission (`chainInFlight`, `uploadGeofence`) can be switched
#: off from the server but never on. A value the server sends that would relax the host's own file is ignored.
FLIGHT_POLICY_SWITCHES = ("envelopeRequired", "confirmationRequired", "chainInFlight", "uploadGeofence")


def read_server_flight_policy(path: str) -> dict[str, bool]:
    """The flight-policy switches that `bridges.uav` of the Server's `data/server-config.json` carries (only the ones that are saved there). A file that cannot be read, or a
    value that is not true or false, is a ValueError."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"the server configuration file cannot be read: {getattr(error, 'strerror', None) or error}") from error
    section = document.get("bridges") if isinstance(document, dict) else None
    uav = section.get("uav") if isinstance(section, dict) else None
    if not isinstance(uav, dict):
        return {}
    found: dict[str, bool] = {}
    for name in FLIGHT_POLICY_SWITCHES:
        if name in uav:
            if not isinstance(uav[name], bool):
                raise ValueError(f"bridges.uav.{name} in the server configuration must be true or false")
            found[name] = uav[name]
    return found


def tightened(settings: UavSettings, switches: Mapping[str, bool]) -> UavSettings:
    """`settings` with the switches of the server applied in the tightening direction only (see FLIGHT_POLICY_SWITCHES). Returns `settings` itself when nothing changes."""
    envelope, confirmation, phases = settings.envelope, settings.confirmation, settings.phases
    if switches.get("envelopeRequired") is True:
        envelope = dataclasses.replace(envelope, required=True)
    if switches.get("uploadGeofence") is False:
        envelope = dataclasses.replace(envelope, upload_geofence=False)
    if switches.get("confirmationRequired") is True:
        confirmation = dataclasses.replace(confirmation, required=True)
    if switches.get("chainInFlight") is False:
        phases = dataclasses.replace(phases, chain_in_flight=False)
    if (envelope, confirmation, phases) == (settings.envelope, settings.confirmation, settings.phases):
        return settings
    return dataclasses.replace(settings, envelope=envelope, confirmation=confirmation, phases=phases)


def with_server_switches(settings: UavSettings, server_config: str | None, environ: Mapping[str, str] | None = None) -> UavSettings:
    """The settings with the switches of Server Admin, when the host allows remote settings and names the server configuration file.

    The mission upload follows the server in either direction; the flight-policy switches (`envelopeRequired`, `confirmationRequired`, `chainInFlight`, `uploadGeofence`)
    only tighten (see FLIGHT_POLICY_SWITCHES). Without the host switch or the file name, `settings` come back unchanged. When both are given and the file cannot be read
    or a value is damaged, the mission upload and the geofence upload are switched OFF, chaining in flight is switched off and the envelope and the confirmation are
    required: a bridge told to follow the server that cannot hear it does not keep a switch that was on, and takes the stricter side of every policy.
    """
    if not server_config or not remote_settings_allowed(environ):
        return settings
    try:
        value = read_server_mission_upload(server_config)
        policy = read_server_flight_policy(server_config)
    except ValueError:
        value = False
        policy = {"envelopeRequired": True, "confirmationRequired": True, "chainInFlight": False, "uploadGeofence": False}
    result = settings
    if value is not None:
        result = dataclasses.replace(result, mission=dataclasses.replace(result.mission, upload_enabled=value))
    return tightened(result, policy)
