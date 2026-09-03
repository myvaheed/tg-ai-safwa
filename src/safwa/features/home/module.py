"""Home: one screen, no entity of its own."""

from __future__ import annotations

from ...foundation.screens import ScreenCommand
from ...shell.manifest import FeatureModule
from .telegram import render_home

MODULE = FeatureModule(
    name="home",
    commands=(
        ScreenCommand(
            handler=render_home, command="start", description="Open Safwa", nav="home"
        ),
    ),
)
