"""What a bot message is: the kinds a reader can tell apart.

`telegram_llm` writes and reads the invisible mark but declares no kinds, so which kinds
exist and what each means to a reader is the host's. The number each is written under
follows from its name, so this list is the whole of what has to be decided.
"""

from __future__ import annotations

from enum import StrEnum

from telegram_llm import KindMarks


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


MARKS = KindMarks([kind.value for kind in MessageKind])
