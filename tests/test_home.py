"""The Home dashboard: its four blocks, the words under the Values, and where the
conversation starts after it."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telegram_fakes import spawn_timer
from ui_harness import history_source, services_for

from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, delete_subtree, finish_action, finish_card
from safwa.features.diary.model import DiaryEntry
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.home import motivation
from safwa.features.home.dashboard import HOME_GOALS_SHOWN, HOME_LOG_SHOWN, dashboard_text
from safwa.features.home.motivation import (
    MOTIVATION_DIARY_ENTRIES,
    MOTIVATION_DONE_ACTIONS,
    MOTIVATION_FRESH_MINUTES,
    MOTIVATION_HISTORY_SIZE,
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
from safwa.foundation.workspace import require_workspace
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


async def test_the_dashboard_shows_every_action_in_today(sessions) -> None:
    """HM-ACTIONS-006 — tests/brd/home.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Launch the blog")
        subgoal = await create_card(
            session, kind="goal", title="Write posts", parent_id=goal.id
        )
        nested = [subgoal]
        for level in range(4):
            nested.append(await create_card(session, kind="goal", title=f"Part {level}", parent_id=nested[-1].id))
        plain = await _action(session, "Pay rent", stage="today")
        post = await _action(session, "Write the first post", stage="today", parent_id=nested[-1].id)
        critical = await _action(session, "Call the bank", stage="today", priority="critical")
        domain = await _action(session, "Pick a domain", stage="today", parent_id=goal.id)
        extra = [await _action(session, f"Extra {n}", stage="today") for n in range(3)]
        await start_sprint(session, success_criteria="Ship it")
        await session.commit()

    lines = _block(await _text(sessions), "Today")

    total = 4 + len(extra)
    assert lines[0] == f"<b>☀️ Today · {total}</b>"
    # The Goal comes with its first Action, a Subgoal between them is not shown, and the
    # Actions with no Goal follow in Today's order: Critical first.
    body = "\n".join(lines[1:])
    assert body.index(_link("card", goal.id)) < body.index(_link("card", post.id))
    assert all(_link("card", card.id) not in body for card in nested)
    assert body.index(_link("card", domain.id)) < body.index(_link("card", critical.id))
    assert body.index(_link("card", critical.id)) < body.index(_link("card", plain.id))
    assert lines[1] == f"Planned: {total} Actions"
    assert [line.startswith("    ") for line in lines[2:5]] == [False, True, True]
    assert all(_link("card", card.id) in body for card in (plain, post, critical, domain, *extra))


async def test_without_today_the_dashboard_says_so_and_shows_no_other_list(sessions) -> None:
    """HM-ACTIONS-006 — tests/brd/home.feature"""
    async with sessions() as session:
        planned = await _action(session, "Planned", stage="sprint")
        later = await _action(session, "Later")
        await session.commit()

    text = await _text(sessions)

    assert _block(text, "Today") == ["<b>☀️ Today</b> · Nothing is planned for today."]
    # The two Actions are only the changes that created them, not a list of their own.
    assert text.count(_link("card", planned.id)) == 1 and text.count(_link("card", later.id)) == 1
    assert "Sprint" not in text and "Backlog" not in text


async def test_the_dashboard_names_the_first_priority_goals(sessions) -> None:
    """HM-GOALS-013 — tests/brd/home.feature"""
    assert "Priority Goals" not in await _text(sessions)
    async with sessions() as session:
        critical = await create_card(session, kind="goal", title="Critical", priority="critical")
        others = [
            await create_card(session, kind="goal", title=f"Goal {n}", priority="low")
            for n in range(HOME_GOALS_SHOWN)
        ]
        await session.commit()

    lines = _block(await _text(sessions), "Priority Goals")

    assert len(lines) == 1 + HOME_GOALS_SHOWN
    assert _link("card", critical.id) in lines[1]
    assert _link("card", others[-1].id) not in "\n".join(lines)


