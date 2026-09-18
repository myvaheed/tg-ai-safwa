"""A long job watched on one message: the shell's progress note."""

from __future__ import annotations

from sqlalchemy import select
from ui_harness import FakeMessage, services_for

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.telegram import Progress, progress_bar
from tg_agent_shell.telegram.progress import PROGRESS_CELLS


async def test_sc_progress_009_a_long_job_is_watched_on_one_message(sessions) -> None:
    """SC-PROGRESS-009 — tests/brd/tg_agent_shell/screens.feature"""
    assert progress_bar(0, 4) == "░" * PROGRESS_CELLS + " 0%"
    assert progress_bar(1, 4) == "▓▓" + "░" * (PROGRESS_CELLS - 2) + " 25%"
    assert progress_bar(9.9, 10) == "▓" * (PROGRESS_CELLS - 1) + "░ 99%"
    assert progress_bar(4, 4) == "▓" * PROGRESS_CELLS + " 100%"
    assert progress_bar(0, 0) == "░" * PROGRESS_CELLS + " 0%"

    services = services_for(sessions)
    message = FakeMessage(360, bot_message=True, answer_as_new=True)
    progress = Progress(message, services, "🔎 Analysing")

    # Nothing is in the chat before the first report.
    assert message.sent_messages == []
    await progress.report(0, 12, "the Sprints")
    note = message.sent_messages[0]
    assert "🔎 Analysing" in note.text and "0%" in note.text and "the Sprints" in note.text
    assert message.bot.edits == []

    # Redrawn in place, and only when what it would show changed.
    await progress.report(3, 12, "Diary 05.09 – 07.09")
    await progress.report(3, 12, "Diary 05.09 – 07.09")
    assert len(message.bot.edits) == 1
    edited_id, text, _markup = message.bot.edits[0]
    assert edited_id == note.message_id and "25%" in text and "Diary 05.09 – 07.09" in text
    assert len(message.sent_messages) == 1

    await progress.clear()
    assert note.message_id in message.bot.deleted
    await progress.clear()
    assert message.bot.deleted.count(note.message_id) == 1

    # It was a STATUS note while it stood, and leaves no row behind.
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    assert all(row.kind != MessageKind.STATUS.value for row in rows)
