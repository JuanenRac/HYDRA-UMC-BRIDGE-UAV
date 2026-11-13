# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Human confirmation signed by the server (one use)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The confirmation of an operator that a flight request may go ahead: a short token signed by the SERVER, valid once.

A job on `cmd/job` says what to do; it does not prove that a person agreed to it. For the requests that start or continue a flight, the job carries, next to
the usual fields, `operator_confirmation`: a JWT (RFC 7519) signed with HS256 with the key of the SERVER (the same family of token as the identity token that
CONNECTOR-HUB checks), made for ONE action:

* `purpose` is exactly `human-confirmation` (an identity token or any other token of the server is not a confirmation);
* `sub` names the operator and `role` is their role (`operator` or `administrator`);
* `iat` and `exp` bound the life of the token, and `exp - iat` may not exceed `confirmation.max_ttl_s` (a long-lived token is refused even if it is still valid);
* `act` is the request it confirms (`TAKEOFF`, `GOTO_WAYPOINT`, ...) and `job` is the `job_id` it confirms, so a token made for one flight request cannot be replayed on
  another;
* `jti` is its unique number, remembered after use: the same token is accepted ONCE.

The algorithm is exactly HS256 (`none` and every other algorithm are refused). The key is read from a file only the service can read, at least 32 characters. Pure
standard library. This module only checks; the SERVER is what issues the token, after the operator confirmed on a screen. Nothing has run against a real server here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PURPOSE = "human-confirmation"
ROLES = ("operator", "administrator")
MIN_KEY_CHARS = 32
MAX_TOKEN_CHARS = 4096
MAX_REMEMBERED = 4096


class ConfirmationError(ValueError):
    """The token is not a valid confirmation; the message says why."""


@dataclass(frozen=True)
class Confirmation:
    operator: str
    role: str
    action: str
    job_id: str
    token_id: str
    expires_at: float


def load_key(path: str) -> bytes:
    """The HS256 key from a file. A file that is missing, unreadable or shorter than 32 characters is a ValueError (the start of the service stops)."""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as error:
        raise ValueError(f"the confirmation key file cannot be read: {getattr(error, 'strerror', None) or error}") from error
    if len(text) < MIN_KEY_CHARS:
        raise ValueError(f"the confirmation key must have at least {MIN_KEY_CHARS} characters")
    return text.encode("utf-8")


def _unb64(part: str) -> bytes:
    if not part or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in part):
        raise ConfirmationError("the token is not a JWT")
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (binascii.Error, ValueError) as error:
        raise ConfirmationError("the token is not a JWT") from error


def _json(part: str) -> dict:
    try:
        value = json.loads(_unb64(part))
    except (ValueError, UnicodeDecodeError) as error:
        raise ConfirmationError("the token is not a JWT") from error
    if not isinstance(value, dict):
        raise ConfirmationError("the token is not a JWT")
    return value


def make_token(key: bytes, *, operator: str, role: str, action: str, job_id: str, token_id: str, issued_at: float, ttl_s: float) -> str:
    """A token as the SERVER makes it. Used by the tests and by a simulator of the server; the bridge itself only checks."""

    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=").decode()

    head = part({"alg": "HS256", "typ": "JWT"})
    body = part({"purpose": PURPOSE, "sub": operator, "role": role, "act": action, "job": job_id, "jti": token_id, "iat": int(issued_at), "exp": int(issued_at + ttl_s)})
    signature = hmac.new(key, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


class ConfirmationVerifier:
    """Checks the confirmation of a request and remembers the tokens it has accepted, so each one works once."""

    def __init__(self, key: bytes, *, max_ttl_s: float = 120.0, max_clock_skew_s: float = 5.0, clock: Callable[[], float] = time.time) -> None:
        if len(key) < MIN_KEY_CHARS:
            raise ValueError(f"the confirmation key must have at least {MIN_KEY_CHARS} characters")
        self._key = key
        self.max_ttl_s = float(max_ttl_s)
        self.max_clock_skew_s = float(max_clock_skew_s)
        self._clock = clock
        self._used: dict[str, float] = {}
        self._lock = threading.Lock()

    def verify(self, token: object, *, action: str, job_id: str) -> Confirmation:
        """The confirmation in `token` for exactly this action and job, or ConfirmationError. It does NOT use the token up: call `consume` once the request is accepted."""
        if not isinstance(token, str) or not token.strip():
            raise ConfirmationError("this request needs a confirmation of the operator signed by the server, and none came with it")
        if len(token) > MAX_TOKEN_CHARS:
            raise ConfirmationError("the confirmation token is too large")
        pieces = token.strip().split(".")
        if len(pieces) != 3:
            raise ConfirmationError("the token is not a JWT")
        head, claims = _json(pieces[0]), _json(pieces[1])
        if head.get("alg") != "HS256":
            raise ConfirmationError("the token algorithm is not accepted (only HS256)")
        expected = hmac.new(self._key, f"{pieces[0]}.{pieces[1]}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(pieces[2])):
            raise ConfirmationError("the signature of the confirmation is not valid")
        if claims.get("purpose") != PURPOSE:
            raise ConfirmationError("the token is not a human confirmation")
        for name in ("sub", "role", "act", "job", "jti"):
            if not isinstance(claims.get(name), str) or not claims[name].strip():
                raise ConfirmationError(f"the confirmation has no valid {name}")
        if claims["role"] not in ROLES:
            raise ConfirmationError("the role of the confirmation is not one that may confirm a flight")
        issued, expires = claims.get("iat"), claims.get("exp")
        if isinstance(issued, bool) or not isinstance(issued, (int, float)) or isinstance(expires, bool) or not isinstance(expires, (int, float)):
            raise ConfirmationError("the confirmation has no valid iat and exp")
        now = self._clock()
        if expires <= now:
            raise ConfirmationError("the confirmation has expired")
        if issued > now + self.max_clock_skew_s:
            raise ConfirmationError("the confirmation was issued in the future")
        if expires - issued > self.max_ttl_s:
            raise ConfirmationError(f"the confirmation lives longer than the {self.max_ttl_s:g} s that are allowed")
        if claims["act"] != action:
            raise ConfirmationError(f"the confirmation is for {claims['act']}, not for {action}")
        if claims["job"] != job_id:
            raise ConfirmationError("the confirmation is for another job")
        with self._lock:
            if self._used.get(claims["jti"], 0.0) > now:
                raise ConfirmationError("this confirmation was already used")
        return Confirmation(claims["sub"], claims["role"], claims["act"], claims["job"], claims["jti"], float(expires))

    def consume(self, confirmation: Confirmation) -> bool:
        """Remember the token as used. False when it was already used (two requests raced for the same token): the second one must be refused."""
        now = self._clock()
        with self._lock:
            if self._used.get(confirmation.token_id, 0.0) > now:
                return False
            if len(self._used) >= MAX_REMEMBERED:
                for key in [k for k, until in self._used.items() if until <= now]:
                    del self._used[key]
                if len(self._used) >= MAX_REMEMBERED:
                    return False  # full of live tokens: fail closed rather than forget one
            self._used[confirmation.token_id] = confirmation.expires_at
            return True
