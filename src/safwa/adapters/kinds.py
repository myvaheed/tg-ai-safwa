"""What a bot message is: the kinds a reader can tell apart.

`telegram_llm` writes and reads the invisible mark but declares no kinds, so which kinds
exist and what each means to a reader is the host's. Which number stands for which kind is
the deployment's instead, in `dialogue_marks.py`, because a number means something only
against the messages already sent under it.
"""

from __future__ import annotations

from enum import StrEnum


class MessageKind(StrEnum):
    DIALOGUE_USER = "dialogue_user"
    DIALOGUE_ASSISTANT = "dialogue_assistant"
    CUE = "cue"
    SUMMARY = "summary"
    UI_INPUT = "ui_input"
    DASHBOARD = "dashboard"
    EDITOR = "editor"
    APPROVAL = "approval"
    RECEIPT = "receipt"
    # Transient progress the sender deletes again, never part of the conversation.
    STATUS = "status"
    ERROR = "error"
