"""Blueprint: one subsession's agent and the check before it opens, no entity of its own."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from . import agent
from .hooks import BLUEPRINT_ASKED_HOOK as BLUEPRINT_ASKED_HOOK

MODULE = FeatureModule(
    name="blueprint",
    agents=(agent.BLUEPRINT_AGENT,),
)
