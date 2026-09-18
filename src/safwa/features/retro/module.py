"""Retro: the screens over a Sprint that has closed, and no entity of its own — what it
writes, it writes on the Sprint's row."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.telegram.manifest import FeatureModule

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
        ),
    ),
    callback_actions=telegram.RETRO_CALLBACK_ACTIONS,
)
