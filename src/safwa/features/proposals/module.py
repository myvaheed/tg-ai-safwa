"""Proposals: the generic review flow, plus the one tool that removes anything."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule
from .remove import REMOVE_TOOL
from .telegram import PROPOSAL_CALLBACK_ACTIONS

MODULE = FeatureModule(
    name="proposals",
    mutation_tools=(REMOVE_TOOL,),
    callback_actions=PROPOSAL_CALLBACK_ACTIONS,
)
