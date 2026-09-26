"""The chat a person and a language model share, as a Telegram bot keeps it.

The package holds what such a bot needs whatever it is for: the chat itself as the record
of the conversation, kept as its messages pass through the bot, each with the kind of
message it is, and a screen that is redrawn or taken away rather than left standing.

It knows nothing about what the bot is about. What each message kind means to the
conversation is the host's to say, once, in a `ChatVocabulary`.
"""

from __future__ import annotations

from .host import TELEGRAM_ALBUM_LIMIT, ChatHost, Freeze
from .notes import Note, NoteStore
from .text import (
    TELEGRAM_TEXT_LIMIT,
    markdown_to_telegram_html,
    split_telegram_text,
    telegram_html_to_text,
)
from .voice import (
    AudioClip,
    ProgressCallback,
    Transcriber,
    TranscriptionError,
    TranscriptionResult,
)
from .window import (
    ChatVocabulary,
    ChatWindow,
    DialogueMessage,
    HistoryEntry,
    WindowEdge,
)

__all__ = [
    "TELEGRAM_ALBUM_LIMIT",
    "TELEGRAM_TEXT_LIMIT",
    "AudioClip",
    "ChatHost",
    "ChatVocabulary",
    "ChatWindow",
    "DialogueMessage",
    "Freeze",
    "HistoryEntry",
    "Note",
    "NoteStore",
    "ProgressCallback",
    "Transcriber",
    "TranscriptionError",
    "TranscriptionResult",
    "WindowEdge",
    "markdown_to_telegram_html",
    "split_telegram_text",
    "telegram_html_to_text",
]
