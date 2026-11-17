# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - tests/test_mavlink_link_source.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Which HEARTBEATs keep the link watchdog alive: the vehicle's autopilot only.

Evidence: simulator (fake messages; no vehicle was connected).
"""

import unittest

from hydra_umc_bridge_uav import HeartbeatMonitor, LinkStatus, MavlinkLink

MAV_TYPE_QUADROTOR = 2
MAV_TYPE_GCS = 6
MAV_TYPE_GIMBAL = 26
MAV_AUTOPILOT_ARDUPILOTMEGA = 3
MAV_AUTOPILOT_INVALID = 8


class Heartbeat:
    def __init__(self, source=None, **fields):
        self.__dict__.update(fields)
        self._source = source

    def get_type(self):
        return "HEARTBEAT"

    def get_srcSystem(self):  # noqa: N802 - the name pymavlink gives it
        if self._source is None:
            raise AttributeError("no source")
        return self._source


class PlainHeartbeat:
    """A message that does not say where it comes from (no get_srcSystem)."""

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def get_type(self):
        return "HEARTBEAT"


class Connection:
    def __init__(self, target_system):
        self.target_system = target_system

    def recv_match(self, type, blocking, timeout):
        return None


class SourceTests(unittest.TestCase):
    def _alive_after(self, connection, message):
        monitor = HeartbeatMonitor(timeout_s=3.0)
        link = MavlinkLink(connection, monitor, clock=lambda: 100.0, poll_s=0.01)
        link._dispatch(message)  # noqa: SLF001 - the routing is what is being tested
        return monitor.state(100.5).status is LinkStatus.OK

    def test_the_autopilot_of_the_vehicle_counts(self):
        message = Heartbeat(1, type=MAV_TYPE_QUADROTOR, autopilot=MAV_AUTOPILOT_ARDUPILOTMEGA)
        self.assertTrue(self._alive_after(Connection(1), message))

    def test_a_ground_station_does_not_count(self):
        message = Heartbeat(1, type=MAV_TYPE_GCS, autopilot=MAV_AUTOPILOT_INVALID)
        self.assertFalse(self._alive_after(Connection(1), message))

    def test_a_component_that_is_no_autopilot_does_not_count(self):
        message = Heartbeat(1, type=MAV_TYPE_GIMBAL, autopilot=MAV_AUTOPILOT_INVALID)
        self.assertFalse(self._alive_after(Connection(1), message))

    def test_another_system_on_the_same_link_does_not_count(self):
        message = Heartbeat(7, type=MAV_TYPE_QUADROTOR, autopilot=MAV_AUTOPILOT_ARDUPILOTMEGA)
        self.assertFalse(self._alive_after(Connection(1), message))

    def test_a_message_that_does_not_say_where_it_comes_from_counts_as_before(self):
        message = PlainHeartbeat(type=MAV_TYPE_QUADROTOR)
        self.assertTrue(self._alive_after(Connection(1), message))
        # a message whose source cannot be read, and a connection that does not know its target
        self.assertTrue(self._alive_after(Connection(1), Heartbeat(None, type=MAV_TYPE_QUADROTOR)))
        self.assertTrue(self._alive_after(Connection(None), Heartbeat(7, type=MAV_TYPE_QUADROTOR)))
        self.assertTrue(self._alive_after(Connection(0), Heartbeat(7, type=MAV_TYPE_QUADROTOR)))


if __name__ == "__main__":
    unittest.main()
