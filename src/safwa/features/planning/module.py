"""Planning: the mode the workspace is in when no Sprint runs, and the Sprint itself."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule
from . import background, views

MODULE = FeatureModule(
    name="planning",
    views=views.VIEWS,
    background=(background.SPRINT_EXPIRY,),
)
