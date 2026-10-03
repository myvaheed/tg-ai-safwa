"""Life in weeks: the screen Retro opens, its pictures, and the settings they are drawn from.
No command and no menu button of its own: the way in is Retro."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from . import telegram

MODULE = FeatureModule(
    name="life",
    callback_actions=telegram.LIFE_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
