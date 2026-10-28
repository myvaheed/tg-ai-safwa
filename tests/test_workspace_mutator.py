"""The workspace mutator: what the roster promises about the one session that changes the workspace.

The workspace is not an entity — it is the set the owner keeps, Cards, Checks, Values, Tags
and Requests — so what can be asserted here is the contract of the session that proposes every
change to it. Reminders and Remind are the reminder subagent's.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import get_args

import pytest
from schedule_helpers import rule_for

from safwa.bootstrap.modules import (
    AGENTS,
    ALLOWED_VIEWS,
    HELPERS,
    PROPOSALS,
    SCREENS,
    SYSTEM_PROMPT,
    routed_prompt,
)
from safwa.features.advisor.agent import ADVISOR_VIEWS
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import create_check, resolve_check
from safwa.features.diary.agent import DIARY_AGENT, diary_clock
from safwa.features.home import motivation
from safwa.features.home.motivation import owner_context
from safwa.features.onboarding.agent import ONBOARDING_AGENT
from safwa.features.planning import agent as planning_agent
from safwa.features.planning.agent import sprint_now
from safwa.features.planning.use_cases import start_sprint
from safwa.features.profile.agent import PROFILE_AGENT
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.retro import agent as retro_agent
from safwa.features.retro.agent import retro_now
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from safwa.features.workspace_mutator import state
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT
from safwa.features.workspace_mutator.remove import ARCHIVABLE, RemoveToolInput
from safwa.features.workspace_mutator.state import (
    CLOSE_ITEMS,
    CONTEXT_DESCRIPTION_MAX_CHARS,
    close_items,
    workspace_context,
)
from tg_agent_shell.ai.messages import ContextBuilder, StateBlocks
from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.foundation.days import from_today
from tg_agent_shell.telegram.manifest import AgentContext


def test_every_mutation_tool_belongs_to_the_part_that_owns_what_it_changes():
    """WS-SCOPE-001 — tests/brd/workspace_mutator.feature"""
    # AGENTS.md: the Advisor holds no mutation tool at all, and preparation runs where the
    # change was authored.  A new tool that reaches no subagent is unreachable.
    routed = {tool for agent in AGENTS for tool in agent.mutation_tools}

    assert routed == set(PROPOSALS.tools)
    assert set(PROPOSALS.tools) - set(MUTATOR_AGENT.mutation_tools) == {
        "diary", "stop_onboarding", "sprint", "profile", "reminder", "remind", "rename_media",
    }
    # One way to take away what the workspace keeps; a Reminder's part deletes with its own.
    assert [agent.name for agent in AGENTS if "remove" in agent.mutation_tools] == [
        MUTATOR_AGENT.name,
    ]
    assert ONBOARDING_AGENT.mutation_tools == ()
    assert [agent.name for agent in AGENTS if "stop_onboarding" in agent.mutation_tools] == [
        PROFILE_AGENT.name,
    ]
    assert [agent.name for agent in AGENTS if "rename_media" in agent.mutation_tools] == [
        DIARY_AGENT.name,
    ]


def test_the_board_judges_a_change_against_the_state_it_is_given():
    """WS-JUDGE-002 — tests/brd/workspace_mutator.feature"""
    # Its prompt tells it to weigh every proposal against the Sprint, the Success criteria
    # and the active Values, which reach it only as the workspace state block.
    assert MUTATOR_AGENT.workspace_state is True


def test_ws_remove_003_one_way_to_delete_and_two_kinds_that_may_be_archived():
    """WS-REMOVE-003 — tests/brd/workspace_mutator.feature"""
    entities = set(get_args(RemoveToolInput.model_fields["entity"].annotation))

    assert entities == {"card", "check", "tag", "value", "request"}
    assert ARCHIVABLE == {"card", "check"}
    for entity in sorted(entities - ARCHIVABLE):
        with pytest.raises(ValueError, match="never archived"):
            RemoveToolInput(mode="archive", entity=entity, id=1)


class _NoMemory:
    async def sync(self):
        return type("Facts", (), {"text": ""})()


@asynccontextmanager
async def _no_session():
    yield None


def _builder(state: str) -> ContextBuilder:
    async def workspace_state(_session) -> StateBlocks:
        return StateBlocks(
            state=state, clock="Today is Tuesday, 8 September 2026 (2026-09-08), 10:00, Europe/Istanbul."
        )

    return ContextBuilder(
        _no_session,
        _NoMemory(),
        workspace_state,
        system_prompt="You keep what the owner keeps.",
        subagents={},
    )


async def test_ws_context_004_the_advisor_reads_it_always_and_a_subagent_only_when_it_asks():
    """WS-CONTEXT-004 — tests/brd/workspace_mutator.feature"""
    builder = _builder("Workspace mode: sprint")
    turn = [{"role": "user", "content": "Move the roof Card to Today."}]

    answering = json.dumps(
        await builder.root([{"role": "user", "content": "What is open?"}])
    )
    asked = json.dumps(
        await builder.routed(RoutedSubagent("mutator", "Change it.", workspace_state=True), turn)
    )
    did_not = json.dumps(
        await builder.routed(RoutedSubagent("days", "Write it.", workspace_state=False), turn)
    )

    assert "Workspace mode: sprint" in answering
    assert "Workspace mode: sprint" in asked
    assert "Workspace mode: sprint" not in did_not
    # The one that changes the workspace asks for it; the one that writes days does not.
    assert MUTATOR_AGENT.workspace_state is True
    assert DIARY_AGENT.workspace_state is False


async def test_mutator_selects_one_creation_rule_from_the_live_profile(read_views):
    """PS-THOUGHT-026 — tests/brd/profile.feature"""
    sessions, runner = read_views
    spec = next(agent for agent in AGENTS if agent.name == "workspace_mutator")
    routed = spec.bind(
        AgentContext(owner_id=42, timezone="Europe/Istanbul", query_runner=runner,
                     history=SimpleNamespace(), sessions=sessions),
        prompt=routed_prompt(spec),
    )
    builder = ContextBuilder(
        sessions, _NoMemory(), workspace_context, system_prompt=SYSTEM_PROMPT,
        subagents={routed.name: routed},
    )
    prefixes = []
    for on in (False, True, True, False):
        async with sessions() as session:
            await set_profile_field(session, ProfileField.THOUGHTS_BY_DEFAULT, on)
            await session.commit()
        messages = await builder.messages_for(routed.name, [])
        prefix = messages[0]["content"]
        prefixes.append(prefix)
        assert messages[0]["role"] == "system"
        assert ("Otherwise, create a Thought" in prefix) is on
        assert ("Otherwise, create the requested type" in prefix) is not on
        assert "read Thoughts by default" not in prefix
        for context in (messages, await builder.root([])):
            serialized = json.dumps(context)
            assert "Thoughts by default:" not in serialized
            assert "thoughts_by_default" not in serialized
    assert prefixes[0] == prefixes[3]
    assert prefixes[1] == prefixes[2]
    assert prefixes[0] != prefixes[1]


async def test_ws_context_005_the_mode_comes_first_and_the_owner_next(sessions):
    """WS-CONTEXT-005 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        await set_profile_field(
            session, ProfileField.ABOUT_ME, "I run in the mornings"
        )
        await create_card(session, title="Ship it", kind="action", stage="today", effort_points=3)
        await session.commit()
        planning = (await workspace_context(session)).state.splitlines()

        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
        running = (await workspace_context(session)).state.splitlines()

    assert planning[0] == "Workspace mode: planning"
    assert running[0] == "Workspace mode: sprint"
    assert running[1] == "About me: I run in the mornings"
    assert running[2].startswith("Advisor instructions:")


