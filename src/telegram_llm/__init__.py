"""The chat a person and a language model share, as a Telegram bot keeps it.

The package holds what such a bot needs whatever it is for: the chat itself as the record
of the conversation, a message that says for itself what kind of message it is, and a
screen that is redrawn or taken away rather than left standing.

It knows nothing about what the bot is about. What each message kind means to the
conversation is the host's to say, once, in a `ChatVocabulary`.
"""

from __future__ import annotations

from .host import ChatHost, Freeze
from .marking import KindMarks
from .notes import Note, NoteStore
from .text import (
    TELEGRAM_TEXT_LIMIT,
    markdown_to_telegram_html,
    restore_citations,
    split_telegram_text,
)
from .voice import (
    AudioClip,
    ProgressCallback,
    Transcriber,
    TranscriptionError,
    TranscriptionResult,
)
from .window import (
    ChatMessage,
    ChatReader,
    ChatVocabulary,
    ChatWindow,
    DialogueMessage,
    HistoryEntry,
)

__all__ = [
    "TELEGRAM_TEXT_LIMIT",
    "AudioClip",
    "ChatHost",
    "ChatMessage",
    "ChatReader",
    "ChatVocabulary",
    "ChatWindow",
    "DialogueMessage",
    "Freeze",
    "HistoryEntry",
    "KindMarks",
    "Note",
    "NoteStore",
    "ProgressCallback",
    "Transcriber",
    "TranscriptionError",
    "TranscriptionResult",
    "markdown_to_telegram_html",
    "restore_citations",
    "split_telegram_text",
]
