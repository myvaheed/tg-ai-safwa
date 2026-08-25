"""The board: one subagent, no entity of its own."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule
from . import agent

MODULE = FeatureModule(name="board", agents=(agent.BOARD_AGENT,))
