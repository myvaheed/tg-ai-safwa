"""The Home dashboard: its four blocks, the words under the Values, and where the
conversation starts after it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from ui_harness import history_source, services_for

from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, delete_subtree, finish_action
from safwa.features.diary.model import DiaryEntry
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.home.dashboard import HOME_ACTIONS_SHOWN, HOME_LOG_SHOWN, dashboard_text
from safwa.features.home.motivation import (
    MOTIVATION_DIARY_ENTRIES,
    MOTIVATION_DONE_ACTIONS,
    MOTIVATION_MAX_CHARS,
    Motivator,
)
from safwa.features.planning.use_cases import start_sprint
from safwa.features.profile.api import home_after_minutes
from safwa.features.profile.model import (
    HOME_AFTER_MINUTES_DEFAULT,
    HOME_AFTER_MINUTES_MAX,
    HOME_AFTER_MINUTES_MIN,
    ProfileField,
)
from safwa.features.profile.telegram.screens import PROFILE_FIELDS
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.summary.window import SUMMARY_HEADER
from safwa.features.values.use_cases import create_value
from telegram_llm import Note, markdown_to_telegram_html
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.telegram import OwnerAndWritingMiddleware, render_citations


async def keep(sessions, chat_id, message_id, text, kind: MessageKind, at: datetime) -> None:
    """One message as the bot keeps it: the owner's words come in, everything else goes out."""
    direction = "in" if kind is MessageKind.DIALOGUE_USER else "out"
    await TelegramNotes(sessions).write(
        Note(chat_id, message_id, direction, kind.value, text=text, at=at)
    )


def _link(item_type: str, item_id: int) -> str:
    return f"https://t.me/safwa_ai_bot?start={item_type}-{item_id}"


async def _action(session, title: str, **fields) -> Card:
    return await create_card(session, kind="action", title=title, effort_points=1, **fields)


async def _text(sessions, words=None, now=None) -> str:
    """The dashboard as the chat shows it: its Markdown rendered the way an answer is."""
    async with sessions() as session:
        text = await dashboard_text(session, words or {}, now=now)
        return await render_citations(
            session, services_for(sessions), markdown_to_telegram_html(text)
        )


def _block(text: str, heading: str) -> list[str]:
    """The lines of one block, its heading first."""
    for block in text.split("\n\n"):
        if heading in block.splitlines()[0]:
            return block.splitlines()
    return []


