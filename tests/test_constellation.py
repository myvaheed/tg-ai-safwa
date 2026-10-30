"""The Constellation: the sky of Values, one Value's orbits and one Goal's branch, asked for in
words or from the Dashboard."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
from schedule_helpers import create_card, create_check
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from ui_harness import FakeCallback, FakeMessage, place_of, services_for

import safwa.features.compass.agent as compass_agent
from llm_gateway import ToolCall
from safwa.features.cards.model import CardCheck, CardStage
from safwa.features.cards.telegram import render_board
from safwa.features.cards.use_cases import (
    archive_subtree,
    finish_action,
    finish_card,
    move_card,
    toggle_card_parent,
    update_card_fields,
)
from safwa.features.checks.use_cases import resolve_check
from safwa.features.compass.agent import COMPASS_AGENT, show_constellation_tool
from safwa.features.compass.constellation import (
    COLUMN,
    ORBIT_SQUASH,
    ORBITS_CENTRE,
    STAR_RADIUS,
    read_branch,
    read_orbits,
    read_sky,
)
from safwa.features.compass.weight import AFTER_COMPLETION_RUNS, SKY_WINDOW_DAYS, weigh
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.values.model import CheckValue
from safwa.features.values.use_cases import create_value
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.telegram import Place, callback_token_handler
from tg_agent_shell.telegram.manifest import AgentContext

# What counts for Health in `tree`: Get fit (tree_level 2), Run 5 km (1), Run (after each
# completion), Stretch (every day), Buy shoes and Slept?.
HEALTH = 3 + 2 + math.sqrt(AFTER_COMPLETION_RUNS) + math.sqrt(SKY_WINDOW_DAYS) + 1 + 1


def now() -> datetime:
    return datetime.now(UTC)


async def tree(sessions) -> SimpleNamespace:
    """Get fit (Health) over Run 5 km over Run, repeating after each completion, and Stretch
    (Health), repeating every day, with Buy shoes (Family) beside them and Slept? (Health) on
    Run; Earn, a root Goal with no Value, over Call Bob (Family); Calm, which nothing carries;
    and what is not drawn: a Thought, Old run and Gone, Done under Get fit, Gone archived too,
    Loose end, Done under nothing, and Answered?, passed."""
    async with sessions() as session:
        calm = (await create_value(session, "Calm", "Quiet evenings")).id
        health = (await create_value(session, "Health", "Sleep and move")).id
        family = (await create_value(session, "Family", "Time together")).id
        fit = await create_card(session, kind="goal", title="Get fit", value_ids={health})
        sub = await create_card(session, kind="goal", title="Run 5 km", parent_ids={fit.id})
        run = await create_card(
            session, kind="action", title="Run", parent_ids={sub.id}, schedule="after completion"
        )
        stretch = await create_card(
            session, kind="action", title="Stretch", parent_ids={sub.id}, value_ids={health},
            schedule="daily 07:00",
        )
        shoes = await create_card(
            session, kind="action", title="Buy shoes", parent_ids={fit.id}, value_ids={family}
        )
        earn = await create_card(session, kind="goal", title="Earn $20k")
        bob = await create_card(
            session, kind="action", title="Call Bob", parent_ids={earn.id}, value_ids={family},
            blocked_description="Bob is away",
        )
        await create_card(session, kind="thought", title="Some idea")
        gone = await create_card(session, kind="action", title="Gone", parent_ids={fit.id})
        await finish_action(session, gone.id)
        await archive_subtree(session, gone.id)
        old = await create_card(
            session, kind="action", title="Old run", parent_ids={fit.id}, effort_points=3
        )
        await finish_action(session, old.id, tracked_mins=90)
        loose = await create_card(session, kind="action", title="Loose end")
        await finish_action(session, loose.id)
        slept = await create_check(session, title="Slept?")
        session.add(CheckValue(check_id=slept.id, value_id=health))
        session.add(CardCheck(card_id=run.id, check_id=slept.id))
        answered = await create_check(session, title="Answered?")
        await resolve_check(session, answered.id, "passed")
        await move_card(session, shoes.id, CardStage.SPRINT)
        await session.commit()
        return SimpleNamespace(
            calm=calm, health=health, family=family, fit=fit.id, sub=sub.id, run=run.id,
            stretch=stretch.id, shoes=shoes.id, earn=earn.id, bob=bob.id, old=old.id,
            slept=slept.id,
        )


def card(card_id: int) -> tuple[str, int]:
    return ("card", card_id)


def nodes(picture):
    return {node.item: node for node in picture.nodes}


# ----------------------------------------------------------------------- the weight


async def test_a_value_weighs_the_open_work_that_counts_for_it(sessions):
    """CMP-WEIGHT-006 — tests/brd/compass.feature"""
    ids = await tree(sessions)
    async with sessions() as session:
        await update_card_fields(session, ids.shoes, {"priority": "critical"})
        await session.commit()
        weighed = await weigh(session, now())
    weight = weighed.weight
    assert weight[card(ids.fit)] == 3 and weight[card(ids.sub)] == 2 and weight[card(ids.earn)] == 2
    assert weighed.runs[card(ids.run)] == AFTER_COMPLETION_RUNS
    assert weight[card(ids.run)] == math.sqrt(AFTER_COMPLETION_RUNS)
    assert weighed.runs[card(ids.stretch)] == SKY_WINDOW_DAYS
    assert weight[card(ids.stretch)] == math.sqrt(SKY_WINDOW_DAYS)
    assert weight[card(ids.shoes)] == weight[card(ids.bob)] == weight[("check", ids.slept)] == 1
    # Run 5 km and Run count for Health through Get fit, Slept? through Run.
    health = {item for item, values in weighed.counts.items() if ids.health in values}
    assert health == {
        card(ids.fit), card(ids.sub), card(ids.run), card(ids.stretch), card(ids.shoes),
        ("check", ids.slept),
    }
    assert math.isclose(weighed.of(ids.health), HEALTH)
    assert weighed.of(ids.family) == 2 and weighed.of(ids.calm) == 0
    assert weighed.shared(ids.health, ids.family) == 1
    # A Thought, the finished, the archived and the answered weigh nothing.
    assert {item[1] for item in weight if item[0] == "card"} == {
        ids.fit, ids.sub, ids.run, ids.stretch, ids.shoes, ids.earn, ids.bob,
    }
    assert [item for item in weight if item[0] == "check"] == [("check", ids.slept)]


# ----------------------------------------------------------------------- the three pictures


async def test_the_sky_shows_each_value_as_big_as_its_weight(sessions):
    """CMP-SKY-007 — tests/brd/compass.feature"""
    async with sessions() as session:
        assert await read_sky(session, now()) is None
    ids = await tree(sessions)
    async with sessions() as session:
        sky = await read_sky(session, now())
    calm, health, family = sky.stars
    assert [star.name for star in sky.stars] == ["Calm", "Health", "Family"]
    assert [star.rank for star in sky.stars] == [1, 2, 3]
    assert calm.x < health.x < family.x
    assert health.radius == STAR_RADIUS[1]
    assert math.isclose(family.radius, STAR_RADIUS[1] * math.sqrt(2 / HEALTH))
    assert calm.radius == 0 and calm.flagged and not health.flagged
    assert sky.shared == ((ids.health, ids.family, 1),)
    assert sky.nodes == ()
    assert sky.doors == (("value", ids.health, "Health"), ("value", ids.family, "Family"))


async def test_one_value_has_what_counts_for_it_on_orbits_around_it(sessions):
    """CMP-SKY-008 — tests/brd/compass.feature"""
    ids = await tree(sessions)
    async with sessions() as session:
        orbits = await read_orbits(session, ids.health, now())
    [star] = orbits.stars
    assert (star.x, star.y) == ORBITS_CENTRE and star.name == "Health"
    assert orbits.heading == "Health"
    assert orbits.subtitle.startswith("Rank 2 · weight 14 · 1st of 3 by weight")
    at = nodes(orbits)
    assert set(at) == {
        card(ids.fit), card(ids.sub), card(ids.run), card(ids.stretch), card(ids.shoes),
        ("check", ids.slept),
    }
    assert [at[card(each)].level for each in (ids.fit, ids.sub, ids.run, ids.shoes)] == [0, 1, 2, 2]
    assert at[("check", ids.slept)].level == 2
    for node in orbits.nodes:
        reach = math.hypot(node.x - star.x, (node.y - star.y) / ORBIT_SQUASH)
        assert math.isclose(reach, orbits.orbits[node.level])
    rays = dict(orbits.rays)
    assert set(rays) == {card(ids.fit), card(ids.stretch), ("check", ids.slept)}
    assert rays[card(ids.fit)] == 3 + 2 + 2 + 1
    assert math.isclose(sum(rays.values()), HEALTH)
    assert (card(ids.fit), card(ids.sub)) in orbits.branches
    assert (card(ids.run), ("check", ids.slept)) in orbits.branches
    assert at[card(ids.shoes)].values == (ids.family,) and at[card(ids.fit)].values == ()
    assert orbits.doors == (("goal", ids.fit, "Get fit"),)


async def test_a_goals_branch_reads_from_left_to_right_under_the_goals_above(sessions):
    """CMP-SKY-009 — tests/brd/compass.feature"""
    ids = await tree(sessions)
    async with sessions() as session:
        await toggle_card_parent(session, ids.stretch, ids.fit)
        await session.commit()
        sub = await read_branch(session, ids.sub, now())
        fit = await read_branch(session, ids.fit, now())
        earn = await read_branch(session, ids.earn, now())
        assert await read_branch(session, ids.run, now()) is None
    at = nodes(sub)
    assert set(at) == {card(ids.sub), card(ids.run), card(ids.stretch), ("check", ids.slept), card(ids.fit)}
    ghost = at[card(ids.fit)]
    assert ghost.ghost and ghost.level == -1 and (card(ids.fit), card(ids.sub)) in sub.branches
    assert math.isclose(at[card(ids.sub)].x - ghost.x, COLUMN)
    assert math.isclose(at[card(ids.run)].x - at[card(ids.sub)].x, COLUMN)
    assert math.isclose(at[("check", ids.slept)].x - at[card(ids.run)].x, COLUMN)
    assert math.isclose(at[card(ids.sub)].y, (at[card(ids.run)].y + at[card(ids.stretch)].y) / 2)
    assert at[card(ids.run)].repeats and not at[card(ids.sub)].repeats
    assert at[card(ids.stretch)].values == (ids.health,)

    at = nodes(fit)
    assert not any(node.ghost for node in fit.nodes)
    assert [node.item for node in fit.nodes].count(card(ids.stretch)) == 1
    assert {(card(ids.sub), card(ids.stretch)), (card(ids.fit), card(ids.stretch))} <= set(fit.branches)
    # Buy shoes is open, Old run and the archived Gone Done, and the rest repeat.
    assert at[card(ids.fit)].progress == 2 / 3 and at[card(ids.sub)].progress is None
    assert at[card(ids.shoes)].moving and not at[card(ids.run)].moving

    at = nodes(earn)
    assert at[card(ids.bob)].blocked and at[card(ids.earn)].flagged


async def test_each_picture_has_a_panel_of_what_was_done_and_what_is_left(sessions):
    """CMP-STATS-010 — tests/brd/compass.feature"""
    ids = await tree(sessions)
    async with sessions() as session:
        sky = await read_sky(session, now())
        health = await read_orbits(session, ids.health, now())
        fit = await read_branch(session, ids.fit, now())
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        await session.commit()
        tracked = await read_sky(session, now())
        tracked_fit = await read_branch(session, ids.fit, now())
    assert sky.panel.heading == f"Last {SKY_WINDOW_DAYS} days"
    assert sky.panel.rows == (
        ("✓", "3 Done"),
        ("◆", "1 of 1 Check passed"),
        ("○", "4 open · 1 in Sprint or Today"),
        ("·", "No Value: 1 Done"),
        ("·", "Values at 0: 1"),
    )
    weekly = (AFTER_COMPLETION_RUNS + SKY_WINDOW_DAYS) * 7 / SKY_WINDOW_DAYS
    assert health.panel.rows == (
        ("✓", "2 Done"),
        ("◆", "0 of 0 Checks passed"),
        ("○", "3 open · 1 in Sprint or Today"),
        ("↻", f"2 repeating Actions · {weekly:.0f} a week"),
    )
    assert fit.panel.heading == "All time" and fit.panel.share == 2 / 3
    assert fit.panel.rows[:2] == (
        ("✓", "2 of 3 Actions Done · 67%"),
        ("○", "3 open · 0 Blocked"),
    )
    assert fit.panel.rows[-1][1].startswith("Created ")
    assert ("⚡", "3 EP Done") in tracked.panel.rows and ("⏱", "1h 30m tracked") in tracked.panel.rows
    assert ("⚡", "3 EP Done · 0 ahead") in tracked_fit.panel.rows


# ----------------------------------------------------------------------- asked in words


async def test_compass_sends_the_picture_as_a_file_and_refuses_a_wrong_call(sessions, monkeypatch):
    """CMP-SKY-004 — tests/brd/compass.feature"""
    services = services_for(sessions)
    owner = QueueTestMessage(message_id=980, is_bot=False, answer_as_new=True)
    monkeypatch.setattr(compass_agent, "owner_anchor", lambda bot, owner_id: owner)
    context = AgentContext(
        owner_id=42,
        timezone="Europe/Istanbul",
        query_runner=None,  # type: ignore[arg-type]
        history=None,  # type: ignore[arg-type]
        sessions=sessions,
        chat=services.chat,
        bot=owner.bot,
    )
    tool = show_constellation_tool(context)
    assert "as a picture" in COMPASS_AGENT.purpose
    assert [spec.schema["function"]["name"] for spec in COMPASS_AGENT.read_tools(context)] == [
        "get_more_about", "show_constellation",
    ]

    async def show(**arguments) -> dict:
        return await tool.run(
            ToolCall(id="sky", name="show_constellation", arguments_json=json.dumps(arguments))
        )

    empty = await show()
    assert empty["sent"] is None and "Nothing to draw yet" in empty["note"]
    assert owner.bot.documents_sent == []

    ids = await tree(sessions)
    shown = await show()
    assert shown["sent"] == "Constellation"
    assert "Do not call show_constellation again" in shown["next"]
    [(document, caption)] = owner.bot.documents_sent
    assert document.filename == "constellation.png" and caption is None
    assert Image.open(BytesIO(document.data)).format == "PNG"
    async with sessions() as session:
        kept = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == owner.sent[-1].message_id)
        )
    assert kept.kind == MessageKind.RECEIPT.value and kept.text is None
    assert (await show(value="health"))["sent"] == "Constellation · Health"
    assert (await show(goal=ids.sub))["sent"] == "Constellation · Run 5 km"
    # A Value nothing carries is drawn as a dim star alone.
    assert (await show(value="calm"))["sent"] == "Constellation · Calm"

    async with sessions() as session:
        await finish_action(session, ids.bob)
        await finish_card(session, ids.earn)
        await session.commit()
    sent = len(owner.bot.documents_sent)
    for arguments, hint in (
        ({"value": "Wealth"}, 'Retry with "value" one of: Calm, Health, Family.'),
        ({"goal": 999}, "Find its id in ai_cards with kind = 'goal', or call without goal."),
        ({"goal": ids.run}, "Find its id in ai_cards with kind = 'goal', or call without goal."),
        ({"goal": ids.earn}, "Call with an open Goal, or without goal."),
        ({"value": "Health", "goal": ids.fit}, "Retry with one of them, or none."),
    ):
        refused = await show(**arguments)
        assert refused["status"] == "error" and refused["hint"] == hint
    assert len(owner.bot.documents_sent) == sent


# ----------------------------------------------------------------------- the Dashboard


def pressed(markup, text: str) -> str:
    [button] = [button for row in markup.inline_keyboard for button in row if button.text == text]
    return button.callback_data.split(":", 1)[1]


async def test_the_dashboard_button_opens_the_sky_and_leads_down_and_back(sessions):
    """CMP-SKY-005 — tests/brd/compass.feature"""
    services = services_for(sessions)
    board = FakeMessage(96, bot_message=True)
    await render_board(board, services)
    markup = board.edits[-1][1]
    dashboard = Place("board", {"kinds": "actions"})
    assert await place_of(sessions, markup, "🌌 Constellation") == Place("constellation", {}, dashboard)

    # Nothing to draw: it says so and sends nothing.
    shown = QueueTestMessage(message_id=97, answer_as_new=True)
    await callback_token_handler(FakeCallback(pressed(markup, "🌌 Constellation"), shown), services)
    assert shown.bot.documents_sent == []
    assert "Nothing to draw yet: add a Value or a Goal first." in shown.bot.drawn[-1]

    ids = await tree(sessions)
    await render_board(board, services)
    markup = board.edits[-1][1]
    shown = QueueTestMessage(message_id=98, answer_as_new=True)
    await callback_token_handler(FakeCallback(pressed(markup, "🌌 Constellation"), shown), services)
    [(document, _)] = shown.bot.documents_sent
    assert document.filename == "constellation.png"
    assert 98 in shown.bot.deleted
    file, words = shown.sent[-2:]
    assert words.text == (
        "<b>🌌 Constellation</b>\nOpen the file to zoom in.\n"
        "Ringed in red: 1. Ask the Advisor about them."
    )
    assert words.buttons() == ["Health", "Family", "↩️ Back"]
    sky = words.markups[-1]
    assert await place_of(sessions, sky, "Health") == Place("constellation", {"value": ids.health}, dashboard)

    await callback_token_handler(FakeCallback(pressed(sky, "Health"), words), services)
    assert len(shown.bot.documents_sent) == 2 and words.message_id in shown.bot.deleted
    orbits = shown.sent[-1]
    assert orbits.text.startswith("<b>🌌 Constellation · Health</b>")
    assert orbits.buttons() == ["Get fit", "↩️ Back"]

    await callback_token_handler(FakeCallback(pressed(orbits.markups[-1], "Get fit"), orbits), services)
    branch = shown.sent[-1]
    assert len(shown.bot.documents_sent) == 3
    assert branch.text.startswith("<b>🌌 Constellation · Get fit</b>")
    assert branch.buttons() == ["↩️ Back"]

    await callback_token_handler(
        FakeCallback(pressed(branch.markups[-1], "↩️ Back"), branch), services
    )
    assert file.message_id not in shown.bot.deleted
    assert "🗂 Dashboard" in shown.bot.drawn[-1]
