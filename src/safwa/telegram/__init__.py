from __future__ import annotations

# Importing these submodules registers their @router handlers as a side effect.
# Keep them explicit so dropping a symbol re-export below does not silently
# unregister the handlers living in that submodule.
from . import (  # noqa: F401
    callbacks,
    commands,
    dialogue,
)
from ._core import (
    BACKGROUND_SOURCE_ID,
    RELATION_CHOICES,
    CallbackContext,
    GenerationGuard,
    OwnerAndWritingMiddleware,
    Services,
    router,
)
from ._messaging import dismiss_prior_ui
from .callbacks import CALLBACK_ACTIONS, callback_token_handler
from .cards import (
    handle_card_creation_chooser,
    render_card,
    render_card_choices,
    render_card_creation,
    render_children,
    render_dashboard,
)
from .commands import sync_bot_commands
from .dialogue import ordinary_text, voice_message
from .escalation import ReminderRuntime, format_escalation
from .items import render_item_editor, render_item_text_prompt
from .proposals import render_ai_outcome, render_proposal
from .screens import open_citation, open_item_screen, render_citations
from .sprint import render_sprint, render_today

__all__ = [
    "BACKGROUND_SOURCE_ID",
    "CALLBACK_ACTIONS",
    "CallbackContext",
    "GenerationGuard",
    "OwnerAndWritingMiddleware",
    "RELATION_CHOICES",
    "ReminderRuntime",
    "Services",
    "callback_token_handler",
    "dismiss_prior_ui",
    "format_escalation",
    "handle_card_creation_chooser",
    "open_citation",
    "open_item_screen",
    "ordinary_text",
    "render_ai_outcome",
    "render_card",
    "render_card_choices",
    "render_card_creation",
    "render_children",
    "render_citations",
    "render_dashboard",
    "render_item_editor",
    "render_item_text_prompt",
    "render_proposal",
    "render_sprint",
    "render_today",
    "router",
    "sync_bot_commands",
    "voice_message",
]
