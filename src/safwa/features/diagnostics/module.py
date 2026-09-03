"""Diagnostics: one command, no entity and no screen of its own."""

from __future__ import annotations

from ...foundation.screens import ScreenCommand
from ...shell.manifest import FeatureModule
from .telegram import command_status

MODULE = FeatureModule(
    name="diagnostics",
    commands=(
        ScreenCommand(
            handler=command_status, command="status", description="Safwa diagnostics"
        ),
    ),
)
