# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - tests for the simulator smoke check (its judgement; the simulator itself is not started here)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
import importlib.util
import json
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("sitl_smoke", Path(__file__).resolve().parent.parent / "tools" / "sitl_smoke.py")
sitl_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sitl_smoke)


def status(link, sent):
    return json.dumps({"heartbeat": {"sent": sent}, "link": {"status": link}})


class JudgeTests(unittest.TestCase):
    def test_a_heard_link_and_sent_beats_pass(self):
        passed, _ = sitl_smoke.judge("HEARTBEAT_RUNNING\n" + status("LOST", 1) + "\n" + status("OK", 3) + "\nHEARTBEAT_STOPPED\n")
        self.assertTrue(passed)

    def test_a_simulator_that_never_spoke_fails(self):
        passed, reason = sitl_smoke.judge(status("LOST", 5) + "\n" + status("LOST", 9))
        self.assertFalse(passed)
        self.assertIn("never heard", reason)

    def test_a_run_without_the_monitor_or_without_beats_fails(self):
        self.assertFalse(sitl_smoke.judge(json.dumps({"heartbeat": {"sent": 4}}))[0])
        self.assertFalse(sitl_smoke.judge(status("OK", 0))[0])
        self.assertFalse(sitl_smoke.judge("")[0])
        self.assertFalse(sitl_smoke.judge("{not json\n" + status("OK", 0))[0])

    def test_bad_options_are_refused(self):
        self.assertEqual(sitl_smoke.main(["--connection", "x", "--seconds", "0"]), 2)


if __name__ == "__main__":
    unittest.main()