async def test_the_dashboard_reads_in_one_order(sessions) -> None:
    """HM-ORDER-014 — tests/brd/home.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        await create_value(session, "Health", active=True)
        await create_card(session, kind="goal", title="Get fit")
        await _action(session, "Run", stage="today")
        await session.commit()
    now = datetime(2026, 10, 7, 11, 5, tzinfo=UTC)

    headings = [block.splitlines()[0] for block in (await _text(sessions, now=now)).split("\n\n")]

    assert headings[0].startswith("<b>🏠 Wed, 07 Oct · ")
    assert [heading.split("</b>")[0] for heading in headings[1:5]] == [
        "<b>🗒 Latest changes",
        "<b>🎯 Priority Goals",
        "<b>💎 Values in focus",
        "<b>☀️ Today · 1",
    ]
    assert headings[5].startswith("⌛ Tracked today")


class Words:
    """The model: words for each numbered Value it knows by name, or no call at all when it
    knows none. `hold` keeps every call open until the test lets it go."""

    def __init__(self, said: dict[str, str], *, hold: bool = False) -> None:
        self.said = said
        self.requests: list[CompletionRequest] = []
        self.hold = hold
        self.reached = asyncio.Event()
        self.go = asyncio.Event()

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.requests.append(request)
        if self.hold:
            self.reached.set()
            await self.go.wait()
        listed = request.messages[1]["content"].split("Values, numbered:\n")[1].splitlines()
        words = [
            {"value": int(number), "text": self.said[name]}
            for number, rest in (line.split(". ", 1) for line in listed)
            if (name := rest.split(" — ")[0]) in self.said
        ]
        if not words:
            return CompletionTurn(content="I would rather not.")
        arguments = json.dumps({"words": words})
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

    words = await Motivator(model, spawn=spawn_timer).write(sessions)

    assert words == {health.id: "A run today keeps it going."}
    # Every Value in one request, with reasoning off.
    [request] = model.requests
    assert request.reasoning_effort == "none"
    context = request.messages[1]["content"]
    assert "I run in the mornings." in context
    assert "- Run a marathon" in context
    finished = [line for line in context.splitlines() if line.startswith("- 2026-")]
    assert len(finished) == MOTIVATION_DONE_ACTIONS
    assert finished[0].endswith(done[-1].title)
    assert "Run 0" not in context
    diary = context.split("Diary, newest first:\n")[1].split("\n\n")[0].splitlines()
    assert len(diary) == MOTIVATION_DIARY_ENTRIES
    assert diary == ["2026-09-04: Slept well", "2026-09-02: A good run"]
    assert context.endswith("Values, numbered:\n1. Family\n2. Health — Body first")

    lines = _block(await _text(sessions, words), "Values in focus")
    assert lines[1].startswith(f'<a href="{_link("value", family.id)}">')
    assert lines[1].endswith("</a>")
    assert _link("value", health.id) in lines[2]
    assert lines[2].endswith(" — A run today keeps it going.")
    assert _link("value", tidy.id) not in "\n".join(lines)


async def _health(sessions) -> int:
    async with sessions() as session:
        health = await create_value(session, "Health", active=True)
        await session.commit()
        return health.id


@pytest.mark.parametrize("running", [False, True])
async def test_motivation_reads_ranked_goals_sprint_criteria_and_today(
    sessions, monkeypatch, running
) -> None:
    """HM-VALUES-007 — tests/brd/home.feature"""
    now = datetime(2026, 10, 7, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(motivation, "utcnow", lambda: now)
    await _health(sessions)
    async with sessions() as session:
        workspace = await require_workspace(session)
        workspace.sprint_success_criteria = "A draft for the next Sprint"
        await create_card(session, kind="goal", title="Low Goal", priority="low")
        await create_card(session, kind="goal", title="Critical Goal", priority="critical")
        await create_card(
            session, kind="goal", title="Deadline Goal", priority="low",
            schedule="2026-10-15T23:59:00+03:00",
            schedule_rule={"kind": "deadline", "date": "2026-10-15", "time": None},
        )
        closed = await create_card(session, kind="goal", title="Closed Goal")
        await finish_card(session, closed.id)
        await _action(session, "Ordinary Today Action", stage="today")
        await _action(session, "Critical Today Action", stage="today", priority="critical")
        await _action(session, "Backlog Action")
        await _action(session, "Sprint Action", stage="sprint")
        if running:
            await start_sprint(session, success_criteria="Run three times this Sprint")
            workspace.sprint_success_criteria = "A draft for the next Sprint"
        await session.commit()
    model = Words({"Health": "Move today."})

    await Motivator(model, spawn=spawn_timer).write(sessions)

    context = model.requests[0].messages[1]["content"]
    assert "Today: 2026-10-08" in context
    goals = context.split("Priority Goals, highest focus first:\n")[1].split("\n\n")[0]
    assert goals.splitlines() == [
        "- Deadline Goal (priority=low) deadline=2026-10-15 23:59",
        "- Critical Goal (priority=critical)",
        "- Low Goal (priority=low)",
    ]
    assert "Closed Goal" not in context
    today = context.split("Today Actions, in Today's order:\n")[1].split("\n\n")[0]
    assert today.splitlines() == [
        "- Critical Today Action (priority=critical)",
        "- Ordinary Today Action (priority=medium)",
    ]
    assert "Backlog Action" not in context and "Sprint Action" not in context
    assert "A draft for the next Sprint" not in context
    assert ("Run three times this Sprint" if running else "No Sprint is running.") in context


async def test_new_motivation_reads_recent_words_by_value_after_the_cache_expires(
    sessions, monkeypatch
) -> None:
    """HM-VALUES-007 — tests/brd/home.feature"""
    now = datetime(2026, 10, 8, 9, tzinfo=UTC)
    monkeypatch.setattr(motivation, "utcnow", lambda: now)
    health = await _health(sessions)
    model = Words({"Health": "Health message 0."})
    motivator = Motivator(model, spawn=spawn_timer)
    assert await motivator.write(sessions) == {health: "Health message 0."}
    assert "Previous motivation, newest first:\nNone." in model.requests[0].messages[1]["content"]
    async with sessions() as session:
        family = await create_value(session, "Family", active=True)
        await session.commit()

    for generation in range(1, MOTIVATION_HISTORY_SIZE + 2):
        now += timedelta(minutes=MOTIVATION_FRESH_MINUTES)
        model.said = {
            "Health": f"Health message {generation}.",
            "Family": f"Family message {generation}.",
        }
        words = await motivator.write(sessions)
        assert words == {
            health: f"Health message {generation}.",
            family.id: f"Family message {generation}.",
        }
        context = model.requests[-1].messages[1]["content"]
        previous = context.split("Previous motivation, newest first:\n")[1].split("\n\n")[0]
        assert [line for line in previous.splitlines() if line.startswith("- Value 2:")] == [
            f"- Value 2: Health message {index}."
            for index in range(generation - 1, max(-1, generation - MOTIVATION_HISTORY_SIZE - 1), -1)
        ]
        assert "- Value 1: Health" not in previous
        assert f"Health message {generation}." not in previous
        # Reusing fresh words must not add another generation to history.
        assert await motivator.write(sessions) == words
        assert len(model.requests) == generation + 1

    now += timedelta(minutes=MOTIVATION_FRESH_MINUTES)
    model.said = {}
    assert await motivator.write(sessions) == {}
    previous = model.requests[-1].messages[1]["content"]
    model.said = {"Health": "A fresh angle."}
    assert await motivator.write(sessions) == {health: "A fresh angle."}
    assert model.requests[-1].messages[1]["content"] == previous


async def test_words_are_shown_again_for_10_minutes_without_a_request(
    sessions, monkeypatch
) -> None:
    """HM-VALUES-007 — tests/brd/home.feature"""
    assert MOTIVATION_FRESH_MINUTES == 10
    health = await _health(sessions)
    model = Words({"Health": "Keep going."})
    motivator = Motivator(model, spawn=spawn_timer)
    assert motivator.fresh() is None

    assert await motivator.write(sessions) == {health: "Keep going."}
    later = datetime.now(UTC) + timedelta(minutes=MOTIVATION_FRESH_MINUTES) - timedelta(seconds=5)
    monkeypatch.setattr(motivation, "utcnow", lambda: later)
    assert motivator.fresh() == {health: "Keep going."}
    assert await motivator.write(sessions) == {health: "Keep going."}
    assert len(model.requests) == 1

    model.said = {"Health": "Once more."}
    later += timedelta(seconds=10)
    assert motivator.fresh() is None
    assert await motivator.write(sessions) == {health: "Once more."}
    assert len(model.requests) == 2


async def test_a_dashboard_waits_for_the_request_that_runs(sessions) -> None:
    """HM-VALUES-007 — tests/brd/home.feature"""
    health = await _health(sessions)
    model = Words({"Health": "Keep going."}, hold=True)
    motivator = Motivator(model, spawn=spawn_timer)

    first = asyncio.create_task(motivator.write(sessions))
    await asyncio.wait_for(model.reached.wait(), timeout=5)
    second = asyncio.create_task(motivator.write(sessions))
    await asyncio.sleep(0)
    # The dashboard that asked first is gone: the request goes on for the other.
    first.cancel()
    model.go.set()

    assert await asyncio.wait_for(second, timeout=5) == {health: "Keep going."}
    assert first.cancelled()
    assert len(model.requests) == 1
    assert motivator.fresh() == {health: "Keep going."}


async def test_no_words_are_kept_when_none_were_written(sessions) -> None:
    """HM-VALUES-007 — tests/brd/home.feature"""
    health = await _health(sessions)
    model = Words({})
    motivator = Motivator(model, spawn=spawn_timer)

    assert await motivator.write(sessions) == {}
    assert motivator.fresh() is None
    asked = len(model.requests)

    model.said = {"Health": "Keep going."}
    assert await motivator.write(sessions) == {health: "Keep going."}
    assert len(model.requests) == asked + 1


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


async def test_the_dashboard_opens_with_the_last_changes(sessions) -> None:
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
