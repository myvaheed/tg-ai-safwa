from __future__ import annotations

from enum import StrEnum


class AIProvider(StrEnum):
    LMSTUDIO = "lmstudio"
    OPENROUTER = "openrouter"


class ASRProvider(StrEnum):
    """Where a voice message is transcribed.

    The first three speak the same OpenAI-compatible API; `faster_whisper` decodes in
    this process instead of calling one.
    """

    OFF = "off"
    OPENAI = "openai"
    GROQ = "groq"
    LOCAL = "local"
    FASTER_WHISPER = "faster_whisper"


class ActorType(StrEnum):
    """Who made a change: the owner on a screen, or a proposal the owner approved."""

    USER_UI = "user_ui"
    AI = "ai"
