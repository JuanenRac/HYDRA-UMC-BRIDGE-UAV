#!/usr/bin/env python3
# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - Read-only UAV flight-request plan inspector
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Print the static UAV flight-request plan without opening any real transport."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SDK_ROOT = Path(os.environ.get("HYDRA_UMC_SDK_ROOT", ROOT.parent / "HYDRA-UMC-SDK"))
sys.path[:0] = [str(ROOT / "src"), str(SDK_ROOT / "clients" / "python" / "src")]

from hydra_umc_bridge_uav import UavCoordinator  # noqa: E402


def main() -> int:
    """Serialize the static, non-runtime flight-request plan."""

    print(json.dumps(UavCoordinator().request_plan().to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