async def test_ws_context_006_everything_named_in_the_state_is_a_citation(sessions):
    """WS-CONTEXT-006 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health", description="Sleep and exercise")
        tag = await create_tag(session, "Family", description="Time with my family")
        empty = await create_tag(session, "Empty")
        card = await create_card(
            session,
            title="Fix the roof",
            kind="goal",
            stage="backlog",
            priority="critical",
        )
        await session.commit()
        state = (await workspace_context(session)).state

    # Read back with the pattern that parses Safwa's own replies: what the block hands over
    # is already a link, so pointing the owner at one of these is quoting it.
    named = {(kind, int(item_id)) for _label, kind, item_id in SCREENS.citation.findall(state)}

    assert named == {("value", value.id), ("tag", tag.id), ("tag", empty.id), ("card", card.id)}
    assert f"- [Health](value:{value.id}): Sleep and exercise" in state.splitlines()
    assert f"- [Family](tag:{tag.id}): Time with my family" in state.splitlines()
    assert f"- [Empty](tag:{empty.id})" in state.splitlines()
    builder = _builder(state)
    turn = [{"role": "user", "content": "What should I do?"}]
    for messages in (
        await builder.root(turn),
        await builder.routed(RoutedSubagent("mutator", "Change it.", workspace_state=True), turn),
    ):
        assert "Sleep and exercise" in json.dumps(messages)
        assert "Time with my family" in json.dumps(messages)


async def test_ws_context_006_a_description_is_one_short_line_and_no_heading_is_empty(sessions):
    """WS-CONTEXT-006 — tests/brd/workspace_mutator.feature"""
    long = "Two lines:\n" + "word " * (CONTEXT_DESCRIPTION_MAX_CHARS // 4)
    async with sessions() as session:
        nothing = (await workspace_context(session)).state
        tag = await create_tag(session, "Family", description=long)
        await session.commit()
        state = (await workspace_context(session)).state

    assert "Values:" not in nothing and "Priority Goals:" not in nothing
    [line] = [line for line in state.splitlines() if f"(tag:{tag.id})" in line]
    about = line.split(": ", 1)[1]
    assert about.startswith("Two lines: word word")
    assert about.endswith("…") and len(about) <= CONTEXT_DESCRIPTION_MAX_CHARS



async def test_ws_context_007_the_clock_is_the_owners_and_comes_last(sessions):
    """WS-CONTEXT-007 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        context = await workspace_context(session)

    assert context.clock.startswith("Today is ")
    assert context.clock.endswith(", Europe/Istanbul.")

    builder = _builder("Workspace mode: planning")
    turn = [{"role": "user", "content": "What is open?"}]

    messages = await builder.root(turn)

    # Last, and behind the conversation: everything before it is the same on the next turn.
    assert "Today is" in json.dumps(messages[-1])
    assert "Today is" not in json.dumps(messages[:-1])


