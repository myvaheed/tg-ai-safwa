"""The workspace: one subagent, no entity of its own."""

from __future__ import annotations

from ...shell.manifest import FeatureModule
from . import agent

MODULE = FeatureModule(name="workspace_mutator", agents=(agent.MUTATOR_AGENT,))
