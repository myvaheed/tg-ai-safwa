from __future__ import annotations

# Importing these submodules registers their @router handlers as a side effect.
# Keep them explicit so dropping a symbol re-export below does not silently
# unregister the handlers living in that submodule.
from . import (  # noqa: F401
    callbacks,
    commands,
    dialogue,
)
from .callbacks import SHELL_CALLBACK_ACTIONS, callback_token_handler
from .commands import SHELL_COMMANDS, register_commands, sync_bot_commands
from .dialogue import ordinary_text, voice_message
from .items import render_item_editor, render_item_text_prompt
from .proposals import render_ai_outcome, render_proposal

__all__ = [
    "SHELL_CALLBACK_ACTIONS",
    "SHELL_COMMANDS",
    "callback_token_handler",
    "ordinary_text",
    "render_ai_outcome",
    "render_item_editor",
    "render_item_text_prompt",
    "render_proposal",
    "register_commands",
    "sync_bot_commands",
    "voice_message",
]
