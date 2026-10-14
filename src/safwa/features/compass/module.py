"""Compass: the method each feature words for its items, explained when asked; a subagent
that reads the owner's whole tree, asks about what could be set better and shows it as one
picture; the Dashboard's button that sends that picture; and a look at the tree every nine
days."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from . import agent, telegram
from .graph_review import GRAPH_REVIEW_HOOK as GRAPH_REVIEW_HOOK

MODULE = FeatureModule(
    name="compass",
    agents=(agent.COMPASS_AGENT,),
    callback_actions=telegram.COMPASS_CALLBACK_ACTIONS,
)
