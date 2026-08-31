"""Planning: the mode the workspace is in when no Sprint runs, and the Sprint itself."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule
from ...foundation.screens import ScreenSpec
from ...models import Sprint
from . import background, telegram, views

MODULE = FeatureModule(
    name="planning",
    views=views.VIEWS,
    background=(background.SPRINT_EXPIRY,),
    screens=(
        ScreenSpec(
            item_type="retro",
            model=Sprint,
            open=telegram.open_sprint_retro,
            label=telegram.retro_citation_label,
            # A retro is cited and linked, but the `open` tool has no reason to reach it.
            ai_openable=False,
        ),
    ),
)