async def test_ws_context_014_a_date_is_told_in_words_and_from_today(sessions, monkeypatch):
    """WS-CONTEXT-014 — tests/brd/workspace_mutator.feature"""
    # 14:05 in Istanbul.
    now = datetime(2026, 10, 27, 11, 5, tzinfo=UTC)
    for module in (state, planning_agent, retro_agent, motivation):
        monkeypatch.setattr(module, "utcnow", lambda: now)
    context = AgentContext(
        owner_id=42,
        timezone="Europe/Istanbul",
        query_runner=None,  # type: ignore[arg-type]
        history=None,  # type: ignore[arg-type]
        sessions=sessions,
    )
    async with sessions() as session:
        await create_card(session, kind="action", title="Ship", stage="sprint")
        sprint = await start_sprint(session, success_criteria="Ship v2")
        sprint.planned_start_date, sprint.planned_end_date = date(2026, 10, 21), date(2026, 11, 3)
        await session.commit()
        blocks = await workspace_context(session)
        told = await owner_context(session)

    today = "Today is Tuesday, 27 October 2026 (2026-10-27), 14:05, Europe/Istanbul."
    assert blocks.clock == today
    assert diary_clock("Europe/Istanbul", SimpleNamespace(now=lambda: now)) == today
    for lines in (await sprint_now(context, ""), await retro_now(context, ""), told[0]):
        assert today in lines.splitlines()
    assert (
        f"Sprint {sprint.number}: 21 October 2026 (6 days ago) – 3 November 2026 (in 7 days), "
        "14 days. Today is day 7." in blocks.state
    )
    assert f"Sprint {sprint.number} runs 21 October 2026 (6 days ago) – 3 November 2026 (in 7 days)" in (
        await sprint_now(context, "")
    )
    assert [from_today(date(2026, 10, 27) + timedelta(days=days), date(2026, 10, 27)) for days in (
        -6, -1, 0, 1, 6
    )] == ["6 days ago", "yesterday", "today", "tomorrow", "in 6 days"]


