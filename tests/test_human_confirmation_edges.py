# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - tests/test_human_confirmation_edges.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Edge cases of the human confirmation token: every claim that is missing, mistyped or out of range, the clock limits at the exact second, the single use under
racing threads, and the bounded memory of used tokens. A fixed clock and fixed keys: nothing here depends on time or on the network."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hydra_umc_bridge_uav.human_confirmation import (
    MAX_REMEMBERED,
    MAX_TOKEN_CHARS,
    Confirmation,
    ConfirmationError,
    ConfirmationVerifier,
    load_key,
    make_token,
)

KEY = b"k" * 40
NOW = 1_800_000_000.0


def b64(value: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def raw_token(claims: dict, header: dict | None = None, key: bytes = KEY) -> str:
    head, body = b64(header or {"alg": "HS256", "typ": "JWT"}), b64(claims)
    signature = hmac.new(key, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def claims(**changes: object) -> dict:
    base: dict = {"purpose": "human-confirmation", "sub": "ana", "role": "operator", "act": "GO", "job": "J1", "jti": "t1", "iat": int(NOW), "exp": int(NOW) + 60}
    base.update(changes)
    return {name: value for name, value in base.items() if value is not ...}


def verifier(**kwargs: object) -> ConfirmationVerifier:
    return ConfirmationVerifier(KEY, clock=lambda: NOW, **kwargs)  # type: ignore[arg-type]


class KeyFile(unittest.TestCase):
    def test_a_key_file_is_read_and_trimmed(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "key"
            path.write_text("  " + "x" * 32 + "\n", encoding="utf-8")
            self.assertEqual(load_key(str(path)), b"x" * 32)

    def test_a_missing_short_or_binary_key_file_stops_the_start(self) -> None:
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "cannot be read"):
                load_key(str(Path(folder) / "missing"))
            short = Path(folder) / "short"
            short.write_text("x" * 31, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "at least 32"):
                load_key(str(short))
            binary = Path(folder) / "binary"
            binary.write_bytes(b"\xff\xfe\x00\x01" * 20)
            with self.assertRaisesRegex(ValueError, "cannot be read"):
                load_key(str(binary))

    def test_the_verifier_refuses_a_short_key(self) -> None:
        with self.assertRaises(ValueError):
            ConfirmationVerifier(b"short")


class Shape(unittest.TestCase):
    def test_what_is_not_a_token(self) -> None:
        for token in (None, 7, b"abc", "", "   ", [], {}):
            with self.assertRaisesRegex(ConfirmationError, "needs a confirmation"):
                verifier().verify(token, action="GO", job_id="J1")

    def test_a_token_over_the_size_limit(self) -> None:
        with self.assertRaisesRegex(ConfirmationError, "too large"):
            verifier().verify("a" * (MAX_TOKEN_CHARS + 1), action="GO", job_id="J1")
        self.assertEqual(MAX_TOKEN_CHARS, 4096)

    def test_wrong_number_of_parts_and_bad_encoding(self) -> None:
        for token in ("a.b", "a.b.c.d", "a..c", "!!.e30.e30", "W10.e30.e30", "e30.W10.e30", "e30. .e30"):
            with self.assertRaisesRegex(ConfirmationError, "not a JWT"):
                verifier().verify(token, action="GO", job_id="J1")

    def test_a_header_that_is_not_json_or_not_an_object(self) -> None:
        for head in (base64.urlsafe_b64encode(b"\xff\xfe").decode().rstrip("="), base64.urlsafe_b64encode(b"{oops").decode().rstrip("=")):
            with self.assertRaisesRegex(ConfirmationError, "not a JWT"):
                verifier().verify(f"{head}.e30.e30", action="GO", job_id="J1")

    def test_surrounding_white_space_is_ignored(self) -> None:
        token = raw_token(claims())
        self.assertEqual(verifier().verify(f"  {token}\n", action="GO", job_id="J1").operator, "ana")


class Signature(unittest.TestCase):
    def test_only_hs256_is_accepted(self) -> None:
        for alg in ("none", "HS384", "RS256", "", None, 256):
            with self.assertRaisesRegex(ConfirmationError, "only HS256"):
                verifier().verify(raw_token(claims(), header={"alg": alg}), action="GO", job_id="J1")

    def test_another_key_or_a_changed_claim_fails_the_signature(self) -> None:
        with self.assertRaisesRegex(ConfirmationError, "signature"):
            verifier().verify(raw_token(claims(), key=b"z" * 40), action="GO", job_id="J1")
        head, _, signature = raw_token(claims()).split(".")
        forged = f"{head}.{b64(claims(role='administrator'))}.{signature}"
        with self.assertRaisesRegex(ConfirmationError, "signature"):
            verifier().verify(forged, action="GO", job_id="J1")

    def test_an_identity_token_is_not_a_confirmation(self) -> None:
        for purpose in ("identity", "", None, 3, ...):
            with self.assertRaisesRegex(ConfirmationError, "not a human confirmation"):
                verifier().verify(raw_token(claims(purpose=purpose)), action="GO", job_id="J1")


class Claims(unittest.TestCase):
    def test_every_text_claim_is_required_and_not_blank(self) -> None:
        for name in ("sub", "role", "act", "job", "jti"):
            for bad in (..., "", "   ", 5, None, ["x"]):
                with self.assertRaisesRegex(ConfirmationError, f"no valid {name}"):
                    verifier().verify(raw_token(claims(**{name: bad})), action="GO", job_id="J1")

    def test_only_the_two_roles_may_confirm(self) -> None:
        for role in ("administrator", "operator"):
            self.assertEqual(verifier().verify(raw_token(claims(role=role)), action="GO", job_id="J1").role, role)
        for role in ("viewer", "admin", "OPERATOR", "root"):
            with self.assertRaisesRegex(ConfirmationError, "role"):
                verifier().verify(raw_token(claims(role=role)), action="GO", job_id="J1")

    def test_iat_and_exp_must_be_real_numbers(self) -> None:
        for name in ("iat", "exp"):
            for bad in (..., "now", True, False, None, [1]):
                with self.assertRaisesRegex(ConfirmationError, "valid iat and exp"):
                    verifier().verify(raw_token(claims(**{name: bad})), action="GO", job_id="J1")


class Clock(unittest.TestCase):
    def test_expiry_is_exclusive(self) -> None:
        with self.assertRaisesRegex(ConfirmationError, "expired"):
            verifier().verify(raw_token(claims(iat=int(NOW) - 60, exp=int(NOW))), action="GO", job_id="J1")
        verifier().verify(raw_token(claims(iat=int(NOW) - 60, exp=int(NOW) + 1)), action="GO", job_id="J1")

    def test_a_small_skew_into_the_future_is_tolerated_and_a_big_one_is_not(self) -> None:
        verifier().verify(raw_token(claims(iat=int(NOW) + 5, exp=int(NOW) + 60)), action="GO", job_id="J1")
        with self.assertRaisesRegex(ConfirmationError, "future"):
            verifier().verify(raw_token(claims(iat=int(NOW) + 6, exp=int(NOW) + 60)), action="GO", job_id="J1")
        verifier(max_clock_skew_s=30).verify(raw_token(claims(iat=int(NOW) + 30, exp=int(NOW) + 60)), action="GO", job_id="J1")

    def test_the_life_of_the_token_is_capped_at_the_limit(self) -> None:
        verifier(max_ttl_s=120).verify(raw_token(claims(iat=int(NOW), exp=int(NOW) + 120)), action="GO", job_id="J1")
        with self.assertRaisesRegex(ConfirmationError, "longer than"):
            verifier(max_ttl_s=120).verify(raw_token(claims(iat=int(NOW), exp=int(NOW) + 121)), action="GO", job_id="J1")
        with self.assertRaisesRegex(ConfirmationError, "longer than the 10 s"):
            verifier(max_ttl_s=10).verify(raw_token(claims(iat=int(NOW), exp=int(NOW) + 11)), action="GO", job_id="J1")


class Binding(unittest.TestCase):
    def test_the_token_is_for_one_action_and_one_job(self) -> None:
        token = raw_token(claims())
        with self.assertRaisesRegex(ConfirmationError, "is for GO, not for STOP"):
            verifier().verify(token, action="STOP", job_id="J1")
        with self.assertRaisesRegex(ConfirmationError, "another job"):
            verifier().verify(token, action="GO", job_id="J2")
        with self.assertRaisesRegex(ConfirmationError, "is for GO"):
            verifier().verify(token, action="go", job_id="J1")  # no case folding

    def test_make_token_round_trips(self) -> None:
        token = make_token(KEY, operator="ana", role="operator", action="GO", job_id="J1", token_id="t9", issued_at=NOW, ttl_s=30)
        found = verifier().verify(token, action="GO", job_id="J1")
        self.assertEqual((found.operator, found.token_id, found.expires_at), ("ana", "t9", NOW + 30))


class SingleUse(unittest.TestCase):
    def test_verify_does_not_use_the_token_up_but_consume_does(self) -> None:
        guard = verifier()
        token = raw_token(claims())
        first = guard.verify(token, action="GO", job_id="J1")
        guard.verify(token, action="GO", job_id="J1")  # verifying again is harmless
        self.assertTrue(guard.consume(first))
        self.assertFalse(guard.consume(first))
        with self.assertRaisesRegex(ConfirmationError, "already used"):
            guard.verify(token, action="GO", job_id="J1")

    def test_a_used_token_is_forgotten_only_once_it_has_expired(self) -> None:
        now = [NOW]
        guard = ConfirmationVerifier(KEY, clock=lambda: now[0])
        confirmation = Confirmation("ana", "operator", "GO", "J1", "t1", NOW + 60)
        self.assertTrue(guard.consume(confirmation))
        now[0] = NOW + 59
        self.assertFalse(guard.consume(confirmation))
        now[0] = NOW + 60
        self.assertTrue(guard.consume(confirmation))

    def test_racing_threads_get_exactly_one_acceptance(self) -> None:
        guard = verifier()
        confirmation = guard.verify(raw_token(claims()), action="GO", job_id="J1")
        wins: list[bool] = []
        gate = threading.Barrier(16)

        def race() -> None:
            gate.wait()
            wins.append(guard.consume(confirmation))

        threads = [threading.Thread(target=race) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(wins), [False] * 15 + [True])

    def test_memory_is_bounded_and_fails_closed_when_full_of_live_tokens(self) -> None:
        guard = verifier()
        for number in range(MAX_REMEMBERED):
            self.assertTrue(guard.consume(Confirmation("a", "operator", "GO", "J", f"id{number}", NOW + 1000)))
        self.assertFalse(guard.consume(Confirmation("a", "operator", "GO", "J", "one-more", NOW + 1000)))  # refused, nothing forgotten
        self.assertFalse(guard.consume(Confirmation("a", "operator", "GO", "J", "id0", NOW + 1000)))  # an old one is still remembered

    def test_expired_entries_make_room(self) -> None:
        now = [NOW]
        guard = ConfirmationVerifier(KEY, clock=lambda: now[0])
        for number in range(MAX_REMEMBERED):
            guard.consume(Confirmation("a", "operator", "GO", "J", f"id{number}", NOW + 10))
        now[0] = NOW + 11
        self.assertTrue(guard.consume(Confirmation("a", "operator", "GO", "J", "fresh", NOW + 100)))


if __name__ == "__main__":
    unittest.main()
