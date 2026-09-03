"""Which number this chat writes into a message to say what kind it is.

The kinds are the shell's vocabulary and travel with it. The numbers are not: they mean
something only against the messages already sent under them, so they belong to the one
deployment that sent those. `KindMarks` takes the table for that reason, and this is
Safwa's answer to it.

A code that has reached the chat is written into messages that outlive the kind, so giving
it to a second kind rewrites what those messages mean. Rule O holds both halves: the live
codes never move, and a retired one never comes back.
"""

from __future__ import annotations

from telegram_llm import KindMarks

from .adapters.kinds import MessageKind

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
