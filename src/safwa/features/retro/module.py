"""Retro: one screen over a Sprint that has closed, and no entity of its own."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule
from ...foundation.screens import ScreenSpec
from ..planning.model import Sprint
from . import telegram

MODULE = FeatureModule(
    name="retro",
    screens=(
        ScreenSpec(
            item_type="retro",
            model=Sprint,
            open=telegram.open_retro,
            label=telegram.retro_citation_label,
            # A retro is cited and linked, but the `open` tool has no reason to reach it.
            ai_openable=False,
        ),
    ),
)
