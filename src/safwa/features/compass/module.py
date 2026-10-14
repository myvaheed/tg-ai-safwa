"""Compass: the method each feature words for its items, explained when asked; a subagent
that reads the owner's whole tree and asks about what could be set better; and a look at
that tree every nine days."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from . import agent
from .graph_review import GRAPH_REVIEW_HOOK as GRAPH_REVIEW_HOOK

MODULE = FeatureModule(name="compass", agents=(agent.COMPASS_AGENT,))