# Every prompt a model reads a view list from, however that list was written, and the list.
CATALOGUE_PROMPTS = {
    "advisor": (SYSTEM_PROMPT, ADVISOR_VIEWS),
    **{name: (helper.instructions, helper.views) for name, helper in HELPERS.items()},
    **{agent.name: (agent.instructions, agent.views) for agent in AGENTS},
}


@pytest.mark.parametrize("reader", sorted(CATALOGUE_PROMPTS))
def test_a_prompt_names_no_view_the_database_does_not_have(reader: str):
    # The catalogue is composed and cannot name a view that is gone. A prompt also names
    # views in its own prose, and there nothing checks the spelling.
    prompt, views = CATALOGUE_PROMPTS[reader]
    named = set(re.findall(r"\bai_[a-z_]+\b", prompt))

    assert named <= ALLOWED_VIEWS
    # A reader that declares no view is told of none.
    assert bool(named) == bool(views)


class _Closeness:
    """A search index that finds the items it is given."""

    def __init__(self, scores: dict[tuple[str, int], float]) -> None:
        self.scores = scores

    async def relevance(self, search, views):
        return self.scores


def _searched(sessions, scores: dict[tuple[str, int], float] | None):
    ranking = _Closeness(scores) if scores is not None else None
    return SimpleNamespace(sessions=sessions, query_runner=SimpleNamespace(search=ranking))


async def test_ws_context_013_the_open_items_the_owner_is_talking_about(sessions, monkeypatch):
    """WS-CONTEXT-013 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        milk = await create_check(session, title="Milk")
        market = await create_card(session, kind="action", title="Go to the market", check_ids={milk.id})
        run = await create_card(
            session, kind="action", title="Run", schedule="daily 07:00",
            schedule_rule=await rule_for(session, "daily 07:00"),
        )
        goal = await create_card(
            session, kind="goal", title="Ship v2", schedule="by 20 October 2099",
            schedule_rule={"kind": "deadline", "date": "2099-10-20", "time": None},
        )
        done = await create_card(session, kind="action", title="Call the bank")
        answered = await create_check(session, title="Slept well?")
        health = await create_value(session, "Health", "Body and sleep.")
        await session.commit()
    async with sessions() as session:
        await finish_action(session, done.id)
        await resolve_check(session, answered.id, CheckOutcome.PASSED)
        await session.commit()
    closeness = {
        ("card", done.id): 0.99,
        ("check", answered.id): 0.98,
        ("value", health.id): 0.97,
        ("check", milk.id): 0.9,
        ("card", market.id): 0.7,
        ("card", goal.id): 0.6,
        ("card", run.id): 0.5,
    }
    said = "<User>milk from the market</User>"
    listed = [
        f"- [Milk](check:{milk.id}) Check; on [Go to the market](card:{market.id})",
        f"- [Go to the market](card:{market.id}) Action",
        f"- [Ship v2](card:{goal.id}) Goal; deadline: by 20 October 2099",
        f"- [Run](card:{run.id}) Action; schedule: daily 07:00",
    ]

    assert await close_items(_searched(sessions, closeness), said) == "\n".join([CLOSE_ITEMS, *listed])
    monkeypatch.setattr(state, "CLOSE_ITEMS_MAX", 2)
    assert await close_items(_searched(sessions, closeness), said) == "\n".join([CLOSE_ITEMS, *listed[:2]])
    # None close, or no index to ask: nothing is added.
    assert await close_items(_searched(sessions, {}), said) == ""
    assert await close_items(_searched(sessions, None), said) == ""
    assert MUTATOR_AGENT.current is close_items
