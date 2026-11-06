# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Tests of the hand-over of the vehicle state to HYDRA-UMC-TWIN
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The state of the vehicle posted to a stand-in for the twin on a real socket: what is sent, with which token, and what a twin that says no or is gone costs."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hydra_umc_bridge_uav.settings import TwinSettings, UavSettings, load_settings
from hydra_umc_bridge_uav.twin_sender import PATH, TwinSender, check_url, read_token
from hydra_umc_bridge_uav.twin_telemetry import TwinFeed

TOKEN = "a-certify-token-of-sixteen+"
HOME = (38.0, -1.0)


class Message:
    """What pymavlink gives: a message with a type and attributes."""

    def __init__(self, kind: str, **fields: object) -> None:
        self._kind = kind
        self.__dict__.update(fields)

    def get_type(self) -> str:
        return self._kind


class FakeTwin:
    """A stand-in for `POST /twin/uav`: answers with `status` and keeps what it was sent."""

    def __init__(self, status: int = 200, location: str | None = None) -> None:
        self.requests: list[dict[str, object]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the name http.server looks for
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                outer.requests.append(
                    {"path": self.path, "authorization": self.headers.get("Authorization"), "type": self.headers.get("Content-Type"), "body": body}
                )
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


def settings(url: str, **extra: object) -> TwinSettings:
    return TwinSettings(send_enabled=True, url=url, token_file="twin.token", min_interval_s=0.0, **extra)  # type: ignore[arg-type]


STATE = {"schema_version": "1.0", "source": "real", "robot_id": "uav-1", "sequence": 1}


class SenderTests(unittest.TestCase):
    def test_a_state_is_posted_to_the_route_of_the_twin_with_the_token(self) -> None:
        twin = FakeTwin(200)
        self.addCleanup(twin.close)
        sender = TwinSender(settings(twin.url), TOKEN)
        sender(STATE)
        self.assertEqual((sender.sent, sender.refused, sender.failed), (1, 0, 0))
        (request,) = twin.requests
        self.assertEqual(request["path"], PATH)
        self.assertEqual(request["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(request["type"], "application/json")
        self.assertEqual(json.loads(request["body"]), STATE)  # type: ignore[arg-type]

    def test_an_answer_that_says_no_is_refused_and_a_server_error_is_a_failure(self) -> None:
        for status, expected in ((409, (0, 1, 0)), (422, (0, 1, 0)), (401, (0, 1, 0)), (503, (0, 0, 1))):
            twin = FakeTwin(status)
            self.addCleanup(twin.close)
            sender = TwinSender(settings(twin.url), TOKEN)
            sender(STATE)
            self.assertEqual((sender.sent, sender.refused, sender.failed), expected, status)

    def test_a_twin_that_is_gone_costs_a_counter_and_never_raises(self) -> None:
        twin = FakeTwin(200)
        url = twin.url
        twin.close()
        sender = TwinSender(settings(url, timeout_s=0.5), TOKEN)
        sender(STATE)
        sender(STATE)
        self.assertEqual((sender.sent, sender.refused, sender.failed), (0, 0, 2))

    def test_a_state_that_cannot_be_written_as_json_is_a_failure_not_an_exception(self) -> None:
        twin = FakeTwin(200)
        self.addCleanup(twin.close)
        sender = TwinSender(settings(twin.url), TOKEN)
        sender({"value": float("nan")})
        self.assertEqual(sender.failed, 1)
        self.assertEqual(twin.requests, [])

    def test_a_redirect_is_never_followed_so_the_token_stays_where_it_was_given(self) -> None:
        elsewhere = FakeTwin(200)
        self.addCleanup(elsewhere.close)
        twin = FakeTwin(307, location=elsewhere.url + PATH)
        self.addCleanup(twin.close)
        sender = TwinSender(settings(twin.url), TOKEN)
        sender(STATE)
        self.assertEqual(sender.failed, 1)
        self.assertEqual(len(twin.requests), 1)
        self.assertEqual(elsewhere.requests, [])

    def test_the_feed_hands_each_state_to_the_twin(self) -> None:
        twin = FakeTwin(200)
        self.addCleanup(twin.close)
        sender = TwinSender(settings(twin.url), TOKEN)
        feed = TwinFeed(*HOME, TwinSettings(min_interval_s=0.0), sender, clock=lambda: 10.0)
        feed.observe(Message("GLOBAL_POSITION_INT", lat=380000000, lon=-10000000, relative_alt=12500))
        feed.observe(Message("ATTITUDE", roll=0.0, pitch=0.0, yaw=1.0))
        self.assertEqual(sender.sent, 1)
        document = json.loads(twin.requests[0]["body"])  # type: ignore[arg-type]
        self.assertEqual(document["poses"][0]["name"], "vehicle")
        self.assertEqual(document["source"], "real")


class AddressTests(unittest.TestCase):
    def test_only_a_loopback_http_address_is_accepted_unless_the_configuration_allows_more(self) -> None:
        self.assertEqual(check_url(settings("http://127.0.0.1:8111")), "http://127.0.0.1:8111/twin/uav")
        self.assertEqual(check_url(settings("http://localhost:8111/")), "http://localhost:8111/twin/uav")
        self.assertEqual(check_url(settings("https://twin.lan:8443")), "https://twin.lan:8443/twin/uav")
        with self.assertRaises(ValueError):
            check_url(settings("http://192.168.0.10:8111"))
        self.assertEqual(check_url(settings("http://192.168.0.10:8111", allow_remote=True)), "http://192.168.0.10:8111/twin/uav")

    def test_an_address_with_a_path_credentials_or_another_scheme_is_refused(self) -> None:
        for bad in ("", "127.0.0.1:8111", "ftp://127.0.0.1", "http://", "http://u:p@127.0.0.1:8111", "http://127.0.0.1:8111/other", "http://127.0.0.1:8111/?a=1", "http://127.0.0.1:99999"):
            with self.assertRaises(ValueError, msg=bad):
                check_url(settings(bad))


class SettingsTests(unittest.TestCase):
    def test_the_hand_over_is_off_by_default_and_needs_an_address_and_a_token_file_when_on(self) -> None:
        defaults = TwinSettings()
        self.assertFalse(defaults.send_enabled)
        self.assertEqual(defaults.problems(), [])
        self.assertIsNone(TwinSender.from_settings(defaults))
        on_without_anything = TwinSettings(send_enabled=True)
        self.assertTrue(on_without_anything.problems())
        self.assertIn("twin.token_file", " ".join(on_without_anything.problems()))
        self.assertEqual(settings("http://127.0.0.1:8111").problems(), [])
        for bad in (dict(timeout_s=0), dict(timeout_s=61), dict(timeout_s=float("nan")), dict(send_enabled="yes"), dict(allow_remote=1), dict(url=5)):
            self.assertTrue(TwinSettings(**bad).problems(), bad)  # type: ignore[arg-type]

    def test_the_file_of_the_settings_reads_the_new_values_and_names_a_wrong_one(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "uav.json"
            path.write_text(json.dumps({"twin": {"send_enabled": True, "url": "http://127.0.0.1:8111", "token_file": "t.token", "timeout_s": 1.5}}), encoding="utf-8")
            loaded = load_settings(path)
            self.assertIsInstance(loaded, UavSettings)
            self.assertTrue(loaded.twin.send_enabled)
            self.assertEqual(loaded.twin.timeout_s, 1.5)
            path.write_text(json.dumps({"twin": {"send_enabled": True, "url": "http://10.0.0.5:8111", "token_file": "t.token"}}), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                load_settings(path)
            self.assertIn("loopback", str(caught.exception))
            path.write_text(json.dumps({"twin": {"port": 1}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)

    def test_the_token_is_read_from_a_file_relative_to_the_folder_given_and_is_never_empty(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "twin.token").write_text(TOKEN + "\n", encoding="utf-8")
            sender = TwinSender.from_settings(settings("http://127.0.0.1:8111"), folder)
            self.assertIsNotNone(sender)
            self.assertEqual(read_token(Path(folder) / "twin.token"), TOKEN)
            (Path(folder) / "empty.token").write_text("  \n", encoding="utf-8")
            (Path(folder) / "ctrl.token").write_text("ab\x07cd", encoding="utf-8")
            for name in ("empty.token", "ctrl.token", "absent.token"):
                with self.assertRaises(ValueError, msg=name):
                    read_token(Path(folder) / name)
            self.assertNotIn(TOKEN, repr(sender.__dict__.get("url")))  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
