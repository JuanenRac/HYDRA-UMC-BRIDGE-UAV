# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Command line tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""`run` keeps the bridge's heartbeat going on a link that is opened for it. The link is a recorder."""

import io
import json
import threading
import unittest

from hydra_umc_bridge_uav import cli
from hydra_umc_bridge_uav.gcs_heartbeat import MAV_TYPE_GCS


class Sink:
    def __init__(self):
        self.calls = []
        self.closed = False

    def heartbeat_send(self, *args):
        self.calls.append(args)

    def close(self):
        self.closed = True


def run(argv, opener=None, stop=None):
    args = cli.build_parser().parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(args, opener=opener, stop=stop, out=out, err=err, install_signals=False)
    return code, out.getvalue(), err.getvalue()


class RunTests(unittest.TestCase):
    def test_it_opens_the_link_beats_as_a_ground_station_and_stops_after_the_duration(self):
        sink = Sink()
        seen = {}

        def opener(connection, heartbeat_timeout_seconds):
            seen["connection"], seen["timeout"] = connection, heartbeat_timeout_seconds
            return sink

        code, out, err = run(["run", "--connection", "udpin:0.0.0.0:14550", "--heartbeat-hz", "10", "--duration", "0.35", "--status-every", "0.1"], opener)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(seen, {"connection": "udpin:0.0.0.0:14550", "timeout": 10.0})
        self.assertGreaterEqual(len(sink.calls), 2)
        self.assertTrue(all(call[0] == MAV_TYPE_GCS for call in sink.calls))
        self.assertTrue(sink.closed)
        lines = out.strip().splitlines()
        self.assertTrue(lines[0].startswith("HEARTBEAT_RUNNING connection=udpin:0.0.0.0:14550 rate_hz=10"))
        self.assertEqual(lines[-1], "HEARTBEAT_STOPPED")
        status = json.loads(lines[-2])["heartbeat"]
        self.assertFalse(status["running"])
        self.assertGreaterEqual(status["sent"], 2)
        self.assertEqual(status["failed"], 0)

    def test_a_stop_request_ends_it(self):
        stop = threading.Event()
        threading.Timer(0.2, stop.set).start()
        code, out, _ = run(["run", "--connection", "x", "--heartbeat-hz", "10"], lambda *a, **k: Sink(), stop)
        self.assertEqual(code, 0)
        self.assertIn("HEARTBEAT_STOPPED", out)

    def test_a_link_that_cannot_be_opened_is_reported_not_raised(self):
        def opener(connection, heartbeat_timeout_seconds):
            raise RuntimeError("no MAVLink HEARTBEAT from x within 10s")

        code, out, err = run(["run", "--connection", "x"], opener)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("could not be opened", err)

    def test_a_link_that_takes_no_beat_is_a_failure(self):
        class Broken(Sink):
            def heartbeat_send(self, *args):
                raise OSError("link down")

        code, out, _ = run(["run", "--connection", "x", "--heartbeat-hz", "10", "--duration", "0.25"], lambda *a, **k: Broken())
        self.assertEqual(code, 1)
        self.assertIn('"failed"', out)

    def test_bad_options_are_refused_before_any_link_is_opened(self):
        opened = []

        def opener(*a, **k):
            opened.append(a)
            return Sink()

        for argv in (["run", "--connection", "x", "--heartbeat-hz", "0"], ["run", "--connection", "x", "--heartbeat-hz", "50"],
                     ["run", "--connection", "x", "--heartbeat-hz", "fast"], ["run", "--connection", "x", "--duration", "-1"],
                     ["run", "--connection", "x", "--heartbeat-timeout", "0"]):
            code, _, err = run(argv, opener)
            self.assertEqual(code, 2, argv)
            self.assertIn("error", err)
        self.assertEqual(opened, [])

    def test_the_rate_falls_back_to_the_environment_variable(self):
        import os
        previous = os.environ.get(cli.ENVIRONMENT_VARIABLE)
        os.environ[cli.ENVIRONMENT_VARIABLE] = "0"
        try:
            code, _, err = run(["run", "--connection", "x"], lambda *a, **k: Sink())
        finally:
            if previous is None:
                os.environ.pop(cli.ENVIRONMENT_VARIABLE, None)
            else:
                os.environ[cli.ENVIRONMENT_VARIABLE] = previous
        self.assertEqual(code, 2)
        self.assertIn("nothing to do", err)

    def test_main_needs_a_command_and_a_connection(self):
        self.assertNotEqual(cli.main([]), 0)
        self.assertNotEqual(cli.main(["run"]), 0)


class SigningOptionTests(unittest.TestCase):
    def test_a_key_file_reaches_the_opener_and_a_bad_one_is_a_usage_error(self):
        import os
        import tempfile

        seen = {}

        def opener(connection, heartbeat_timeout_seconds, **signing):
            seen.update(signing)
            return Sink()

        with tempfile.TemporaryDirectory() as folder:
            key = os.path.join(folder, "uav.key")
            with open(key, "w", encoding="ascii") as handle:
                handle.write(bytes(range(32)).hex())
            code, _, _ = run(["run", "--connection", "x", "--duration", "0.2", "--signing-key-file", key, "--signing-allow-unsigned"], opener)
            self.assertEqual(code, 0)
            self.assertEqual(seen, {"signing_key": bytes(range(32)), "allow_unsigned": True})
            seen.clear()
            code, _, _ = run(["run", "--connection", "x", "--duration", "0.2", "--signing-key-file", key], opener)
            self.assertEqual(seen["allow_unsigned"], False)
            bad = os.path.join(folder, "bad.key")
            with open(bad, "w", encoding="ascii") as handle:
                handle.write("nope")
            code, _, err = run(["run", "--connection", "x", "--signing-key-file", bad], opener)
            self.assertEqual(code, 2)
            self.assertNotIn("nope", err)

    def test_without_a_key_the_opener_gets_no_signing_arguments(self):
        seen = {}

        def opener(connection, heartbeat_timeout_seconds, **signing):
            seen.update(signing)
            return Sink()

        run(["run", "--connection", "x", "--duration", "0.2"], opener)
        self.assertEqual(seen, {})


