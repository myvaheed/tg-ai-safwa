"""Value priority is the display order wherever a live list is read."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from ui_harness import FakeMessage, button_texts, services_for

from safwa.features.cards.telegram import render_card, render_card_choices, render_card_creation
from safwa.features.cards.telegram.review import CardProposalPresenter
from safwa.features.cards.telegram.selectors import handle_card_creation_chooser
from safwa.features.cards.use_cases import create_card
from safwa.features.checks.telegram.review import CheckProposalPresenter
from safwa.features.checks.telegram.screens import render_check, render_check_values
from safwa.features.checks.use_cases import create_check, toggle_check_value
from safwa.features.compass.graph import read_tree, tree_text
from safwa.features.home.dashboard import dashboard_text
from safwa.features.life.records import life_records
from safwa.features.planning.api import values_in_focus
from safwa.features.values.api import value_names
from safwa.features.values.telegram import command_values
from safwa.features.values.use_cases import create_value, update_value_fields
from safwa.features.workspace_mutator.state import workspace_context
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.proposals.api import ChangeAction, ProposalChange
from tg_agent_shell.telegram.model import UiSession


@pytest_asyncio.fixture(params=[False, True], ids=["prioritized", "reprioritized"])
async def ranked_values(sessions, request):
    async with sessions() as session:
        values = [await create_value(session, name, "What matters") for name in ("Alpha", "Zulu", "Beta")]
        ids = [value.id for value in values]
        await update_value_fields(session, ids[1], order=1)
        if request.param:
            await update_value_fields(session, ids[2], order=1)
        card = await create_card(
            session, kind="action", title="Work", stage="today", value_ids=set(ids)
        )
        check = await create_check(session, title="Evidence?")
        for value_id in ids:
            await toggle_check_value(session, check.id, value_id)
        session.add(UiSession(
            owner_id=42, kind="card_create",
            state={"kind": "action", "title": "Draft", "value_ids": ids},
            expires_at=utcnow() + timedelta(hours=1),
        ))
        await session.commit()
        expected = ["Beta", "Zulu", "Alpha"] if request.param else ["Zulu", "Alpha", "Beta"]
        return ids, card.id, check.id, expected


def _in_order(text, names):
    positions = [text.index(name) for name in names]
    assert positions == sorted(positions), text


async def test_context_home_compass_and_life_read_value_priority(sessions, ranked_values):
    """VL-ORDER-029 — tests/brd/values.feature"""
    ids, card_id, check_id, expected = ranked_values
    async with sessions() as session:
        names = await value_names(session)
        assert list(names.values()) == expected
        assert [value.name for value, _ in await values_in_focus(session)] == expected
        _in_order(await dashboard_text(session, {}), expected)
        _in_order((await workspace_context(session)).state, expected)
        tree = await read_tree(session)
        assert [value.name for value in tree.values] == expected
        assert [names[value_id] for value_id in tree.card_values[card_id]] == expected
        assert [names[value_id] for value_id in tree.check_values[check_id]] == expected
        _in_order(tree_text(tree), expected)
        assert (await life_records(session, utcnow())).values == tuple(expected)


@pytest.mark.parametrize("surface", ["card", "check", "draft", "values"])
async def test_value_selectors_and_overviews_use_priority(sessions, ranked_values, surface):
    """VL-ORDER-029 — tests/brd/values.feature"""
    _, card_id, check_id, expected = ranked_values
    services = services_for(sessions)
    message = FakeMessage(810, bot_message=True)
    if surface == "card":
        await render_card(message, services, card_id, full=True)
        _in_order(message.edits[-1][0], expected)
        await render_card_choices(message, services, "card_choose_values", card_id)
    elif surface == "check":
        await render_check(message, services, check_id)
        _in_order(message.edits[-1][0], expected)
        await render_check_values(message, services, check_id)
    elif surface == "draft":
        await render_card_creation(message, services)
        _in_order(message.edits[-1][0], expected)
        await handle_card_creation_chooser(message, services, "card_create_choose_values")
    else:
        await command_values(message, services)
    _in_order("\n".join(button_texts(message.edits[-1][1])), expected)


@pytest.mark.parametrize("entity", ["card", "check"])
async def test_proposal_values_and_link_receipts_use_priority(sessions, ranked_values, entity):
    """VL-ORDER-029 — tests/brd/values.feature"""
    ids, card_id, check_id, expected = ranked_values
    presenter = CardProposalPresenter() if entity == "card" else CheckProposalPresenter()
    item_id = card_id if entity == "card" else check_id
    change = ProposalChange(entity, ChangeAction.UPDATE, item_id, values={"title": "New title"})
    async with sessions() as session:
        screen = await presenter.screen(session, [change])
        values = next(field for field in screen.fields if field.label == "Values")
        assert values.before == values.after == tuple(expected)
        link = ProposalChange(entity, ChangeAction.LINK, item_id, values={"values": ids})
        details = await presenter.details(session, link, None)
        _in_order(await presenter.summary(session, link, details), expected)
        if entity == "card":
            create = ProposalChange(
                entity, ChangeAction.CREATE,
                values={"kind": "action", "title": "New", "values": ids},
            )
            _in_order(await presenter.summary(session, create, []), expected)


async def test_sql_lists_and_linked_names_default_to_value_priority(read_views):
    """VL-ORDER-029 — tests/brd/values.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        alpha = await create_value(session, "Alpha", "What matters")
        zulu = await create_value(session, "Zulu", "What matters", order=1)
        beta = await create_value(session, "Beta", "What matters")
        ids = [alpha.id, zulu.id, beta.id]
        await create_card(session, kind="action", title="Work", value_ids=set(ids))
        check = await create_check(session, title="Evidence?")
        for value_id in ids:
            await toggle_check_value(session, check.id, value_id)
        await session.commit()
    for expected, limit in [("Zulu,Alpha,Beta", ["Zulu", "Alpha"]), ("Beta,Zulu,Alpha", ["Beta", "Zulu"])]:
        rows = (await runner.run("SELECT name FROM ai_values LIMIT 2")).rows
        assert [row["name"] for row in rows] == limit
        for view in ("ai_cards", "ai_checks"):
            assert (await runner.run(f"SELECT direct_values FROM {view}")).rows == [
                {"direct_values": expected}
            ]
        async with sessions() as session:
            await update_value_fields(session, beta.id, order=1)
            await session.commit()
    rows = (await runner.run("SELECT name FROM ai_values ORDER BY name LIMIT 2")).rows
    assert [row["name"] for row in rows] == ["Alpha", "Beta"]
