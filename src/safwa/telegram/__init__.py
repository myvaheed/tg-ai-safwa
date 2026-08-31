from __future__ import annotations

# Importing these submodules registers their @router handlers as a side effect.
# Keep them explicit so dropping a symbol re-export below does not silently
# unregister the handlers living in that submodule.
from . import (  # noqa: F401
    callbacks,
    commands,
    dialogue,
)
from ._core import RELATION_CHOICES
from ._presentation import card_overview_text, category_expression, energy_expression
from .callbacks import SHELL_CALLBACK_ACTIONS, callback_token_handler
from .cards import (
    handle_card_creation_chooser,
    render_card,
    render_card_choices,
    render_card_creation,
    render_children,
    render_dashboard,
)
from .commands import SHELL_COMMANDS, register_commands, sync_bot_commands
from .dialogue import ordinary_text, voice_message
from .items import render_item_editor, render_item_text_prompt
from .proposals import render_ai_outcome, render_proposal
from .sprint import render_sprint, render_today

__all__ = [
    "SHELL_CALLBACK_ACTIONS",
    "SHELL_COMMANDS",
    "RELATION_CHOICES",
    "callback_token_handler",
    "card_overview_text",
    "category_expression",
    "energy_expression",
    "handle_card_creation_chooser",
    "ordinary_text",
    "render_ai_outcome",
    "render_card",
    "render_card_choices",
    "render_card_creation",
    "render_children",
    "render_dashboard",
    "render_item_editor",
    "render_item_text_prompt",
    "render_proposal",
    "render_sprint",
    "register_commands",
    "render_today",
    "sync_bot_commands",
    "voice_message",
]
