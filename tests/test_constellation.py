"""The Constellation: the tree as one picture, asked for in words or from the Dashboard."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
from schedule_helpers import create_card, create_check
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from ui_harness import FakeCallback, FakeMessage, button_texts, place_of, services_for

import safwa.features.compass.agent as compass_agent
from llm_gateway import ToolCall
from safwa.constants import INBOX_TAG_NAME
from safwa.features.cards.model import CardCheck
from safwa.features.cards.telegram import render_board
from safwa.features.cards.use_cases import finish_action, finish_card
from safwa.features.compass.agent import COMPASS_AGENT, show_constellation_tool
from safwa.features.compass.charts import SKY_DPI, WIDTH_INCHES, draw_sky
from safwa.features.compass.constellation import (
    CHECK_OFFSET,
    ROW,
    SKY_ABOVE,
    Detail,
    read_sky,
)
from safwa.features.tags.model import Tag
from safwa.features.tags.use_cases import seed_inbox_tag
from safwa.features.values.model import CheckValue
from safwa.features.values.use_cases import create_value
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.telegram import Place, callback_token_handler
from tg_agent_shell.telegram.manifest import AgentContext

DAY = date(2026, 10, 14)


async def tree(sessions) -> SimpleNamespace:
    """Get fit (Health) over Run 5 km over Run and Stretch, with Buy shoes beside them; Earn,
    a root Goal with no Value; Calm, which nothing carries; and what is not drawn."""
    async with sessions() as session:
        await seed_inbox_tag(session)
        inbox = await session.scalar(select(Tag.id).where(Tag.name == INBOX_TAG_NAME))
        calm = (await create_value(session, "Calm", "Quiet evenings")).id
        health = (await create_value(session, "Health", "Sleep and move")).id
        fit = await create_card(session, kind="goal", title="Get fit", value_ids={health})
        sub = await create_card(session, kind="goal", title="Run 5 km", parent_id=fit.id)
        run = await create_card(session, kind="action", title="Run", parent_id=sub.id)
        stretch = await create_card(session, kind="action", title="Stretch", parent_id=sub.id)
        shoes = await create_card(session, kind="action", title="Buy shoes", parent_id=fit.id)
        earn = await create_card(session, kind="goal", title="Earn $20k")
        bob = await create_card(session, kind="action", title="Call Bob", parent_id=earn.id)
        await create_card(session, kind="goal", title="Some idea", tag_ids={inbox})
        gone = await create_card(session, kind="action", title="Gone", parent_id=fit.id)
        gone.archived_at = datetime.now(UTC)
        old = await create_card(session, kind="action", title="Old run", parent_id=fit.id)
        await finish_action(session, old.id)
        slept = await create_check(session, title="Slept?")
        session.add(CheckValue(check_id=slept.id, value_id=health))
        session.add(CardCheck(card_id=run.id, check_id=slept.id))
        answered = await create_check(session, title="Answered?")
        answered.outcome = "passed"
        await session.commit()
        return SimpleNamespace(
            calm=calm, health=health, fit=fit.id, sub=sub.id, run=run.id, stretch=stretch.id,
            shoes=shoes.id, earn=earn.id, bob=bob.id, old=old.id, slept=slept.id,
        )


def stars(sky):
    return {star.item: star for star in sky.stars}


# ----------------------------------------------------------------------- what is drawn


async def test_the_values_stand_above_and_each_goal_as_high_as_its_level(sessions):
    """CMP-SKY-004 — tests/brd/compass.feature"""
    async with sessions() as session:
        assert await read_sky(session) is None
    ids = await tree(sessions)
    async with sessions() as session:
        sky = await read_sky(session)
    at = stars(sky)
    assert {star.title for star in sky.stars} == {
        "Calm", "Health", "Get fit", "Run 5 km", "Run", "Stretch", "Buy shoes", "Earn $20k",
        "Call Bob",
    }
    card = {name: at[("card", getattr(ids, name))] for name in
            ("fit", "sub", "run", "stretch", "shoes", "earn", "bob")}
    assert [card[name].kind for name in ("fit", "sub", "run", "earn")] == [
        "goal", "subgoal", "action", "goal",
    ]
    assert {name: star.y for name, star in card.items()} == {
        "fit": 2 * ROW, "sub": ROW, "run": 0, "stretch": 0, "shoes": 0, "earn": ROW, "bob": 0,
    }
    assert card["sub"].x == (card["run"].x + card["stretch"].x) / 2
    assert card["fit"].x == (card["sub"].x + card["shoes"].x) / 2
    assert sky.top == 2 * ROW + SKY_ABOVE
    assert {at[("value", ids.health)].y, at[("value", ids.calm)].y} == {sky.top}
    # A Value keeps its colour by its place among them all by name; a Card takes its own
    # Value's, else its parent's.
    assert sky.palette == {ids.calm: 0, ids.health: 1}
    assert card["run"].hue == ids.health and card["bob"].hue is None
    assert (ids.health, ("card", ids.fit)) in sky.links
    assert (("card", ids.fit), ("card", ids.sub)) in sky.branches
    assert (("card", ids.sub), ("card", ids.run)) in sky.branches
    assert {star.item for star in sky.stars if star.flagged} == {
        ("card", ids.earn), ("value", ids.calm),
    }


async def test_detail_done_and_one_value_or_goal_choose_what_is_drawn(sessions):
    """CMP-SKY-004 — tests/brd/compass.feature"""
    ids = await tree(sessions)
    async with sessions() as session:
        goals = await read_sky(session, detail=Detail.GOALS)
        every = await read_sky(session, detail=Detail.ALL)
        finished = await read_sky(session, done=True)
        health = await read_sky(session, value=ids.health)
        branch = await read_sky(session, goal=ids.sub)
    assert {star.kind for star in goals.stars} == {"value", "goal", "subgoal"}
    assert stars(goals)[("card", ids.fit)].y == 2 * ROW

    at = stars(every)
    run, slept = at[("card", ids.run)], at[("check", ids.slept)]
    assert (slept.x, slept.y) == (run.x + CHECK_OFFSET[0], run.y + CHECK_OFFSET[1])
    assert (("card", ids.run), ("check", ids.slept)) in every.branches
    assert [star.title for star in every.stars if star.kind == "check"] == ["Slept?"]

    assert stars(finished)[("card", ids.old)].done
    assert not stars(finished)[("card", ids.run)].done

    assert health.title == "Constellation · Health"
    assert {star.title for star in health.stars} == {
        "Health", "Get fit", "Run 5 km", "Run", "Stretch", "Buy shoes",
    }
    assert branch.title == "Constellation · Run 5 km"
    assert {star.title for star in branch.stars} == {"Run 5 km", "Run", "Stretch"}


async def test_the_sky_is_drawn_as_one_wide_picture(sessions):
    """CMP-SKY-004 — tests/brd/compass.feature"""
    await tree(sessions)
    async with sessions() as session:
        sky = await read_sky(session, detail=Detail.ALL)
    picture = Image.open(BytesIO(draw_sky(sky, DAY)))
    assert picture.format == "PNG"
    assert picture.width >= WIDTH_INCHES[0] * SKY_DPI - 1
    assert picture.width > picture.height


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
    async with sessions() as session:
        kept = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == owner.sent[-1].message_id)
        )
    assert kept.kind == MessageKind.RECEIPT.value and kept.text is None
    assert (await show(value="health", detail="all"))["sent"] == "Constellation · Health"
    assert (await show(goal=ids.sub))["sent"] == "Constellation · Run 5 km"

    async with sessions() as session:
        await finish_action(session, ids.bob)
        await finish_card(session, ids.earn)
        await session.commit()
    sent = len(owner.bot.documents_sent)
    for arguments, hint in (
        ({"value": "Wealth"}, 'Retry with "value" one of: Calm, Health.'),
        ({"goal": 999}, "Find its id in ai_cards with kind = 'goal', or call without goal."),
        ({"goal": ids.run}, "Find its id in ai_cards with kind = 'goal', or call without goal."),
        ({"goal": ids.earn}, 'Retry with "done": true.'),
        ({"value": "Health", "goal": ids.fit}, "Retry with one of them, or none."),
        ({"detail": "everything"}, 'Retry with "detail" one of: goals, actions, all, or without it.'),
    ):
        refused = await show(**arguments)
        assert refused["status"] == "error" and refused["hint"] == hint
    assert len(owner.bot.documents_sent) == sent
    assert (await show(goal=ids.earn, done=True))["sent"] == "Constellation · Earn $20k"


# ----------------------------------------------------------------------- the Dashboard


async def test_the_dashboard_button_sends_the_whole_tree_and_back_takes_it_out(sessions):
    """CMP-SKY-005 — tests/brd/compass.feature"""
    services = services_for(sessions)
    board = FakeMessage(96, bot_message=True)
    await render_board(board, services)
    markup = board.edits[-1][1]
    assert "🌌 Constellation" in button_texts(markup)
    sky = Place("constellation", {}, Place("board", {"kinds": "actions"}))
    assert await place_of(sessions, markup, "🌌 Constellation") == sky
    [button] = [
        button for row in markup.inline_keyboard for button in row
        if button.text == "🌌 Constellation"
    ]
    token = button.callback_data.split(":", 1)[1]

    # Nothing to draw: it says so and sends nothing.
    shown = QueueTestMessage(message_id=97, answer_as_new=True)
    nothing = FakeCallback(token, shown)
    await callback_token_handler(nothing, services)
    assert shown.bot.documents_sent == []
    assert "Nothing to draw yet: add a Value or a Goal first." in shown.bot.drawn[-1]

    await tree(sessions)
    await render_board(board, services)
    markup = board.edits[-1][1]
    [button] = [
        button for row in markup.inline_keyboard for button in row
        if button.text == "🌌 Constellation"
    ]
    shown = QueueTestMessage(message_id=98, answer_as_new=True)
    await callback_token_handler(FakeCallback(button.callback_data.split(":", 1)[1], shown), services)
    [(document, _)] = shown.bot.documents_sent
    assert document.filename == "constellation.png"
    assert 98 in shown.bot.deleted
    file, words = shown.sent[-2:]
    assert words.text == (
        "<b>🌌 Constellation</b>\nOpen the file to zoom in.\n"
        "Ringed in red: 2. Ask the Advisor about them."
    )
    assert words.buttons() == ["↩️ Back"]
    back = words.markups[-1].inline_keyboard[0][0]
    await callback_token_handler(FakeCallback(back.callback_data.split(":", 1)[1], words), services)
    assert file.message_id not in shown.bot.deleted
    assert "🗂 Dashboard" in shown.bot.drawn[-1]
