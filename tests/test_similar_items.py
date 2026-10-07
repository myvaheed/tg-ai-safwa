"""Similar items on a creating review screen: the comparison, what is open, and the screen."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from database_key import keyed
from scored_encoder import Scored
from ui_harness import FakeMessage, services_for

from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS, PROPOSALS, TEXT_MODEL, WORD_FORMS
from safwa.features.cards.use_cases import archive_subtree, create_card, finish_action
from safwa.features.checks.use_cases import archive_check, create_check, resolve_check
from safwa.features.reminders.api import resolve
from safwa.features.reminders.use_cases import create_reminder
from safwa.features.saved_requests.use_cases import create_saved_request
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from safwa.foundation.workspace import Workspace
from tg_agent_shell.proposals.model import ChangeAction, ProposalChange
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.proposals.telegram import render_proposal
from tg_agent_shell.proposals.telegram.screens import SIMILAR_HEADING, SIMILAR_ITEMS_SHOWN
from tg_agent_shell.search.index import SearchIndex

TZ = ZoneInfo("UTC")


def index(tmp_path, encoder, text_model=TEXT_MODEL) -> SearchIndex:
    """The application's index over the `sessions` fixture's database."""
    return SearchIndex(
        keyed(tmp_path / "sessions.db"), text_model, lambda: encoder, WORD_FORMS, AI_VIEWS
    )


async def loaded(tmp_path, encoder, text_model=TEXT_MODEL) -> SearchIndex:
    search = index(tmp_path, encoder, text_model)
    await search.load()
    return search


async def _tags(sessions, *names: str) -> list[int]:
    async with sessions() as session:
        ids = [(await create_tag(session, name)).id for name in names]
        await session.commit()
    return ids


async def test_pr_similar_030_the_closest_above_the_threshold_are_listed_closest_first(
    sessions, tmp_path
):
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    names = ("just above", "closest", "just below", "third", "second")
    ids = dict(zip(names, await _tags(sessions, *names), strict=True))
    encoder = Scored(
        "new",
        {
            "just above": TEXT_MODEL.alike + 0.001,
            "closest": 0.99,
            "just below": TEXT_MODEL.alike - 0.001,
            "third": 0.9,
            "second": 0.95,
        },
    )
    search = await loaded(tmp_path, encoder)

    listed = await search.alike("tag", "name", "new", ids.values())
    assert listed == [ids["closest"], ids["second"], ids["third"], ids["just above"]]
    assert await search.alike("tag", "name", "new", [ids["just above"], ids["just below"]]) == [
        ids["just above"]
    ]
    # Each saved name is encoded once and kept in the index; a screen encodes only its own.
    assert sorted(encoder.encoded) == sorted([*names, "new", "new"])


async def test_pr_similar_030_nothing_is_compared_until_the_model_loads_or_once_it_failed(
    sessions, tmp_path
):
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    [same] = await _tags(sessions, "same")
    encoder = Scored("new", {"same": 1.0})
    assert await index(tmp_path, encoder).alike("tag", "name", "new", [same]) == []

    def unreachable():
        raise OSError("The model could not be downloaded.")

    failed = index(tmp_path, encoder)
    failed._load = unreachable
    await failed.load()
    assert await failed.alike("tag", "name", "new", [same]) == []

    class Failing:
        def encode(self, texts):
            raise RuntimeError("The model failed.")

    assert await (await loaded(tmp_path, Failing())).alike("tag", "name", "new", [same]) == []
    assert await (await loaded(tmp_path, encoder)).alike("tag", "name", "new", [same]) == [same]


async def _create_screen(sessions, search, change: ProposalChange) -> str:
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Create it", workspace_revision=workspace.revision, changes=[change]
        )
        await session.commit()
    services = services_for(sessions, reviews=store)
    services.search = search
    message = FakeMessage(70, bot_message=True)
    await render_proposal(message, services, proposal.id)
    return message.edits[-1][0]


def _new(entity: str, **values) -> ProposalChange:
    return ProposalChange(entity=entity, action=ChangeAction.CREATE, values=values)


async def test_pr_similar_030_a_new_card_lists_open_cards_of_any_kind_closest_first(
    sessions, tmp_path
):
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    async with sessions() as session:
        family = await create_card(session, kind="goal", title="Family")
        mother = await create_card(session, kind="subgoal", title="Mother", parent_id=family.id)
        phone = await create_card(
            session, kind="action", title="Phone mother", stage="sprint", effort_points=1,
            parent_id=mother.id,
        )
        await create_card(
            session, kind="action", title="Gift for mom", effort_points=1, parent_id=mother.id
        )
        done = await create_card(
            session, kind="action", title="Ring mum", stage="today", effort_points=1
        )
        await finish_action(session, done.id)
        archived = await create_card(
            session, kind="action", title="Call mother", stage="today", effort_points=1
        )
        await finish_action(session, archived.id)
        await archive_subtree(session, archived.id)
        await create_check(session, title="Called mom")
        await create_card(session, kind="action", title="Buy bread", effort_points=1)
        await session.commit()
    search = await loaded(
        tmp_path,
        Scored(
            "Call mom",
            {
                "Mother": 0.95,
                "Phone mother": 0.9,
                "Family": 0.85,
                "Gift for mom": 0.82,
                "Ring mum": 1.0,
                "Call mother": 1.0,
                "Called mom": 1.0,
                "Buy bread": 0.3,
            },
        )
    )

    text = await _create_screen(
        sessions, search, _new("card", kind="action", title="Call mom", effort_points=1)
    )

    heading, block = text.split(SIMILAR_HEADING)
    assert block.count("<a href=") == SIMILAR_ITEMS_SHOWN
    cited = [block.index(f'?start=card-{card.id}"') for card in (mother, phone, family)]
    assert cited == sorted(cited)
    for title in ("Gift for mom", "Ring mum", "Call mother", "Called mom", "Buy bread"):
        assert title not in block


async def test_pr_similar_030_only_open_items_of_each_type_are_compared(sessions):
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    now = datetime.now(UTC)
    async with sessions() as session:
        pending = await create_check(session, title="Slept eight hours")
        answered = await create_check(session, title="Drank water")
        await resolve_check(session, answered.id, "passed")
        archived = await create_check(session, title="Stretched")
        await resolve_check(session, archived.id, "missed")
        await archive_check(session, archived.id)
        value = await create_value(session, "Health", active=False)
        tag = await create_tag(session, "Training")
        request = await create_saved_request(
            session, "Open actions", "SELECT id FROM ai_cards WHERE kind = 'action'",
            views=ALLOWED_VIEWS,
        )
        mine = await create_reminder(
            session,
            instruction="Take a walk.",
            schedule=resolve(interval_minutes=120, now=now, tz=TZ),
            tz=TZ,
        )
        await session.commit()

        opened = {
            entity: await similar.open_items(session)
            for entity, similar in PROPOSALS.similar.items()
        }

    assert opened["check"] == [(pending.id, "Slept eight hours")]
    assert opened["value"] == [(value.id, "Health")]
    assert opened["tag"] == [(tag.id, "Training")]
    assert opened["request"] == [(request.id, "Open actions")]
    assert opened["reminder"] == [(mine.id, "Take a walk.")]
    assert set(opened) == {"card", "check", "value", "tag", "request", "reminder"}
    fields = {entity: similar.field for entity, similar in PROPOSALS.similar.items()}
    assert fields == {
        "card": "title", "check": "title", "value": "name", "tag": "name", "request": "name",
        "reminder": "instruction",
    }


async def test_pr_similar_030_a_reminder_is_cited_like_the_rest(sessions, tmp_path):
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    async with sessions() as session:
        walk = await create_reminder(
            session,
            instruction="Take a <walk> outside.",
            schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=TZ),
            tz=TZ,
        )
        await session.commit()
    search = await loaded(tmp_path, Scored("Go for a walk.", {"Take a <walk> outside.": 0.9}))

    text = await _create_screen(
        sessions,
        search,
        _new("reminder", instruction="Go for a walk.", schedule_text="every 2 hours"),
    )

    block = text.split(SIMILAR_HEADING)[1]
    assert f'?start=reminder-{walk.id}">⏰ Take a &lt;walk&gt; outside.</a>' in block


async def test_pr_similar_030_no_list_when_nothing_is_alike_for_a_change_or_unloaded(
    sessions, tmp_path
):
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Training")
        await session.commit()
    similar = await loaded(tmp_path, Scored("Workout", {"Training": 0.9}))
    # Another text model is another index: the one above kept its own vectors.
    unlike = await loaded(
        tmp_path,
        Scored("Workout", {"Training": TEXT_MODEL.alike - 0.1}),
        replace(TEXT_MODEL, name="another text model"),
    )
    new = _new("tag", name="Workout")
    edit = ProposalChange(
        entity="tag", action=ChangeAction.UPDATE, entity_id=tag.id,
        expected_version=tag.version, values={"name": "Workout"},
    )

    assert SIMILAR_HEADING in await _create_screen(sessions, similar, new)
    assert SIMILAR_HEADING not in await _create_screen(sessions, unlike, new)
    assert SIMILAR_HEADING not in await _create_screen(sessions, similar, edit)
    waiting = index(tmp_path, Scored("Workout", {"Training": 0.9}))
    assert SIMILAR_HEADING not in await _create_screen(sessions, waiting, new)
