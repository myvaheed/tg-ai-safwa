"""Proposals: the generic review flow, plus the one tool that removes anything."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule
from .remove import REMOVE_TOOL

MODULE = FeatureModule(
    name="proposals",
    mutation_tools=(REMOVE_TOOL,),
)