class ServerConfigSigningTests(unittest.TestCase):
    """The signing options an administrator saves from Server Admin reach the bridge through the Server's configuration file."""

    def setUp(self):
        import os
        import tempfile

        self._environment = {name: os.environ.pop(name, None) for name in (cli.SIGNING_KEY_ENVIRONMENT, cli.SIGNING_ALLOW_UNSIGNED_ENVIRONMENT, cli.SERVER_CONFIG_ENVIRONMENT)}
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.key = os.path.join(self._folder.name, "uav.key")
        with open(self.key, "w", encoding="ascii") as handle:
            handle.write(bytes(range(32)).hex())
        self.config = os.path.join(self._folder.name, "server-config.json")

    def tearDown(self):
        import os

        for name, value in self._environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _write(self, document):
        with open(self.config, "w", encoding="utf-8") as handle:
            handle.write(document if isinstance(document, str) else json.dumps(document))

    def _opened(self, argv):
        seen = {}

        def opener(connection, heartbeat_timeout_seconds, **signing):
            seen.update(signing)
            return Sink()

        code, _, err = run(["run", "--connection", "x", "--duration", "0.2", *argv], opener)
        return code, seen, err

    def test_the_saved_key_file_turns_signing_on_and_refuses_unsigned_by_default(self):
        self._write({"port": 3000, "mavlink": {"signingKeyFile": self.key}})
        code, seen, _ = self._opened(["--server-config", self.config])
        self.assertEqual(code, 0)
        self.assertEqual(seen, {"signing_key": bytes(range(32)), "allow_unsigned": False})

    def test_the_saved_switch_for_unsigned_messages_is_used(self):
        self._write({"mavlink": {"signingKeyFile": self.key, "signingAllowUnsigned": True}})
        _, seen, _ = self._opened(["--server-config", self.config])
        self.assertEqual(seen["allow_unsigned"], True)

    def test_nothing_saved_means_no_signing(self):
        for document in ({}, {"mavlink": {}}, {"mavlink": {"signingKeyFile": ""}}):
            self._write(document)
            code, seen, _ = self._opened(["--server-config", self.config])
            self.assertEqual((code, seen), (0, {}))

    def test_the_option_and_the_environment_win_over_the_saved_values(self):
        import os

        other = os.path.join(self._folder.name, "other.key")
        with open(other, "w", encoding="ascii") as handle:
            handle.write(bytes(range(1, 33)).hex())
        self._write({"mavlink": {"signingKeyFile": self.key, "signingAllowUnsigned": True}})
        _, seen, _ = self._opened(["--server-config", self.config, "--signing-key-file", other])
        self.assertEqual(seen["signing_key"], bytes(range(1, 33)))
        os.environ[cli.SIGNING_ALLOW_UNSIGNED_ENVIRONMENT] = "0"
        _, seen, _ = self._opened(["--server-config", self.config])
        self.assertEqual(seen["allow_unsigned"], False)
        os.environ[cli.SIGNING_ALLOW_UNSIGNED_ENVIRONMENT] = "1"
        self._write({"mavlink": {"signingKeyFile": self.key, "signingAllowUnsigned": False}})
        _, seen, _ = self._opened(["--server-config", self.config])
        self.assertEqual(seen["allow_unsigned"], True)

    def test_the_configuration_can_be_named_by_the_environment(self):
        import os

        self._write({"mavlink": {"signingKeyFile": self.key}})
        os.environ[cli.SERVER_CONFIG_ENVIRONMENT] = self.config
        _, seen, _ = self._opened([])
        self.assertEqual(seen["signing_key"], bytes(range(32)))

    def test_a_configuration_that_cannot_be_used_is_an_error_never_no_signing(self):
        import os

        opened = []
        for document in ("not json", "[1]", {"mavlink": "yes"}, {"mavlink": {"signingKeyFile": 5}}, {"mavlink": {"signingAllowUnsigned": "no"}}):
            self._write(document)
            code, _, err = run(["run", "--connection", "x", "--server-config", self.config], lambda *a, **k: opened.append(1) or Sink())
            self.assertEqual(code, 2, document)
            self.assertIn("server configuration", err)
        code, _, err = run(["run", "--connection", "x", "--server-config", os.path.join(self._folder.name, "missing.json")], lambda *a, **k: opened.append(1) or Sink())
        self.assertEqual(code, 2)
        self.assertEqual(opened, [])

    def test_a_saved_key_file_that_is_not_a_key_is_a_usage_error_without_printing_it(self):
        import os

        bad = os.path.join(self._folder.name, "bad.key")
        with open(bad, "w", encoding="ascii") as handle:
            handle.write("secret-looking-text")
        self._write({"mavlink": {"signingKeyFile": bad}})
        code, _, err = run(["run", "--connection", "x", "--server-config", self.config], lambda *a, **k: Sink())
        self.assertEqual(code, 2)
        self.assertNotIn("secret-looking-text", err)


if __name__ == "__main__":
    unittest.main()
