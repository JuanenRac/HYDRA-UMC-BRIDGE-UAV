# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Tests of the hand-over of the capture samples to the data lake
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The sample of every successful capture posted to a stand-in for `POST /ingest` on a real socket: what is sent, with which token, and what a data lake that says no or is gone costs."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from hydra_umc_bridge_uav.camera_capture import capture_event
from hydra_umc_bridge_uav.datalake_sender import PATH, DatalakeSender, check_url
from hydra_umc_bridge_uav.settings import DatalakeSettings, UavSettings, load_settings

TOKEN = "an-ingest-token-of-sixteen"


class Picture:
    def __init__(self, **fields: object) -> None:
        self.lat, self.lon, self.alt, self.relative_alt = 380010000, -10000000, 120000, 30000
        self.image_index, self.capture_result, self.camera_id, self.time_utc = 4, 1, 100, 1_800_000_000_000_000
        self.file_url, self.q = "file:///IMG_0004.JPG", [1.0, 0.0, 0.0, 0.0]
        self.__dict__.update(fields)


class FakeLake:
    def __init__(self, status: int = 200, location: str | None = None) -> None:
        self.requests: list[dict[str, object]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                outer.requests.append({"path": self.path, "authorization": self.headers.get("Authorization"), "body": body})
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def settings(url: str, **extra: object) -> DatalakeSettings:
    return DatalakeSettings(send_enabled=True, url=url, token_file="lake.token", **extra)  # type: ignore[arg-type]


GOOD = capture_event(Picture())
BAD = capture_event(Picture(capture_result=0))


class SenderTests(unittest.TestCase):
    def test_a_successful_capture_is_posted_to_ingest_with_the_token(self) -> None:
        lake = FakeLake(200)
        self.addCleanup(lake.close)
        sender = DatalakeSender(settings(lake.url), TOKEN, "uav-7", clock=lambda: 5.0)
        self.assertTrue(sender(GOOD))
        self.assertEqual((sender.sent, sender.refused, sender.failed, sender.skipped), (1, 0, 0, 0))
        (request,) = lake.requests
        self.assertEqual(request["path"], PATH)
        self.assertEqual(request["authorization"], f"Bearer {TOKEN}")
        sample = json.loads(request["body"])  # type: ignore[arg-type]
        self.assertEqual((sample["sourceId"], sample["kind"], sample["sequence"]), ("uav-7", "uav_image", 0))
        self.assertEqual(sample["timestamp"], 1_800_000_000_000)
        self.assertAlmostEqual(sample["fields"]["lat"], 38.001)

    def test_a_failed_capture_sends_nothing(self) -> None:
        lake = FakeLake(200)
        self.addCleanup(lake.close)
        sender = DatalakeSender(settings(lake.url), TOKEN, "uav")
        self.assertFalse(sender(BAD))
        self.assertEqual((sender.sent, sender.skipped, lake.requests), (0, 1, []))

    def test_the_sequence_grows_with_each_sample_and_not_with_a_skipped_one(self) -> None:
        lake = FakeLake(200)
        self.addCleanup(lake.close)
        sender = DatalakeSender(settings(lake.url), TOKEN, "uav")
        sender(GOOD)
        sender(BAD)
        sender(GOOD)
        self.assertEqual([json.loads(r["body"])["sequence"] for r in lake.requests], [0, 1])  # type: ignore[arg-type]

    def test_an_answer_that_says_no_is_refused_and_a_server_error_is_a_failure(self) -> None:
        for status, expected in ((400, (0, 1, 0)), (401, (0, 1, 0)), (413, (0, 1, 0)), (503, (0, 0, 1))):
            lake = FakeLake(status)
            self.addCleanup(lake.close)
            sender = DatalakeSender(settings(lake.url), TOKEN, "uav")
            self.assertFalse(sender(GOOD))
            self.assertEqual((sender.sent, sender.refused, sender.failed), expected, status)

    def test_a_data_lake_that_is_gone_costs_a_counter_and_never_raises(self) -> None:
        lake = FakeLake(200)
        url = lake.url
        lake.close()
        sender = DatalakeSender(settings(url, timeout_s=0.5), TOKEN, "uav")
        self.assertFalse(sender(GOOD))
        self.assertEqual((sender.sent, sender.failed), (0, 1))

    def test_a_redirect_is_never_followed(self) -> None:
        elsewhere = FakeLake(200)
        self.addCleanup(elsewhere.close)
        lake = FakeLake(307, location=elsewhere.url + PATH)
        self.addCleanup(lake.close)
        sender = DatalakeSender(settings(lake.url), TOKEN, "uav")
        sender(GOOD)
        self.assertEqual((len(lake.requests), elsewhere.requests), (1, []))

    def test_from_settings_is_none_while_off_and_reads_the_token_file_when_on(self) -> None:
        self.assertIsNone(DatalakeSender.from_settings(DatalakeSettings(), "uav"))
        lake = FakeLake(200)
        self.addCleanup(lake.close)
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "lake.token").write_text(TOKEN + "\n", encoding="utf-8")
            sender = DatalakeSender.from_settings(settings(lake.url), "uav", folder)
            self.assertTrue(sender(GOOD))  # type: ignore[misc]
        self.assertEqual(lake.requests[0]["authorization"], f"Bearer {TOKEN}")


class AddressAndSettingsTests(unittest.TestCase):
    def test_only_a_loopback_http_address_is_accepted_unless_the_configuration_allows_more(self) -> None:
        self.assertEqual(check_url(settings("http://127.0.0.1:8095")), "http://127.0.0.1:8095/ingest")
        for bad in ("ftp://127.0.0.1", "http://", "http://user:pw@127.0.0.1:1", "http://127.0.0.1:1/x", "http://127.0.0.1:1?q=1", "http://127.0.0.1:notaport", "http://lake.example:8095"):
            with self.assertRaises(ValueError, msg=bad):
                check_url(settings(bad))
        remote = urlparse(check_url(settings("http://lake.example:8095", allow_remote=True)))
        self.assertEqual((remote.scheme, remote.hostname, remote.port), ("http", "lake.example", 8095))
        secure = urlparse(check_url(settings("https://lake.example")))
        self.assertEqual((secure.scheme, secure.hostname), ("https", "lake.example"))

    def test_the_section_is_off_by_default_and_checked_when_on(self) -> None:
        self.assertEqual(DatalakeSettings().problems(), [])
        self.assertFalse(DatalakeSettings().send_enabled)
        self.assertTrue(any("token_file" in p for p in DatalakeSettings(send_enabled=True, url="http://127.0.0.1:1").problems()))
        self.assertTrue(DatalakeSettings(timeout_s=0).problems())
        self.assertTrue(DatalakeSettings(send_enabled="yes").problems())  # type: ignore[arg-type]

    def test_a_file_with_an_unknown_key_in_the_new_sections_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "uav.json")
            path.write_text(json.dumps({"datalake": {"nope": 1}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)
            path.write_text(json.dumps({"job_entry": {"enabled": True}, "datalake": {"send_enabled": False}}), encoding="utf-8")
            self.assertTrue(load_settings(path).job_entry.enabled)
        self.assertEqual(UavSettings().datalake, DatalakeSettings())


if __name__ == "__main__":
    unittest.main()
