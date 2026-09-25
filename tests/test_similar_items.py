"""Similar items on a creating review screen: the comparison, what is open, and the screen."""

from __future__ import annotations

import math
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from ui_harness import FakeMessage, services_for

from safwa.bootstrap.modules import ALLOWED_VIEWS, PROPOSALS
from safwa.features.cards.use_cases import archive_subtree, create_card, finish_action
from safwa.features.checks.use_cases import archive_check, create_check, resolve_check
from safwa.features.reminders.schedule import resolve
from safwa.features.reminders.use_cases import create_reminder, create_sprint_reminder
from safwa.features.saved_requests.use_cases import create_saved_request
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from safwa.foundation.workspace import Workspace
from tg_agent_shell.proposals.model import ChangeAction, ProposalChange
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.proposals.telegram import render_proposal
from tg_agent_shell.proposals.telegram.screens import SIMILAR_HEADING
from tg_agent_shell.similarity import SIMILAR_ITEMS_SHOWN, SIMILAR_THRESHOLD, Similarity

TZ = ZoneInfo("UTC")


class Scored:
    """An encoder whose texts are exactly as alike to `to` as `scores` says.

    `to` lies on one axis. A scored text leans off it onto an axis of its own, so two texts
    other than `to` share nothing but that lean; a text not scored shares nothing at all.
    """

    def __init__(self, to: str, scores: dict[str, float]) -> None:
        self.to = to
        self.scores = scores
        self.axes: dict[str, int] = {to: 0}
        self.encoded: list[str] = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * 64
        if text == self.to:
            vector[0] = 1.0
            return vector
        score = self.scores.get(text, 0.0)
        vector[0] = score
        vector[self.axes.setdefault(text, len(self.axes))] = math.sqrt(1 - score * score)
        return vector


async def loaded(encoder) -> Similarity:
    similarity = Similarity(lambda: encoder)
    await similarity.load()
    return similarity


async def test_pr_similar_030_the_closest_above_the_threshold_are_listed_closest_first():
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    encoder = Scored(
        "new",
        {
            "just above": SIMILAR_THRESHOLD + 0.001,
            "closest": 0.99,
            "just below": SIMILAR_THRESHOLD - 0.001,
            "third": 0.9,
            "second": 0.95,
        },
    )
    similarity = await loaded(encoder)
    items = [(1, "just above"), (2, "closest"), (3, "just below"), (4, "third"), (5, "second")]

    listed = await similarity.closest("new", items)
    assert listed == [2, 5, 4]
    assert len(listed) == SIMILAR_ITEMS_SHOWN
    assert await similarity.closest("new", [(1, "just above"), (3, "just below")]) == [1]
    # Each text is encoded once; the second screen reads what the first one did.
    assert sorted(encoder.encoded) == sorted(["new", *(text for _, text in items)])


async def test_pr_similar_030_nothing_is_compared_until_the_model_loads_or_once_it_failed():
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    items = [(1, "same")]
    encoder = Scored("new", {"same": 1.0})
    assert await Similarity(lambda: encoder).closest("new", items) == []

    def unreachable():
        raise OSError("The model could not be downloaded.")

    failed = Similarity(unreachable)
    await failed.load()
    assert await failed.closest("new", items) == []

    class Failing:
        def encode(self, texts):
            raise RuntimeError("The model failed.")

    assert await (await loaded(Failing())).closest("new", items) == []
    assert await (await loaded(encoder)).closest("new", items) == [1]


async def _create_screen(sessions, similarity, change: ProposalChange) -> str:
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Create it", workspace_revision=workspace.revision, changes=[change]
        )
        await session.commit()
    services = services_for(sessions, reviews=store)
    services.similarity = similarity
    message = FakeMessage(70, bot_message=True)
    await render_proposal(message, services, proposal.id)
    return message.edits[-1][0]


def _new(entity: str, **values) -> ProposalChange:
    return ProposalChange(entity=entity, action=ChangeAction.CREATE, values=values)


async def test_pr_similar_030_a_new_card_lists_open_cards_of_any_kind_closest_first(sessions):
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
    similarity = await loaded(
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
        sessions, similarity, _new("card", kind="action", title="Call mom", effort_points=1)
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
        await create_sprint_reminder(
            session, instruction="The Sprint ends tomorrow.", at_time=time(9), anchor_at=now,
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


async def test_pr_similar_030_a_reminder_is_cited_like_the_rest(sessions):
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    async with sessions() as session:
        walk = await create_reminder(
            session,
            instruction="Take a <walk> outside.",
            schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=TZ),
            tz=TZ,
        )
        await session.commit()
    similarity = await loaded(Scored("Go for a walk.", {"Take a <walk> outside.": 0.9}))

    text = await _create_screen(
        sessions,
        similarity,
        _new("reminder", instruction="Go for a walk.", schedule_text="every 2 hours"),
    )

    block = text.split(SIMILAR_HEADING)[1]
    assert f'?start=reminder-{walk.id}">⏰ Take a &lt;walk&gt; outside.</a>' in block


async def test_pr_similar_030_no_list_when_nothing_is_alike_for_a_change_or_when_off(sessions):
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Training")
        await session.commit()
    similar = await loaded(Scored("Workout", {"Training": 0.9}))
    unlike = await loaded(Scored("Workout", {"Training": SIMILAR_THRESHOLD - 0.1}))
    new = _new("tag", name="Workout")
    edit = ProposalChange(
        entity="tag", action=ChangeAction.UPDATE, entity_id=tag.id,
        expected_version=tag.version, values={"name": "Workout"},
    )

    assert SIMILAR_HEADING in await _create_screen(sessions, similar, new)
    assert SIMILAR_HEADING not in await _create_screen(sessions, unlike, new)
    assert SIMILAR_HEADING not in await _create_screen(sessions, similar, edit)
    assert SIMILAR_HEADING not in await _create_screen(sessions, None, new)
    waiting = Similarity(lambda: Scored("Workout", {"Training": 0.9}))
    assert SIMILAR_HEADING not in await _create_screen(sessions, waiting, new)
