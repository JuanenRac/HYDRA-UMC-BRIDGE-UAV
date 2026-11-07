# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Entry point for jobs (cmd/job)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The entry point that receives jobs over MQTT, like the other bridges, and decides them with `UavCoordinator`.

`hydra/bridges/uav/cmd/job` carries one `BridgeJob` (the `job_to_dict` shape). A retained replay is ignored, a message that is too large, unreadable or malformed is
refused, and a repeated idempotency key is not run twice (a request that only brings the vehicle to a safe state is the exception, see `UavCoordinator.handle_message`).
The answer goes to `hydra/bridges/uav/cmd/job/result`.

The entry decides; it does not fly. Without a `sender` the answer says `sent: false` and `mode: plan-only`. With a `sender` (a function that takes the accepted
`UavDispatch` and returns the `MavlinkSendResult`) the request is handed to it, and `ARM` is never confirmed here: the sender owns that opt-in.

Off by default (`job_entry.enabled` in `config/uav.json`, or `HYDRA_UMC_BRIDGE_UAV_JOB_ENTRY=1`): when off the bridge subscribes to nothing and answers nothing.
Not run against a real broker or vehicle.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable, Protocol

from hydra_umc_sdk.bridge_contract import CellState, MachineState

from .coordinator import UavCoordinator, UavDispatch

_LOG = logging.getLogger(__name__)

TOPIC_PREFIX = "hydra/bridges/uav/"
JOB_TOPIC = f"{TOPIC_PREFIX}cmd/job"
RESULT_TOPIC = f"{JOB_TOPIC}/result"
ENABLE_ENVIRONMENT = "HYDRA_UMC_BRIDGE_UAV_JOB_ENTRY"
MAX_PAYLOAD_BYTES = 16 * 1024


class MqttPublisher(Protocol):
    def publish(self, topic: str, payload: str) -> object: ...


def enabled_from_environment() -> bool:
    return os.environ.get(ENABLE_ENVIRONMENT, "").strip() == "1"


class UavJobEntry:
    """Receives the jobs of `cmd/job`, decides them with the coordinator and, only when a sender was given, forwards what the gate accepted."""

    def __init__(
        self,
        coordinator: UavCoordinator,
        cell_state: Callable[[], CellState],
        machine_state: Callable[[], MachineState | None],
        sender: Callable[[UavDispatch], object] | None = None,
        *,
        enabled: bool | None = None,
    ) -> None:
        """`cell_state` and `machine_state` are read for every job; the second one must be what the autopilot reported (None when unknown: the gate refuses flight phases)."""
        self._coordinator = coordinator
        self._cell_state = cell_state
        self._machine_state = machine_state
        self._sender = sender
        self.enabled = enabled_from_environment() if enabled is None else bool(enabled)

    def subscriptions(self) -> list[tuple[str, int]]:
        return [(JOB_TOPIC, 1)] if self.enabled else []

    def handles(self, topic: str) -> bool:
        return self.enabled and topic == JOB_TOPIC

    def on_message(self, client: MqttPublisher, topic: str, payload: bytes, *, retained: bool = False) -> dict[str, object] | None:
        """Handle one message of `cmd/job`; publish the answer and return it (None when there is nothing to answer). Never raises."""
        if not self.handles(topic) or retained:
            return None
        try:
            answer = self._decide(payload)
        except Exception as error:  # noqa: BLE001 - nothing a message does may end the loop
            _LOG.exception("could not handle %s", topic)
            answer = {"allowed": False, "reason": f"the job could not be handled: {type(error).__name__}", "sent": False}
        try:
            client.publish(RESULT_TOPIC, json.dumps(answer))
        except Exception:  # noqa: BLE001 - a broker that is gone must not end the loop either
            _LOG.exception("could not publish the answer")
        return answer

    def _decide(self, payload: bytes) -> dict[str, object]:
        if len(payload) > MAX_PAYLOAD_BYTES:
            return {"allowed": False, "reason": f"payload is larger than {MAX_PAYLOAD_BYTES} bytes", "sent": False}
        try:
            raw = json.loads(payload)
        except (ValueError, UnicodeDecodeError) as error:
            return {"allowed": False, "reason": f"malformed job payload: {error}", "sent": False}
        if not isinstance(raw, dict):
            return {"allowed": False, "reason": "malformed job payload: not an object", "sent": False}
        dispatch = self._coordinator.handle_message(raw, self._cell_state(), self._machine_state())
        answer: dict[str, object] = {"allowed": dispatch.accepted, "reason": dispatch.reason, "request": dispatch.request, "mode": dispatch.mode, "sent": False}
        job_id = raw.get("job_id")
        if isinstance(job_id, str):
            answer["job_id"] = job_id
        if dispatch.accepted and self._sender is not None:
            result = self._sender(dispatch)
            sent = bool(getattr(result, "sent", getattr(result, "ok", False)))
            answer["sent"] = sent
            answer["mode"] = "send"
            if not sent:
                answer["allowed"] = False
                answer["reason"] = str(getattr(result, "reason", "the vehicle did not take the request"))
        return answer
