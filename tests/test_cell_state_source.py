# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Tests of the cell state source and the persistence of one-use memories
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The `cell_state` section of the settings, the file that keeps the used confirmations and the file that keeps the idempotency keys."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hydra_umc_sdk.bridge_contract import CellState

from hydra_umc_bridge_uav import cli
from hydra_umc_bridge_uav.human_confirmation import ConfirmationVerifier, make_token
from hydra_umc_bridge_uav.settings import CellStateSettings, load_settings

KEY = b"c" * 40


class SettingsTests(unittest.TestCase):
    def write(self, folder: str, body: dict) -> Path:
        path = Path(folder) / "uav.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_the_defaults_keep_the_behaviour_of_before(self):
        settings = load_settings(None)
        self.assertEqual(settings.cell_state.source, "none")
        self.assertEqual((settings.job_entry.idempotency_file, settings.confirmation.used_file), ("", ""))

    def test_the_shipped_configuration_declares_them_all_at_their_defaults(self):
        shipped = Path(__file__).resolve().parents[1] / "config" / "uav.json"
        settings = load_settings(shipped)
        self.assertEqual(settings.cell_state, CellStateSettings())
        self.assertEqual((settings.job_entry.idempotency_file, settings.confirmation.used_file), ("", ""))

    def test_bad_values_are_refused(self):
        bad = [
            {"cell_state": {"source": "gpio"}},
            {"cell_state": {"source": "file"}},
            {"cell_state": {"source": "safety_zones", "path": "p"}},
            {"cell_state": {"source": "safety_zones", "path": "p", "url": "http://x/", "key_file": "k"}},
            {"cell_state": {"source": "safety_zones", "path": "p", "algorithm": "Ed25519"}},
            {"cell_state": {"source": "fixed", "value": "ready"}},
            {"cell_state": {"max_age_seconds": 0}},
            {"cell_state": {"algorithm": "RSA"}},
            {"cell_state": {"surprise": 1}},
            {"job_entry": {"idempotency_file": 3}},
            {"confirmation": {"used_file": 3}},
        ]
        for body in bad:
            with tempfile.TemporaryDirectory() as folder, self.subTest(body), self.assertRaises(ValueError):
                load_settings(self.write(folder, body))

    def test_a_source_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            body = {"cell_state": {"source": "safety_zones", "url": "https://zones.local/state", "key_file": "k", "max_age_seconds": 3}}
            settings = load_settings(self.write(folder, body))
        self.assertEqual((settings.cell_state.source, settings.cell_state.max_age_seconds), ("safety_zones", 3))


class ConfirmationMemoryTests(unittest.TestCase):
    def token(self, token_id: str, now: float) -> str:
        return make_token(KEY, operator="ana", role="operator", action="TAKEOFF", job_id="job-1", token_id=token_id, issued_at=now, ttl_s=60)

    def test_a_used_token_stays_used_after_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            used = str(Path(folder) / "used.json")
            now = 1_800_000_000.0
            first = ConfirmationVerifier(KEY, clock=lambda: now, used_file=used)
            confirmation = first.verify(self.token("t-1", now), action="TAKEOFF", job_id="job-1")
            self.assertTrue(first.consume(confirmation))
            second = ConfirmationVerifier(KEY, clock=lambda: now + 5, used_file=used)
            with self.assertRaises(Exception) as caught:
                second.verify(self.token("t-1", now), action="TAKEOFF", job_id="job-1")
            self.assertIn("already used", str(caught.exception))
            self.assertFalse(second.consume(confirmation))

    def test_expired_entries_are_forgotten_when_the_file_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            used = Path(folder) / "used.json"
            used.write_text(json.dumps({"old": 10.0, "live": 9_000_000_000.0}), encoding="utf-8")
            verifier = ConfirmationVerifier(KEY, clock=lambda: 1_800_000_000.0, used_file=str(used))
            self.assertEqual(set(verifier._used), {"live"})

    def test_a_damaged_file_stops_the_start_instead_of_forgetting_the_tokens(self):
        with tempfile.TemporaryDirectory() as folder:
            used = Path(folder) / "used.json"
            for text in ("not json", "[]", '{"a": "x"}', '{"a": true}'):
                used.write_text(text, encoding="utf-8")
                with self.subTest(text), self.assertRaises(ValueError):
                    ConfirmationVerifier(KEY, used_file=str(used))

    def test_a_token_that_cannot_be_remembered_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            used = str(Path(folder) / "used.json")
            now = 1_800_000_000.0
            verifier = ConfirmationVerifier(KEY, clock=lambda: now, used_file=used)
            confirmation = verifier.verify(self.token("t-2", now), action="TAKEOFF", job_id="job-1")
            with mock.patch("os.replace", side_effect=OSError("disk full")):
                self.assertFalse(verifier.consume(confirmation))
            self.assertNotIn("t-2", verifier._used)

    def test_without_a_file_nothing_is_written(self):
        verifier = ConfirmationVerifier(KEY)
        self.assertEqual(verifier._used_file, "")


class WiringTests(unittest.TestCase):
    def opened(self, body: dict, folder: str):
        path = Path(folder) / "uav.json"
        path.write_text(json.dumps({"job_entry": {"enabled": True}, **body}), encoding="utf-8")
        captured: dict = {}

        def opener(host, port, job_entry):
            captured["entry"] = job_entry
            return object()

        cli._open_job_entry(mock.Mock(settings=str(path), mqtt_port=1883, server_config=None), "broker", None, opener)
        return captured["entry"]

    def test_without_a_source_the_cell_reads_ready_as_before_and_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            entry = self.opened({}, folder)
            self.assertEqual(entry._cell_state(), CellState.READY)
            self.assertEqual(entry._cell_source, "none")

    def test_a_file_source_decides_the_cell_state_and_a_stale_file_is_a_safe_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            cell = Path(folder) / "cell.txt"
            cell.write_text("INHIBITED\n", encoding="utf-8")
            entry = self.opened({"cell_state": {"source": "file", "path": str(cell), "max_age_seconds": 30}}, folder)
            self.assertEqual(entry._cell_state(), CellState.INHIBITED)
            self.assertEqual(entry._cell_source, "file")
            cell.unlink()
            self.assertEqual(entry._cell_state(), CellState.SAFE_STOP)

    def test_the_answer_of_a_job_carries_the_source(self):
        with tempfile.TemporaryDirectory() as folder:
            entry = self.opened({"cell_state": {"source": "fixed", "value": "SAFE_STOP"}}, folder)
            job = {"job_id": "j-1", "idempotency_key": "k-1", "origin": "test", "phase": "PROCESS", "machine_state": "IDLE", "parameters": {}}
            answer = entry.on_message(mock.Mock(), "hydra/bridges/uav/cmd/job", json.dumps(job).encode())
            self.assertEqual(answer["cell_source"], "fixed")
            self.assertFalse(answer["allowed"])

    def test_a_source_that_cannot_be_built_stops_the_start(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "short.key"
            key.write_text("tiny", encoding="utf-8")
            body = {"cell_state": {"source": "safety_zones", "path": str(Path(folder) / "state.json"), "key_file": str(key)}}
            with self.assertRaises(ValueError):
                self.opened(body, folder)

    def test_the_idempotency_file_is_created_when_it_is_named(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / "keys.db"
            entry = self.opened({"job_entry": {"enabled": True, "idempotency_file": str(store)}}, folder)
            self.assertTrue(store.exists())
            entry._coordinator._store.close()


if __name__ == "__main__":
    unittest.main()
