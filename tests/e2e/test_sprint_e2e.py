"""The sprint subagent in a real Advisor turn: a Sprint started, refused and finished in words.

The real registry, the sprint subagent bound from its own declaration, the real review flow
and the real database; only the provider is scripted.
"""

from __future__ import annotations

import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from agent_turns import forward_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.use_cases import create_card
from safwa.features.planning.agent import SPRINT_PROMPT, SprintToolInput
from safwa.features.planning.api import SPRINT_LENGTH_MAX_DAYS, SPRINT_LENGTH_MIN_DAYS
from safwa.features.planning.model import Sprint, SprintCommitment
from safwa.features.planning.use_cases import FINISHED_BY_HAND, set_sprint_capacity, start_sprint
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.foundation.workspace import SPRINT_LENGTH_DAYS, Workspace
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


def _today():
    return utcnow().astimezone(ZoneInfo("Europe/Istanbul")).date()


def _read(call: list[dict]) -> str:
    return "\n".join(str(message.get("content")) for message in call)


def _tool_result(call: list[dict], name: str) -> dict:
    [result] = [message for message in call if message.get("role") == "tool" and message.get("name") == name]
    return json.loads(str(result["content"]))


async def _save(harness, advisor, provider, proposal_id: int, words: list[str]):
    async with harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, proposal_id)
        await session.commit()
    provider.responses.extend(words)
    answered = await advisor.resolve_approval(
        proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": affected}
    )
    return affected, answered


async def test_pl_mode_002_a_sprint_started_in_words_is_the_one_the_button_starts(e2e_harness):
    """PL-MODE-002 — tests/brd/planning.feature"""
    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        card = await create_card(
            session, kind="action", title="Ship it", stage="sprint", effort_points=3
        )
        await set_sprint_capacity(session, 10)
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("sprint"),
            mutation_turn(
                ("sprint", {"mode": "create", "success_criteria": "Ship v2"}),
                content="I propose starting the Sprint.",
            ),
        ],
        subagents=(e2e_harness.subagent("sprint"),),
    )

    screen = await advisor.handle("Start the Sprint, the goal is to ship v2")

    assert screen.kind is AIOutcomeKind.PROPOSAL
    today = _today()
    last = today + timedelta(days=SPRINT_LENGTH_DAYS - 1)
    async with e2e_harness.sessions() as session:
        drawn = await PROPOSALS.presenter("sprint").screen(
            session, advisor.reviews.proposal(screen.proposal_id).changes
        )
    assert (drawn.mode, drawn.item) == ("Start", f"{SPRINT_LENGTH_DAYS}-day Sprint")
    assert drawn.blocks[:2] == (
        "Success criteria: Ship v2",
        f"{today} – {last}, {SPRINT_LENGTH_DAYS} days",
    )
    assert drawn.blocks[2].startswith("Planned: 1 Actions · 3 EP · capacity 10 EP")

    affected, answered = await _save(
        e2e_harness, advisor, provider, screen.proposal_id, ["The Sprint is on.", forward_turn("sprint")]
    )

    assert answered is not None and answered.message.startswith("The Sprint is on.")
    async with e2e_harness.sessions() as session:
        workspace = await session.get(Workspace, 1)
        sprint = await session.get(Sprint, workspace.active_sprint_id)
        commitments = list(await session.scalars(select(SprintCommitment)))
    assert affected == [sprint.id]
    # What the Start button makes: the next Sprint's length from today, the criteria, the
    # plan at the effort it has now, and the next Sprint's capacity (PL-CAPACITY-027).
    assert (sprint.planned_start_date, sprint.planned_end_date) == (today, last)
    assert sprint.success_criteria == workspace.sprint_success_criteria == "Ship v2"
    assert sprint.capacity_effort_points == 10
    assert [(c.card_id, c.effort_snapshot, c.scope_kind) for c in commitments] == [
        (card.id, 3, "initial")
    ]