async def test_the_dashboard_opens_with_the_first_actions_of_today(sessions) -> None:
    """HM-ACTIONS-006 — tests/brd/home.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Launch the blog")
        subgoal = await create_card(
            session, kind="subgoal", title="Write posts", parent_id=goal.id
        )
        plain = await _action(session, "Pay rent", stage="today")
        post = await _action(session, "Write the first post", stage="today", parent_id=subgoal.id)
        critical = await _action(session, "Call the bank", stage="today", priority="critical")
        domain = await _action(session, "Pick a domain", stage="today", parent_id=goal.id)
        extra = [await _action(session, f"Extra {n}", stage="today") for n in range(3)]
        await start_sprint(session, success_criteria="Ship it")
        await session.commit()

    lines = _block(await _text(sessions), "Today")

    total = 4 + len(extra)
    assert lines[0] == f"<b>☀️ Today · {HOME_ACTIONS_SHOWN} of {total}</b>"
    # The Goal comes with its first Action, a Subgoal between them is not shown, and the
    # Actions with no Goal follow in Today's order: Critical first.
    body = "\n".join(lines[1:])
    assert body.index(_link("card", goal.id)) < body.index(_link("card", post.id))
    assert _link("card", subgoal.id) not in body
    assert body.index(_link("card", domain.id)) < body.index(_link("card", critical.id))
    assert body.index(_link("card", critical.id)) < body.index(_link("card", plain.id))
    assert lines[1] == f"Planned: {total} Actions"
    assert [line.startswith("    ") for line in lines[2:5]] == [False, True, True]
    assert sum(_link("card", card.id) in body for card in (plain, post, critical, domain, *extra)) == (
        HOME_ACTIONS_SHOWN
    )


async def test_without_today_the_dashboard_takes_the_sprint_then_the_backlog(sessions) -> None:
    """HM-ACTIONS-006 — tests/brd/home.feature"""
    assert (await _text(sessions)).split("\n\n")[1] == "Nothing is planned yet."
    async with sessions() as session:
        later = await _action(session, "Later", priority="low")
        sooner = await _action(session, "Sooner", priority="critical")
        await session.commit()
    lines = _block(await _text(sessions), "Backlog")
    assert lines[0] == "<b>📚 Backlog · 2 of 2</b>"
    assert lines[1].find(_link("card", sooner.id)) >= 0
    assert lines[2].find(_link("card", later.id)) >= 0

    async with sessions() as session:
        planned = await _action(session, "Planned", stage="sprint")
        await session.commit()
    lines = _block(await _text(sessions), "Sprint")
    assert lines[0] == "<b>🏃 Sprint · 1 of 1</b>"
    assert lines[1] == "Planned: 1 Actions"
    assert _link("card", planned.id) in lines[2]


class Words:
    """The model: words for each Value by name, or no call at all for one it cannot do."""

    def __init__(self, said: dict[str, str]) -> None:
        self.said = said
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.requests.append(request)
        context = request.messages[1]["content"]
        name = context.rsplit("Value: ", 1)[1].split(" — ")[0]
        if name not in self.said:
            return CompletionTurn(content="I would rather not.")
        arguments = json.dumps({"text": self.said[name]})
        return CompletionTurn(content=None, tool_calls=(ToolCall("c1", "motivate", arguments),))


async def test_each_value_in_focus_carries_the_words_written_for_it(sessions) -> None:
    """HM-VALUES-007 — tests/brd/home.feature"""
    assert MOTIVATION_MAX_CHARS == 200
    async with sessions() as session:
        await set_profile_field(session, ProfileField.ABOUT_ME, "I run in the mornings.")
        health = await create_value(session, "Health", "Body first", active=True)
        family = await create_value(session, "Family", active=True)
        tidy = await create_value(session, "Tidiness")
        await create_card(session, kind="goal", title="Run a marathon")
        done = []
        for n in range(MOTIVATION_DONE_ACTIONS + 1):
            action = await _action(session, f"Run {n}")
            await finish_action(session, action.id)
            done.append(action)
            action.completed_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=n)
        for day, body in ((1, "An old day"), (2, "A good run"), (4, "Slept well")):
            await create_diary_entry(session, entry_date=datetime(2026, 9, day).date(), body=body)
        # A day of photos alone carries no words to read.
        session.add(DiaryEntry(entry_date=datetime(2026, 9, 3).date(), body=None))
        await session.commit()
    model = Words({"Health": "A run today keeps it going."})

    words = await Motivator(model).write(sessions)

    assert words == {health.id: "A run today keeps it going."}
    context = next(
        request.messages[1]["content"]
        for request in model.requests
        if "Value: Health" in request.messages[1]["content"]
    )
    assert "I run in the mornings." in context
    assert "- Run a marathon" in context
    finished = [line for line in context.splitlines() if line.startswith("- 2026-")]
    assert len(finished) == MOTIVATION_DONE_ACTIONS
    assert finished[0].endswith(done[-1].title)
    assert "Run 0" not in context
    diary = context.split("Diary, newest first:\n")[1].split("\n\n")[0].splitlines()
    assert len(diary) == MOTIVATION_DIARY_ENTRIES
    assert diary == ["2026-09-04: Slept well", "2026-09-02: A good run"]
    assert context.endswith("Value: Health — Body first")

    lines = _block(await _text(sessions, words), "Values in focus")
    assert lines[1].startswith(f'<a href="{_link("value", family.id)}">')
    assert lines[1].endswith("</a>")
    assert _link("value", health.id) in lines[2]
    assert lines[2].endswith(" — A run today keeps it going.")
    assert _link("value", tidy.id) not in "\n".join(lines)


async def test_the_dashboard_shows_the_time_of_the_day_while_time_tracking_is_on(
    sessions,
) -> None:
    """HM-TIME-008 — tests/brd/home.feature"""
    now = datetime.now(UTC)
    async with sessions() as session:
        today = [await _action(session, f"Today {n}") for n in range(2)]
        await finish_action(session, today[0].id, tracked_mins=90)
        await finish_action(session, today[1].id, tracked_mins=45)
        yesterday = await _action(session, "Yesterday")
        await finish_action(session, yesterday.id, tracked_mins=600)
        yesterday.completed_at = now - timedelta(days=1)
        untimed = await _action(session, "Untimed")
        await finish_action(session, untimed.id)
        await session.commit()
    assert "⌛" not in await _text(sessions, now=now)

    async with sessions() as session:
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        await session.commit()
    assert "⌛ Tracked today: 2h 15m over 2 Actions" in await _text(sessions, now=now)
    assert "⌛ Tracked today: nothing yet" in await _text(sessions, now=now + timedelta(days=2))


async def test_the_dashboard_ends_with_the_last_changes(sessions) -> None:
    """HM-LOG-009 — tests/brd/home.feature"""
    async with sessions() as session:
        kept = await _action(session, "Kept")
        gone = await _action(session, "Gone")
        await delete_subtree(session, gone.id)
        await session.commit()

    lines = _block(await _text(sessions), "Latest changes")[1:]
    # Newest first; a deleted item, and every change to it before, is a title with no link.
    assert lines[0].endswith("🗑 Gone — deleted")
    assert lines[1].endswith("➕ Gone — created")
    assert _link("card", kept.id) in lines[2] and lines[2].endswith(" — created")
    assert lines[0].split()[0].count(":") == 1

    async with sessions() as session:
        for n in range(HOME_LOG_SHOWN):
            await _action(session, f"Filler {n}")
        await session.commit()
    lines = _block(await _text(sessions), "Latest changes")[1:]
    assert len(lines) == HOME_LOG_SHOWN
    assert f"Filler {HOME_LOG_SHOWN - 1}" in lines[0]
    assert "Gone" not in "\n".join(lines)


async def test_the_quiet_time_is_set_in_the_profile(sessions) -> None:
    """PS-HOME-018 — tests/brd/profile.feature"""
    assert HOME_AFTER_MINUTES_DEFAULT == 30
    parse = PROFILE_FIELDS["home_after_minutes"].parse
    assert parse(str(HOME_AFTER_MINUTES_MIN)) == HOME_AFTER_MINUTES_MIN
    assert parse(str(HOME_AFTER_MINUTES_MAX)) == HOME_AFTER_MINUTES_MAX
    for refused in (str(HOME_AFTER_MINUTES_MIN - 1), str(HOME_AFTER_MINUTES_MAX + 1), "off", "1.5"):
        with pytest.raises(ValueError, match="whole number of minutes"):
            parse(refused)
    async with sessions() as session:
        assert await home_after_minutes(session) == HOME_AFTER_MINUTES_DEFAULT
        with pytest.raises(DomainError, match="between"):
            await set_profile_field(session, ProfileField.HOME_AFTER_MINUTES, 0)
        assert await home_after_minutes(session) == HOME_AFTER_MINUTES_DEFAULT
        await set_profile_field(session, ProfileField.HOME_AFTER_MINUTES, 45)
        await session.commit()
    async with sessions() as session:
        assert await home_after_minutes(session) == 45


async def test_after_a_clear_the_conversation_starts_over(sessions) -> None:
    """HM-HISTORY-010 — tests/brd/home.feature"""
    chat_id = 700
    at = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    await keep(sessions, chat_id, 10, "Before the clear", MessageKind.DIALOGUE_USER, at)
    await keep(
        sessions, chat_id, 11, f"{SUMMARY_HEADER}\nAn older Summary.", MessageKind.SUMMARY,
        at + timedelta(minutes=1),
    )
    await keep(
        sessions, chat_id, 12, "An answer before it", MessageKind.DIALOGUE_ASSISTANT,
        at + timedelta(minutes=2),
    )
    await keep(sessions, chat_id, 13, "🏠 The dashboard", MessageKind.HOME, at + timedelta(hours=1))
    await keep(
        sessions, chat_id, 14, "After the clear", MessageKind.DIALOGUE_USER,
        at + timedelta(hours=2),
    )
    source = history_source(sessions)

    entries = await source.recent(chat_id)

    assert [entry.text for entry in entries] == ["After the clear"]
    # The Diary reads a whole day, the clear inside it.
    day = await source.day_transcript(
        chat_id, start=at - timedelta(hours=1), end=at + timedelta(days=1), token_budget=10_000
    )
    assert "Before the clear" in day and "After the clear" in day
    assert "The dashboard" not in day


async def test_every_message_and_press_of_the_owner_restarts_the_quiet_time(sessions) -> None:
    """HM-QUIET-003 — tests/brd/home.feature"""
    services = services_for(sessions)
    long_ago = datetime.now(UTC) - timedelta(days=1)
    services.owner_acted_at = long_ago

    async def handler(event, data) -> None:
        return None

    private = SimpleNamespace(type="private")
    stranger = SimpleNamespace(from_user=SimpleNamespace(id=7), chat=private)
    await OwnerAndWritingMiddleware()(handler, stranger, {"services": services})
    assert services.owner_acted_at == long_ago

    owner = SimpleNamespace(from_user=SimpleNamespace(id=services.owner_id), chat=private)
    await OwnerAndWritingMiddleware()(handler, owner, {"services": services})
    assert services.owner_acted_at > long_ago
