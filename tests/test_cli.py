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


if __name__ == "__main__":
    unittest.main()
