"""What Safwa cites in its own prose, and the screen a citation opens."""

from __future__ import annotations

import html
from datetime import date

import pytest
from marks import read_kind_mark
from ui_harness import (
    FakeMessage,
    button_texts,
    services_for,
)

from safwa.bootstrap.modules import (
    AI_VIEWS,
    ALLOWED_VIEWS,
)
from safwa.features.cards.use_cases import create_card
from safwa.features.checks.use_cases import create_check
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.saved_requests.use_cases import (
    create_saved_request,
)
from safwa.features.tags.use_cases import create_tag, delete_tag
from safwa.features.values.use_cases import create_value
from tg_agent_shell.ai.outcome import AIOutcome, AIOutcomeKind
from tg_agent_shell.ai.sql import create_ai_views
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.telegram import render_ai_outcome
from tg_agent_shell.telegram import (
    open_item_screen,
    render_citations,
)


async def test_open_item_screen_renders_the_manual_screen_of_every_item(sessions) -> None:
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        card = await create_card(session, kind="action", title="Pull-ups", effort_points=1)
        value = await create_value(session, "Health")
        tag = await create_tag(session, "Training")
        check = await create_check(session, title="Form is safe")
        request = await create_saved_request(
            session, "Open actions", "SELECT id FROM ai_cards WHERE kind = 'action'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        targets = [
            ("card", card.id, "Pull-ups"),
            ("value", value.id, "Health"),
            ("tag", tag.id, "Training"),
            ("check", check.id, "Form is safe"),
            ("request", request.id, "Open actions"),
        ]

    services = services_for(sessions)
    for index, (item_type, item_id, expected) in enumerate(targets):
        message = FakeMessage(900 + index, bot_message=True)
        await open_item_screen(message, services, item_type, item_id)
        text, markup = message.edits[-1]
        assert expected in text
        # "Opened" means the real screen, buttons included — not a read-only summary.
        assert button_texts(markup)

    message = FakeMessage(999, bot_message=True)
    with pytest.raises(DomainError):
        await open_item_screen(message, services, "sprint", 1)


async def test_citations_become_deep_links_only_for_live_items(sessions) -> None:
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Pull-ups", effort_points=1)
        tag = await create_tag(session, "Training")
        await delete_tag(session, tag.id)
        await session.commit()
        card_id, tag_id = card.id, tag.id

    services = services_for(sessions)
    text = html.escape(
        f"[Pull & ups](card:{card_id}) under [Training](tag:{tag_id}), "
        "[nothing](card:4242), [not one](sprint:1)."
    )
    async with sessions() as session:
        rendered = await render_citations(session, services, text)

    assert (
        f'<a href="https://t.me/safwa_ai_bot?start=card-{card_id}">⭐️ Pull-ups · ⚡1</a>'
        in rendered
    )
    # A deleted item leaves its words and loses its link, and an unknown type is not a citation.
    assert f"[Training](tag:{tag_id})" not in rendered and "Training" in rendered
    assert "nothing" in rendered and "card-4242" not in rendered
    assert "[not one](sprint:1)" in rendered

    services.bot_username = ""
    async with sessions() as session:
        assert "<a href" not in await render_citations(session, services, text)


async def test_a_citation_aimed_at_something_that_is_not_an_id_keeps_only_its_words(
    sessions,
) -> None:
    """A stamp is not an id, and raw Markdown must never reach the chat."""
    services = services_for(sessions)
    text = html.escape("Записал [📅 17 августа 2026 · 🌟5](diary:wwsouhzg_fB8) за сегодня.")

    async with sessions() as session:
        rendered = await render_citations(session, services, text)

    assert "diary:wwsouhzg_fB8" not in rendered and "<a href" not in rendered
    assert "Записал 📅 17 августа 2026 · 🌟5 за сегодня." in rendered


