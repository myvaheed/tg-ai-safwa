"""Proposals: the generic review flow, and the screens it answers on."""

from __future__ import annotations

from ..telegram.manifest import FeatureModule
from .telegram import PROPOSAL_CALLBACK_ACTIONS

MODULE = FeatureModule(name="proposals", callback_actions=PROPOSAL_CALLBACK_ACTIONS)
