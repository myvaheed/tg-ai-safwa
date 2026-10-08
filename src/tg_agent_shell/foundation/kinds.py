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
    # A Cue that leaves the chat a set time after it was sent, its note with it.
    PASSING_CUE = "passing_cue"
    SUMMARY = "summary"
    UI_INPUT = "ui_input"
    DASHBOARD = "dashboard"
    # The screen drawn when a quiet chat is cleared; history starts over after it.
    HOME = "home"
    # The clear boundary kept after its Home screen is taken out of the chat.
    CHAT_RESET = "chat_reset"
    EDITOR = "editor"
    APPROVAL = "approval"
    RECEIPT = "receipt"
    # Transient progress the sender deletes again, never part of the conversation.
    STATUS = "status"
    ERROR = "error"
