"""Planning: the mode the workspace is in when no Sprint runs, and the Sprint itself."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule
from ...foundation.screens import ScreenCommand, ScreenSpec
from ...models import Sprint
from ...telegram.callbacks import PLANNING_CALLBACK_ACTIONS
from ...telegram.sprint import render_sprint, render_today
from . import background, telegram, views

MODULE = FeatureModule(
    name="planning",
    views=views.VIEWS,
    background=(background.SPRINT_EXPIRY,),
    commands=(
        ScreenCommand(
            handler=render_today,
            command="today",
            description="Today dashboard",
            nav="today",
            needs_sprint=True,
        ),
        ScreenCommand(
            handler=render_sprint,
            command="sprint",
            description="Planning or Sprint",
            nav="sprint",
        ),
    ),
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
    callback_actions=PLANNING_CALLBACK_ACTIONS,
)
