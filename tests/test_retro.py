"""The retro screen: what a Sprint leaves behind once it has closed."""

from __future__ import annotations

from ui_harness import FakeMessage, button_texts, services_for

from safwa.features.cards.use_cases import create_card
from safwa.features.planning.use_cases import start_sprint
from safwa.features.retro.telegram import open_retro


async def test_rt_open_001_a_sprint_that_ended_keeps_a_screen_of_its_own(sessions) -> None:
    """RT-OPEN-001 — tests/brd/retro.feature"""
    async with sessions() as session:
        await create_card(
            session, kind="action", title="Planned", stage="sprint", effort_points=2
        )
        sprint = await start_sprint(session, success_criteria="Ship v2")
        await session.commit()

    message = FakeMessage(320, bot_message=True)

    await open_retro(message, services_for(sessions), sprint.id)

    text, markup = message.edits[-1]
    assert f"Sprint {sprint.number} retro" in text
    assert str(sprint.planned_start_date) in text
    assert str(sprint.planned_end_date) in text
    assert "Ship v2" in text
    assert "There is nothing here yet." in text
    assert button_texts(markup) == ["↩️ Menu"]
