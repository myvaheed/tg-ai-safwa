from __future__ import annotations

from enum import StrEnum


class AIProvider(StrEnum):
    LMSTUDIO = "lmstudio"
    OPENROUTER = "openrouter"


class ActorType(StrEnum):
    """Who made a change: the owner on a screen, or a proposal the owner approved."""

    USER_UI = "user_ui"
    AI = "ai"
