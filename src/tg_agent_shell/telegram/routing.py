"""The router one application builds for itself.

An aiogram Router attaches to a single Dispatcher, so this is a factory rather than a
module object every import shares: two applications in one process each get their own, and
a second build never finds the first one's handlers still on it. Registration is explicit
here, so a handler this function does not name is a handler nothing reaches.
"""

from __future__ import annotations

from aiogram import F, Router

from .callbacks import callback_token_handler
from .commands import dismiss_screens_before_a_command, navigation, register_commands
from .contributions import ScreenCommand
from .dialogue import ordinary_text, voice_message
from .services import OwnerAndWritingMiddleware


def build_router(commands: tuple[ScreenCommand, ...]) -> Router:
    """A new router with the shell's handlers and the screens the features declared."""
    router = Router(name="tg_agent_shell")
    router.message.outer_middleware.register(OwnerAndWritingMiddleware())
    router.callback_query.outer_middleware.register(OwnerAndWritingMiddleware())
    router.message.middleware(dismiss_screens_before_a_command)
    register_commands(router, commands)
    router.message.register(ordinary_text, F.text & ~F.text.startswith("/"))
    router.message.register(voice_message, F.voice | F.audio | F.video_note)
    router.callback_query.register(callback_token_handler, F.data.startswith("cb:"))
    router.callback_query.register(navigation, F.data.startswith("nav:"))
    return router