async def test_ag_session_051_the_subagents_values_follow_the_conversation_and_are_read_again(
    e2e_harness,
):
    """AG-SESSION-051 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        await create_card(session, kind="action", title="Ship it", stage="sprint", effort_points=3)
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("sprint"),
            mutation_turn(("sprint", {"mode": "update", "success_criteria": "Ship v2"})),
        ],
        subagents=(e2e_harness.subagent("sprint"),),
    )

    screen = await advisor.handle("The next Sprint is about shipping v2")

    first = provider.calls[1]
    assert "Next Sprint's Success criteria: not written yet" in str(first[-1]["content"])
    assert "Next Sprint's Success criteria" not in str(first[0]["content"])
    await _save(e2e_harness, advisor, provider, screen.proposal_id, ["Written.", "Done."])
    # The step after Save reads the block again, with the value that Save wrote.
    assert "Next Sprint's Success criteria: Ship v2" in _read(provider.calls[2])
    assert provider.calls[2][0] == first[0]


async def test_pl_mode_002_refusals_are_the_buttons_own_and_nothing_is_proposed(e2e_harness):
    """PL-MODE-002 — tests/brd/planning.feature"""
    # Nothing planned: starting is refused for the reason the button gives.
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("sprint"),
            mutation_turn(("sprint", {"mode": "create", "success_criteria": "Ship v2"})),
            "Plan an Action first.",
            "Shall I help you pick one?",
        ],
        subagents=(e2e_harness.subagent("sprint"),),
    )

    outcome = await advisor.handle("Start the Sprint")

    assert outcome.kind is AIOutcomeKind.ANSWER
    refused = _tool_result(provider.calls[2], "sprint")
    assert refused["code"] == "start_refused"
    assert "at least one Action in Sprint or Today" in refused["error"]

    # A running Sprint's Success criteria were fixed when it started.
    async with e2e_harness.sessions() as session:
        await create_card(session, kind="action", title="Ship it", stage="sprint", effort_points=3)
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("sprint"),
            mutation_turn(("sprint", {"mode": "update", "success_criteria": "Ship v3"})),
            "They were fixed when the Sprint started.",
            forward_turn("sprint"),
        ],
        subagents=(e2e_harness.subagent("sprint"),),
    )

    outcome = await advisor.handle("Change the Sprint's criteria to ship v3")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message.startswith("They were fixed when the Sprint started.")
    refused = _tool_result(provider.calls[2], "sprint")
    assert refused["code"] == "criteria_refused"
    assert "fixed when it started" in refused["error"]
    assert not e2e_harness.reviews.open_batches
    async with e2e_harness.sessions() as session:
        assert (await session.scalar(select(Sprint))).success_criteria == "Ship v2"

    # Dates, a pause, an extension or a Sprint brought back are nowhere in the tool.
    assert set(SprintToolInput.model_fields) == {
        "mode", "success_criteria", "length_days", "capacity_effort_points"
    }
    assert "there is no way to" in SPRINT_PROMPT


async def test_pl_mode_002_a_sprint_finished_in_words_ends_as_the_button_ends_it(e2e_harness):
    """PL-MODE-002 — tests/brd/planning.feature"""
    async with e2e_harness.sessions() as session:
        await create_card(session, kind="action", title="Ship it", stage="today", effort_points=3)
        sprint = await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [route_turn("sprint"), mutation_turn(("sprint", {"mode": "complete"}))],
        subagents=(e2e_harness.subagent("sprint"),),
    )

    screen = await advisor.handle("Finish the Sprint")

    async with e2e_harness.sessions() as session:
        drawn = await PROPOSALS.presenter("sprint").screen(
            session, advisor.reviews.proposal(screen.proposal_id).changes
        )
    assert (drawn.mode, drawn.item) == ("Finish", f"Sprint {sprint.number}")
    assert drawn.blocks[0].endswith(f"day 1 of {SPRINT_LENGTH_DAYS}")
    assert drawn.blocks[2] == "1 Actions are still open. They keep their stage."

    await _save(e2e_harness, advisor, provider, screen.proposal_id, ["It is over.", "Rest."])

    async with e2e_harness.sessions() as session:
        workspace = await session.get(Workspace, 1)
        ended = await session.get(Sprint, sprint.id)
    assert workspace.active_sprint_id is None
    assert ended.finish_reason == FINISHED_BY_HAND
    assert ended.retro is not None


async def test_pl_length_035_the_next_sprints_length_and_capacity_are_set_in_words(e2e_harness):
    """PL-LENGTH-035 — tests/brd/planning.feature"""
    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("sprint"),
            mutation_turn(
                ("sprint", {"mode": "update", "length_days": 7, "capacity_effort_points": 12.5})
            ),
        ],
        subagents=(e2e_harness.subagent("sprint"),),
    )

    screen = await advisor.handle("Make the next Sprint a week, with 12.5 points")

    assert screen.kind is AIOutcomeKind.PROPOSAL
    async with e2e_harness.sessions() as session:
        drawn = await PROPOSALS.presenter("sprint").screen(
            session, advisor.reviews.proposal(screen.proposal_id).changes
        )
    assert (drawn.mode, drawn.item) == ("Edit", "Next Sprint")
    assert drawn.blocks == (
        f"<b>Length</b>\nNow: {SPRINT_LENGTH_DAYS} days\nBecomes: 7 days",
        "<b>Capacity</b>\nNow: off\nBecomes: 12.5 EP",
    )

    await _save(e2e_harness, advisor, provider, screen.proposal_id, ["Set.", "Done."])

    async with e2e_harness.sessions() as session:
        workspace = await session.get(Workspace, 1)
    assert (workspace.sprint_length_days, workspace.sprint_capacity_effort_points) == (7, 12.5)

    # Out of range: refused before any screen, with the range the screen gives.
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("sprint"),
            mutation_turn(("sprint", {"mode": "update", "length_days": 61})),
            "A Sprint runs 2 to 60 days.",
            forward_turn("sprint"),
        ],
        subagents=(e2e_harness.subagent("sprint"),),
    )

    outcome = await advisor.handle("Make the next Sprint 61 days")

    assert outcome.kind is AIOutcomeKind.ANSWER
    refused = _tool_result(provider.calls[2], "sprint")
    assert refused["code"] == "length_refused"
    assert f"between {SPRINT_LENGTH_MIN_DAYS} and {SPRINT_LENGTH_MAX_DAYS} days" in refused["error"]
    assert not e2e_harness.reviews.open_batches


async def test_pl_capacity_036_with_effort_points_off_a_capacity_in_words_is_refused(e2e_harness):
    """PL-CAPACITY-036 — tests/brd/planning.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("sprint"),
            mutation_turn(("sprint", {"mode": "update", "capacity_effort_points": 20})),
            "Effort Points are off.",
            forward_turn("sprint"),
        ],
        subagents=(e2e_harness.subagent("sprint"),),
    )

    outcome = await advisor.handle("Give the next Sprint a capacity of 20 points")

    assert outcome.kind is AIOutcomeKind.ANSWER
    refused = _tool_result(provider.calls[2], "sprint")
    assert refused["code"] == "capacity_refused"
    assert "Effort Points are off" in refused["error"]
    assert not e2e_harness.reviews.open_batches
