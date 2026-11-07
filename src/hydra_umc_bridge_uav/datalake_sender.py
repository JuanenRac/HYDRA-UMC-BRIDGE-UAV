# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Send the geotagged capture samples to the data lake
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Post each `datalake_sample()` of a successful capture to `POST /ingest` of HYDRA-UMC-DATALAKE.

Off unless `datalake.send_enabled` is true in `config/uav.json`. The address, the file with the `ingest` token and the timeout are in the same file; an `http://`
address must be a loopback one unless `datalake.allow_remote` says otherwise, and a redirect is never followed (it would carry the token elsewhere). The token is read
from a file and never printed. Like the twin sender it never raises into the bridge: a data lake that is down costs a counter (`failed`), one that says no costs
`refused`. A capture that failed has no sample (see `camera_capture.datalake_sample`), so nothing is sent for it. Not run against a real camera.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Mapping

from .camera_capture import datalake_sample
from .settings import DatalakeSettings
from .twin_sender import _LOOPBACK, _NoRedirect, read_token

PATH = "/ingest"


def check_url(settings: DatalakeSettings) -> str:
    """The address to post to, or a ValueError that says what is wrong with `datalake.url`."""
    parts = urllib.parse.urlsplit(settings.url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("datalake.url must be http://host:port or https://host:port")
    if parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("datalake.url must have no credentials, path or query")
    try:
        parts.port  # noqa: B018 - a port that is not a number raises here
    except ValueError as error:
        raise ValueError("datalake.url has no valid port") from error
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK and not settings.allow_remote:
        raise ValueError("datalake.url is not a loopback address: the token would travel in clear text; set datalake.allow_remote to accept that, or use https://")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, PATH, "", ""))


class DatalakeSender:
    """Posts the sample of a capture event. Call it with the event of `capture_event()`."""

    def __init__(self, settings: DatalakeSettings, token: str, source_id: str, clock: Callable[[], float] = time.time) -> None:
        self.url = check_url(settings)
        self.timeout_s = float(settings.timeout_s)
        self.source_id = source_id
        self._token = token
        self._clock = clock
        self._opener = urllib.request.build_opener(_NoRedirect)
        self.sent = 0
        self.refused = 0
        self.failed = 0
        self.skipped = 0
        self._sequence = 0

    @classmethod
    def from_settings(cls, settings: DatalakeSettings, source_id: str, base_dir: str | Path = ".") -> "DatalakeSender | None":
        """None while `datalake.send_enabled` is off. A file path in the settings is relative to `base_dir`."""
        if not settings.send_enabled:
            return None
        token_path = Path(settings.token_file)
        if not token_path.is_absolute():
            token_path = Path(base_dir) / token_path
        return cls(settings, read_token(token_path), source_id)

    def __call__(self, event: Mapping[str, object]) -> bool:
        """True when the data lake took the sample. A failed capture is skipped without a request."""
        sample = datalake_sample(event, self.source_id, int(self._clock() * 1000), self._sequence)
        if sample is None:
            self.skipped += 1
            return False
        self._sequence += 1
        try:
            request = urllib.request.Request(
                self.url,
                data=json.dumps(sample, allow_nan=False).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            )
            with self._opener.open(request, timeout=self.timeout_s) as answer:
                answer.read(1024)
                if 200 <= answer.status < 300:
                    self.sent += 1
                    return True
                self.failed += 1
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                self.refused += 1
            else:
                self.failed += 1
            error.close()
        except (OSError, ValueError):
            self.failed += 1
        return False