async def test_citations_use_compact_labels_from_saved_items(sessions) -> None:
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        goal = await create_card(session, kind="goal", title="Быть здоровым")
        action = await create_card(
            session,
            kind="action",
            title="Бегать 3 км",
            parent_id=goal.id,
            effort_points=2,
            categories={"self"},
            energy_types={"physical", "social"},
        )
        value = await create_value(session, "Свобода")
        tag = await create_tag(session, "Здоровье")
        long_tag = await create_tag(session, "x" * 26)
        request = await create_saved_request(
            session, "План на неделю", "SELECT id FROM ai_cards WHERE kind = 'action'",
            views=ALLOWED_VIEWS,
        )
        diary = await create_diary_entry(
            session, entry_date=date(2026, 8, 16), body="Хороший день.", feeling_score=6
        )
        await session.commit()

    services = services_for(sessions)
    text = html.escape(
        " ".join(
            (
                f"[goal](card:{goal.id})",
                f"[action](card:{action.id})",
                f"[value](value:{value.id})",
                f"[tag](tag:{tag.id})",
                f"[long tag](tag:{long_tag.id})",
                f"[request](request:{request.id})",
                f"[diary](diary:{diary.id})",
            )
        )
    )
    async with sessions() as session:
        rendered = await render_citations(session, services, text)

    expected = {
        f"card-{goal.id}": "🎯 Быть здоровым · ⚡0/2",
        f"card-{action.id}": "⭐️ Бегать 3 км · 💪🤝·🌱·⚡2",
        f"value-{value.id}": "💎 Свобода",
        f"tag-{tag.id}": "🏷 Здоровье",
        f"tag-{long_tag.id}": f"🏷 {'x' * 24}…",
        f"request-{request.id}": "💬 План на неделю · 1",
        f"diary-{diary.id}": "16 августа · 🙂6",
    }
    for payload, label in expected.items():
        assert f'?start={payload}">{label}</a>' in rendered


async def test_ai_markdown_is_rendered_as_safe_html_around_live_citations(sessions) -> None:
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Быть здоровым")
        await session.commit()
        goal_id = goal.id

    message = FakeMessage(949, bot_message=False)
    await render_ai_outcome(
        message,
        services_for(sessions),
        AIOutcome(
            AIOutcomeKind.ANSWER,
            f"Это **важно**: **[цель](card:{goal_id})**; *курсив*, ~~нет~~, "
            "`x < y` и <script>.",
        ),
    )
    _kind, visible = read_kind_mark(message.answers[-1])

    assert "<b>важно</b>" in visible
    assert f'<b><a href="https://t.me/safwa_ai_bot?start=card-{goal_id}">' in visible
    assert "</a></b>" in visible
    assert "<i>курсив</i>" in visible
    assert "<s>нет</s>" in visible
    assert "<code>x &lt; y</code>" in visible
    assert "&lt;script&gt;" in visible
    assert "**" not in visible


async def test_a_diary_citation_is_named_by_the_entry_and_opens_the_whole_day(sessions) -> None:
    async with sessions() as session:
        entry = await create_diary_entry(
            session,
            entry_date=date(2026, 3, 4),
            body="Долгий день, но рынок закрыл.",
            feeling_score=6,
        )
        await session.commit()
        entry_id = entry.id

    services = services_for(sessions)
    async with sessions() as session:
        # Whatever the model wrote as the label, the link shows the saved date and score.
        rendered = await render_citations(
            session, services, html.escape(f"Тот день: [что-то своё](diary:{entry_id}).")
        )
    assert (
        f'<a href="https://t.me/safwa_ai_bot?start=diary-{entry_id}">4 марта · 🙂6</a>'
        in rendered
    )

    # The compact score comes back out of Telegram as part of the label, and still links.
    async with sessions() as session:
        assert f"?start=diary-{entry_id}" in await render_citations(
            session, services, html.escape(f"[4 марта · 🙂6](diary:{entry_id})")
        )

    message = FakeMessage(970, bot_message=True)
    await open_item_screen(message, services, "diary", entry_id)
    text, markup = message.edits[-1]
    assert "📔 4 марта · 🙂6" in text
    assert "Долгий день, но рынок закрыл." in text
    # The Diary is written through proposals alone, so its screen offers no control.
    assert markup is None
