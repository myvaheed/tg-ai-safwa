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
from safwa.features.cards.api import goal_tree
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, delete_card, finish_action
from safwa.features.diary.model import DiaryEntry
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.home import motivation
from safwa.features.home.dashboard import HOME_LOG_SHOWN, dashboard_text
from safwa.features.home.motivation import (
    MOTIVATION_DIARY_ENTRIES,
    MOTIVATION_DONE_ACTIONS,
    MOTIVATION_FRESH_MINUTES,
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
from safwa.features.values.use_cases import create_value, update_value_fields
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.workspace import require_workspace
from telegram_llm import Note, markdown_to_telegram_html
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramNotes, mark_cleared
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
        subgoal = await create_card(session, kind="goal", title="Write posts", parent_ids={goal.id})
        plain = await _action(session, "Pay rent", stage="today")
        post = await _action(session, "Write the first post", stage="today", parent_ids={subgoal.id})
        critical = await _action(session, "Call the bank", stage="today", priority="critical")
        domain = await _action(session, "Pick a domain", stage="today", parent_ids={goal.id})
        extra = await _action(session, "Extra", stage="today")
        await start_sprint(session, success_criteria="Ship it")
        await session.commit()

    lines = _block(await _text(sessions), "Today")

    assert lines[0] == "<b>☀️ Today · 5</b>"
    assert lines[1] == "Planned: 5 Actions"
    # Today's order is Critical, then oldest first; the Goal stands where its first Action
    # does, once, and its later Action comes up under it.
    rows = lines[2:]
    tree = [(critical, 0), (plain, 0), (goal, 0), (subgoal, 1), (post, 2), (domain, 1), (extra, 0)]
    assert len(rows) == len(tree)
    for row, (card, depth) in zip(rows, tree, strict=True):
        assert _link("card", card.id) in row
        assert len(row) - len(row.lstrip(" ")) == 4 * depth


async def _shared_post(session):
    """Launch the blog and Grow audience over First post, and Write it in Today under that."""
    blog = await create_card(session, kind="goal", title="Launch the blog")
    audience = await create_card(session, kind="goal", title="Grow audience")
    post = await create_card(
        session, kind="goal", title="First post", parent_ids={blog.id, audience.id}
    )
    write = await _action(session, "Write it", stage="today", parent_ids={post.id})
    return blog, audience, post, write


async def test_a_card_under_two_goals_stands_once_and_names_the_other(sessions) -> None:
    """HM-ACTIONS-006 — tests/brd/home.feature"""
    async with sessions() as session:
        blog, audience, post, write = await _shared_post(session)
        await session.commit()

    rows = _block(await _text(sessions), "Today")[2:]
    tree = [(blog, 0), (post, 1), (write, 2)]
    assert len(rows) == len(tree)
    for row, (card, depth) in zip(rows, tree, strict=True):
        assert _link("card", card.id) in row
        assert len(row) - len(row.lstrip(" ")) == 4 * depth
    assert "↖" in rows[1] and rows[1].index(_link("card", audience.id)) > rows[1].index("↖")
    assert not any("↖" in row for row in (rows[0], rows[2]))


def _drawn(rows) -> list[tuple[int, str, list[str]]]:
    return [(row.depth, row.card.title, [parent.title for parent in row.also_under]) for row in rows]


async def test_pl_key_025_a_card_under_several_goals_stands_under_the_higher_on_a_tie(sessions) -> None:
    """PL-KEY-025 — tests/brd/planning.feature"""
    async with sessions() as session:
        _blog, _audience, _post, write = await _shared_post(session)
        rows = await goal_tree(session, [write])
    assert _drawn(rows) == [
        (0, "Launch the blog", []), (1, "First post", ["Grow audience"]), (2, "Write it", []),
    ]


@pytest.mark.parametrize("higher,first,third", [
    ("A", "X", False), ("B", "X", False), ("A", "Y", False), ("A", "X", True),
])
async def test_pl_key_025_a_card_stands_under_the_goal_with_most_of_today(
    sessions, higher, first, third
) -> None:
    """PL-KEY-025 — tests/brd/planning.feature"""
    async with sessions() as session:
        goals = {
            title: await create_card(
                session, kind="goal", title=title,
                priority="critical" if title == higher else "medium",
            )
            for title in ("A", "B")
        }
        x = await _action(
            session, "X", stage="today", parent_ids={goals["A"].id, goals["B"].id},
            priority="critical" if first == "X" else "medium",
        )
        y = await _action(
            session, "Y", stage="today", parent_ids={goals["B"].id},
            priority="critical" if first == "Y" else "medium",
        )
        actions = [x, y] if first == "X" else [y, x]
        if third:
            actions.append(await _action(session, "Z", stage="today", parent_ids={goals["A"].id}))
        rows = await goal_tree(session, actions)

    if third:
        # A and B each hold two of Today's Actions, so X goes to the higher one.
        expected = [(0, "A", []), (1, "X", ["B"]), (1, "Z", []), (0, "B", []), (1, "Y", [])]
    else:
        under_b = [(1, "X", ["A"]), (1, "Y", [])] if first == "X" else [(1, "Y", []), (1, "X", ["A"])]
        expected = [(0, "B", []), *under_b]
    assert _drawn(rows) == expected


async def test_pl_context_020_the_advisor_reads_the_other_goals_a_card_stands_under(sessions) -> None:
    """PL-CONTEXT-020 — tests/brd/planning.feature"""
    async with sessions() as session:
        blog, audience, post, write = await _shared_post(session)
        state = (await workspace_context(session)).state
    lines = state.splitlines()
    assert f"- [{write.title}](card:{write.id}) under=[First post](card:{post.id})" in lines
    shared = next(line for line in lines if line.startswith(f"- [First post](card:{post.id})"))
    assert shared.endswith(
        f"under=[Launch the blog](card:{blog.id}), [{audience.title}](card:{audience.id})"
    )


async def test_home_keeps_goal_and_subgoal_effort_in_a_shared_hierarchy(sessions, effort_on):
    """HM-ACTIONS-006 — tests/brd/home.feature"""
    async with sessions() as session:
        blog, _audience, post, write = await _shared_post(session)
        write.effort_points = 3
        finished_post = await create_card(
            session, kind="action", title="Drafted", effort_points=2, parent_ids={post.id}
        )
        await finish_action(session, finished_post.id)
        finished_blog = await create_card(
            session, kind="action", title="Registered", effort_points=1, parent_ids={blog.id}
        )
        await finish_action(session, finished_blog.id)
        await create_card(session, kind="action", title="Domain", effort_points=2, parent_ids={blog.id})
        await session.commit()

    rows = _block(await _text(sessions), "Today")[2:]
    assert "⚡3/8" in next(row for row in rows if _link("card", blog.id) in row)
    assert "⚡2/5" in next(row for row in rows if _link("card", post.id) in row)
    assert sum(_link("card", write.id) in row for row in rows) == 1


async def test_motivator_keeps_the_other_parents_of_shared_cards(sessions):
    """HM-VALUES-007 — tests/brd/home.feature"""
    async with sessions() as session:
        await _shared_post(session)
        context = await motivation.owner_context(session)
    today = context.split("Today Actions, in Today's order, each under its Goals and Subgoals:\n")[1]
    assert today.split("\n\n")[0].splitlines() == [
        "- Launch the blog (priority=medium)",
        "  - First post (priority=medium) (also under: Grow audience)",
        "    - Write it (priority=medium)",
    ]


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


async def test_the_dashboard_reads_in_one_order(sessions) -> None:
    """HM-ORDER-014 — tests/brd/home.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        health = await create_value(session, "Health", "What matters to me")
        await create_card(session, kind="goal", title="Get fit", priority="critical")
        await _action(session, "Run", stage="today", value_ids={health.id})
        await session.commit()
    now = datetime(2026, 10, 7, 11, 5, tzinfo=UTC)

    text = await _text(sessions, now=now)
    headings = [block.splitlines()[0] for block in text.split("\n\n")]

    assert headings[0].startswith("<b>🏠 Wed, 07 Oct · ")
    assert [heading.split("</b>")[0] for heading in headings[1:4]] == [
        "<b>🗒 Latest changes",
        "<b>💎 Values in focus",
        "<b>☀️ Today · 1",
    ]
    assert headings[4].startswith("⌛ Tracked today")
    assert "Priority Goals" not in text


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
            if (name := rest.split(" (carried")[0].split(" — ")[0]) in self.said
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
        health = await create_value(session, "Health", "Body first")
        family = await create_value(session, "Family", "Calls home every Sunday")
        tidy = await create_value(session, "Tidiness", "What matters to me")
        marathon = await create_card(
            session, kind="goal", title="Run a marathon", value_ids={health.id}
        )
        await _action(session, "Morning run", stage="today", parent_ids={marathon.id})
        await _action(session, "Call home", stage="today", value_ids={family.id})
        await _action(session, "Tidy the desk", stage="sprint", value_ids={tidy.id})
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
    assert "- Run a marathon (priority=medium)\n  - Morning run (priority=medium)" in context
    finished = [line for line in context.splitlines() if line.startswith("- 2026-")]
    assert len(finished) == MOTIVATION_DONE_ACTIONS
    assert finished[0].endswith(done[-1].title)
    assert "Run 0" not in context
    diary = context.split("Diary, newest first:\n")[1].split("\n\n")[0].splitlines()
    assert len(diary) == MOTIVATION_DIARY_ENTRIES
    assert diary == ["2026-09-04: Slept well", "2026-09-02: A good run"]
    # A Value in focus comes with the Today Cards that carry it; one carried only in the
    # Sprint is not in focus.
    assert context.endswith(
        "Values, numbered:\n"
        "1. Health — Body first (carried today by: Run a marathon)\n"
        "2. Family — Calls home every Sunday (carried today by: Call home)"
    )

    lines = _block(await _text(sessions, words), "Values in focus")
    assert lines[1].startswith(f'<a href="{_link("value", health.id)}">')
    assert lines[1].endswith(" — A run today keeps it going.")
    assert _link("value", family.id) in lines[2]
    assert lines[2].endswith("</a>")
    assert _link("value", tidy.id) not in "\n".join(lines)


async def _in_focus(session, name: str) -> int:
    """A Value in focus: an Action in Today carries it."""
    value = await create_value(session, name, "What matters to me")
    await _action(session, f"Walk for {name}", stage="today", value_ids={value.id})
    return value.id


async def _health(sessions) -> int:
    async with sessions() as session:
        health = await _in_focus(session, "Health")
        await session.commit()
        return health


@pytest.mark.parametrize("running", [False, True])
async def test_motivation_reads_sprint_criteria_and_today_as_its_tree(
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
        blog = await create_card(session, kind="goal", title="Blog")
        posts = await create_card(session, kind="goal", title="Posts", parent_ids={blog.id})
        await _action(session, "Ordinary Today Action", stage="today")
        await _action(session, "Critical Today Action", stage="today", priority="critical")
        await _action(session, "Write a post", stage="today", parent_ids={posts.id})
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
    today = context.split("Today Actions, in Today's order, each under its Goals and Subgoals:\n")
    assert today[1].split("\n\n")[0].splitlines() == [
        "- Critical Today Action (priority=critical)",
        "- Walk for Health (priority=medium)",
        "- Ordinary Today Action (priority=medium)",
        "- Blog (priority=medium)",
        "  - Posts (priority=medium)",
        "    - Write a post (priority=medium)",
    ]
    assert "Low Goal" not in context and "Priority Goals" not in context
    assert "Backlog Action" not in context and "Sprint Action" not in context
    assert "A draft for the next Sprint" not in context
    assert ("Run three times this Sprint" if running else "No Sprint is running.") in context


async def test_new_motivation_reads_the_last_words_by_value_after_the_cache_expires(
    sessions, monkeypatch
) -> None:
    """HM-VALUES-007 — tests/brd/home.feature"""
    now = datetime(2026, 10, 8, 9, tzinfo=UTC)
    monkeypatch.setattr(motivation, "utcnow", lambda: now)
    health = await _health(sessions)
    model = Words({"Health": "Health message 0."})
    motivator = Motivator(model, spawn=spawn_timer)
    assert await motivator.write(sessions) == {health: "Health message 0."}
    assert "Previous motivation:\nNone." in model.requests[0].messages[1]["content"]
    async with sessions() as session:
        family = await _in_focus(session, "Family")
        await update_value_fields(session, family, order=1)
        await session.commit()

    for generation in range(1, 4):
        now += timedelta(minutes=MOTIVATION_FRESH_MINUTES)
        model.said = {
            "Health": f"Health message {generation}.",
            "Family": f"Family message {generation}.",
        }
        words = await motivator.write(sessions)
        assert words == {
            health: f"Health message {generation}.",
            family: f"Family message {generation}.",
        }
        context = model.requests[-1].messages[1]["content"]
        previous = context.split("Previous motivation:\n")[1].split("\n\n")[0]
        # Only the last words, each under the Value's number now: Family came first.
        assert previous.splitlines() == [
            *([f"- Value 1: Family message {generation - 1}."] if generation > 1 else []),
            f"- Value 2: Health message {generation - 1}.",
        ]
        # Reusing fresh words must not replace the last generation.
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
        await delete_card(session, gone.id)
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
    await keep(sessions, chat_id, 13, "🏠 The dashboard", MessageKind.DASHBOARD, at + timedelta(hours=1))
    async with sessions() as session:
        await mark_cleared(session, chat_id, 13)
        await session.commit()
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
