"""Continuity: the summaries and the memory file that outlive one conversation."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule
from .background import MEMORY_FILE_POLL, MEMORY_MAINTENANCE

MODULE = FeatureModule(
    name="continuity",
    background=(MEMORY_FILE_POLL, MEMORY_MAINTENANCE),
)
