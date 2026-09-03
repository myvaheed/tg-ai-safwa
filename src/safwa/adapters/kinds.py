"""What a bot message is, and the number that says so inside the message itself.

`telegram_llm` writes and reads the invisible mark but declares no kinds: which kinds exist,
and what each of them means to a reader, is the host's. This is that half of the contract, so
a kind and its wire code are declared together and move together.
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


# Codes are append-only.  A code that has reached a real chat is written into messages that
# outlive the kind, so giving it to a second kind rewrites what those messages mean.  Rule O
# holds both halves: the live codes never move, and a retired one never comes back.
RETIRED_MARK_CODES = frozenset({1, 2, 7, 13})
MARKS = KindMarks(
    {
        MessageKind.DIALOGUE_USER.value: 3,
        MessageKind.DIALOGUE_ASSISTANT.value: 4,
        MessageKind.CUE.value: 5,
        MessageKind.SUMMARY.value: 6,
        MessageKind.UI_INPUT.value: 8,
        MessageKind.DASHBOARD.value: 9,
        MessageKind.EDITOR.value: 10,
        MessageKind.APPROVAL.value: 11,
        MessageKind.RECEIPT.value: 12,
        MessageKind.ERROR.value: 14,
        MessageKind.STATUS.value: 15,
    }
)
