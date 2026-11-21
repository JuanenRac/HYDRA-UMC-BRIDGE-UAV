# =============================================================================
# HYDRA-UMC-BRIDGE-UAV - The reader of the cell state chosen in the settings
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Builds the shared cell state reader of the SDK (`hydra_umc_sdk.cell_state_source`) from the `cell_state` section of `config/uav.json`.

Only used when the source is not "none": with "none" the bridge reads the cell as it always did and the SDK module is not even imported. A source that cannot be built (a key file
that is missing or too short, a path and a url together) is an error at start, never a silent READY.
"""

from __future__ import annotations

from typing import Any

from .settings import CellStateSettings


def build_cell_source(settings: CellStateSettings) -> Any:
    from hydra_umc_sdk.cell_state_source import CellStateSource, CellStateSourceConfig, CellStateSourceError

    try:
        config = CellStateSourceConfig(
            source=settings.source,
            value=settings.value,
            path=settings.path or None,
            url=settings.url or None,
            max_age_seconds=float(settings.max_age_seconds),
            key_file=settings.key_file or None,
            public_key_file=settings.public_key_file or None,
            algorithm=settings.algorithm,
            key_id=settings.key_id or None,
        )
        return CellStateSource(config)
    except CellStateSourceError as error:
        raise ValueError(f"the cell state source cannot be used: {error}") from error
