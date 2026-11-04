# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - tests of the mission upload switch of Server Admin (no broker, no autopilot)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The mission upload switch of the server configuration: followed only when the host allows it and names the file; anything damaged switches the upload off."""

import json
import tempfile
import unittest
from pathlib import Path

from hydra_umc_bridge_uav.settings import (
    REMOTE_ENVIRONMENT,
    MissionSettings,
    UavSettings,
    read_server_mission_upload,
    remote_settings_allowed,
    with_server_switches,
)

ALLOWED = {REMOTE_ENVIRONMENT: "1"}


class ServerSwitchTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def config(self, document, raw=None):
        path = Path(self._dir.name) / "server-config.json"
        path.write_text(raw if raw is not None else json.dumps(document), encoding="utf-8")
        return str(path)

    def test_the_host_allows_remote_settings_only_with_its_own_switch(self):
        self.assertFalse(remote_settings_allowed({}))
        self.assertFalse(remote_settings_allowed({REMOTE_ENVIRONMENT: "0"}))
        self.assertTrue(remote_settings_allowed(ALLOWED))

    def test_the_file_gives_the_switch_or_nothing(self):
        self.assertTrue(read_server_mission_upload(self.config({"bridges": {"uav": {"missionUploadEnabled": True}}})))
        self.assertFalse(read_server_mission_upload(self.config({"bridges": {"uav": {"missionUploadEnabled": False}}})))
        self.assertIsNone(read_server_mission_upload(self.config({})))
        self.assertIsNone(read_server_mission_upload(self.config({"bridges": {"amr": {}}})))
        with self.assertRaises(ValueError):
            read_server_mission_upload(self.config({"bridges": {"uav": {"missionUploadEnabled": "yes"}}}))
        with self.assertRaises(ValueError):
            read_server_mission_upload(self.config(None, raw="{not json"))
        with self.assertRaises(ValueError):
            read_server_mission_upload(str(Path(self._dir.name) / "missing.json"))

    def test_without_the_host_switch_or_the_file_the_local_settings_stand(self):
        local = UavSettings()
        path = self.config({"bridges": {"uav": {"missionUploadEnabled": True}}})
        self.assertIs(with_server_switches(local, path, {}), local)
        self.assertIs(with_server_switches(local, None, ALLOWED), local)
        self.assertFalse(with_server_switches(local, path, {}).mission.upload_enabled)

    def test_with_both_the_server_decides_in_either_direction(self):
        path_on = self.config({"bridges": {"uav": {"missionUploadEnabled": True}}})
        self.assertTrue(with_server_switches(UavSettings(), path_on, ALLOWED).mission.upload_enabled)
        path_off = self.config({"bridges": {"uav": {"missionUploadEnabled": False}}})
        on_locally = UavSettings(mission=MissionSettings(upload_enabled=True))
        self.assertFalse(with_server_switches(on_locally, path_off, ALLOWED).mission.upload_enabled)
        self.assertEqual(with_server_switches(UavSettings(), path_on, ALLOWED).mission.max_items, 100)

    def test_a_file_the_server_does_not_fill_keeps_the_local_value(self):
        local = UavSettings(mission=MissionSettings(upload_enabled=True))
        self.assertIs(with_server_switches(local, self.config({}), ALLOWED), local)

    def test_a_file_that_cannot_be_heard_switches_the_upload_off(self):
        on_locally = UavSettings(mission=MissionSettings(upload_enabled=True))
        for path in (self.config(None, raw="{not json"), self.config({"bridges": {"uav": {"missionUploadEnabled": 1}}}), str(Path(self._dir.name) / "missing.json")):
            self.assertFalse(with_server_switches(on_locally, path, ALLOWED).mission.upload_enabled)


if __name__ == "__main__":
    unittest.main()
