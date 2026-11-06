# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Hand the state of the vehicle to HYDRA-UMC-TWIN
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Send each `TwinState` that `TwinFeed` builds to `POST /twin/uav` of HYDRA-UMC-TWIN (started there with `serve --uav-input`).

Off unless `twin.send_enabled` is true in `config/uav.json`. The address, the file with the token of the twin's interlock routes and the timeout are in the
same file; an `http://` address must be a loopback one unless `twin.allow_remote` says otherwise, because the token would travel in clear text, and an `https://`
address is checked against the certificates of the system. The token is read from a file and is never printed. A redirect is never followed (it would carry the
token to another place).

The sender never raises into the bridge: a twin that is down, slow or refusing costs a counter (`failed`, `refused`) and the vehicle goes on. `refused` counts the
answers of the twin that say no (an old sequence, a state it does not accept); `failed` counts what did not get an answer at all. Not run against a real twin
or a real vehicle.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .settings import TwinSettings

PATH = "/twin/uav"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        return None


def check_url(settings: TwinSettings) -> str:
    """The address to post to, or a ValueError that says what is wrong with `twin.url`."""
    parts = urllib.parse.urlsplit(settings.url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("twin.url must be http://host:port or https://host:port")
    if parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("twin.url must have no credentials, path or query")
    try:
        parts.port  # noqa: B018 - a port that is not a number raises here
    except ValueError as error:
        raise ValueError("twin.url has no valid port") from error
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK and not settings.allow_remote:
        raise ValueError("twin.url is not a loopback address: the token would travel in clear text; set twin.allow_remote to accept that, or use https://")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, PATH, "", ""))


def read_token(path: str | Path) -> str:
    """The token in the file, without the line break; a ValueError when it cannot be read or is not usable."""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as error:
        raise ValueError(f"twin.token_file cannot be read: {getattr(error, 'strerror', None) or error}") from error
    if not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("twin.token_file is empty or has a control character")
    return text


class TwinSender:
    """Posts a state to the twin. Use it as the `send` of `TwinFeed`."""

    def __init__(self, settings: TwinSettings, token: str) -> None:
        self.url = check_url(settings)
        self.timeout_s = float(settings.timeout_s)
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect)
        self.sent = 0
        self.refused = 0
        self.failed = 0

    @classmethod
    def from_settings(cls, settings: TwinSettings, base_dir: str | Path = ".") -> "TwinSender | None":
        """None while `twin.send_enabled` is off. A file path in the settings is relative to `base_dir`."""
        if not settings.send_enabled:
            return None
        token_path = Path(settings.token_file)
        if not token_path.is_absolute():
            token_path = Path(base_dir) / token_path
        return cls(settings, read_token(token_path))

    def __call__(self, state: dict[str, object]) -> None:
        try:
            request = urllib.request.Request(
                self.url,
                data=json.dumps(state, allow_nan=False).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            )
            with self._opener.open(request, timeout=self.timeout_s) as answer:
                answer.read(1024)
                if 200 <= answer.status < 300:
                    self.sent += 1
                else:
                    self.failed += 1
        except urllib.error.HTTPError as error:
            # the twin answered and said no (409 old sequence, 422 not accepted, 400 invalid, 401 token...): not a link failure
            if 400 <= error.code < 500:
                self.refused += 1
            else:
                self.failed += 1
            error.close()
        except (OSError, ValueError):
            self.failed += 1

