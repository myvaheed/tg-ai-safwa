"""What a bot message is: the kinds a reader can tell apart.

`telegram_llm` keeps each message's kind beside it but declares no kinds, so which kinds
exist and what each means to a reader is the host's.
"""

from __future__ import annotations

from enum import StrEnum


class MessageKind(StrEnum):
    DIALOGUE_USER = "dialogue_user"
    DIALOGUE_ASSISTANT = "dialogue_assistant"
    # Words the interface wrote about what happened: read on the owner's side as a
    # system line, never as something the assistant said.
    EVENT = "event"
    CUE = "cue"
    SUMMARY = "summary"
    UI_INPUT = "ui_input"
    DASHBOARD = "dashboard"
    # The dashboard drawn when a quiet chat is cleared: no screen or conversation, and the
    # conversation starts over after it.
    HOME = "home"
    EDITOR = "editor"
    APPROVAL = "approval"
    RECEIPT = "receipt"
    # Transient progress the sender deletes again, never part of the conversation.
    STATUS = "status"
    ERROR = "error"
