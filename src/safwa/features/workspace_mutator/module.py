"""The workspace: one subagent, no entity of its own."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from . import agent
from .remove import REMOVE_TOOL

MODULE = FeatureModule(
    name="workspace_mutator",
    agents=(agent.MUTATOR_AGENT,),
    mutation_tools=(REMOVE_TOOL,),
)
