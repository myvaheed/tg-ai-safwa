"""Blueprint: one subsession's agent, no entity of its own."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from . import agent

MODULE = FeatureModule(
    name="blueprint",
    agents=(agent.BLUEPRINT_AGENT,),
)
