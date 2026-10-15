"""Compass: the subagent the Advisor hands a question about the owner's own tree to, and the
look at that tree every nine days in the Morning."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from schedule_helpers import create_card, create_check
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import AGENTS, REGISTRY, SYSTEM_PROMPT, routed_prompt
from safwa.constants import INBOX_TAG_NAME
from safwa.features.cards.use_cases import toggle_card_parent
from safwa.features.compass.agent import COMPASS_AGENT, COMPASS_PROMPT, MORE_ABOUT, compass_now
from safwa.features.compass.graph import (
    COMPASS_FINDINGS_MAX,
    GOAL_VALUES_MANY,
    NOT_PROBLEMS,
    PROBLEMS,
    read_tree,
    tree_text,
)
from safwa.features.compass.graph_review import (
    COMPASS_REVIEW_DAYS,
    GRAPH_REQUEST,
    GRAPH_REVIEW_HOOK,
    GRAPH_REVIEW_PROMPT,
    GraphReviewer,
    graph_request,
    review_day,
    review_graph,
)
from safwa.features.onboarding.agent import MANUAL
from safwa.features.profile.api import hook_switched_on, morning_time, set_hook_switch
from safwa.features.tags.model import Tag
from safwa.features.tags.use_cases import seed_inbox_tag
from safwa.features.values.model import CheckValue
from safwa.features.values.use_cases import create_value
from safwa.foundation.workspace import require_workspace
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.contracts import OnTick, RunAndAdvise, RunContext, Tick
from tg_agent_shell.hooks.registry import HookRegistry

MORNING = Tick("08:00", clock=morning_time)


class GraphProvider:
    def __init__(self, findings=()):
        self.findings = list(findings)
        self.calls = []

    async def complete(self, request):
        self.calls.append(request)
        call = (
            ToolCall(id="graph", name="findings", arguments_json=json.dumps({"findings": self.findings}))
            if self.findings
            else ToolCall(id="graph", name="clear", arguments_json="{}")
        )
        return CompletionTurn(content="", tool_calls=(call,))


def work(sessions, provider):
    async def nobody_publishes(text, kind):
        raise AssertionError("a hook on a tick has no chat")

    return RunContext(
        resources=SimpleNamespace(graph_reviewer=GraphReviewer(provider)),
        still_current=lambda: True,
        publish=nobody_publishes,
        sessions=sessions,
    )


async def started_days_ago(sessions, days):
    async with sessions() as session:
        workspace = await require_workspace(session)
        workspace.created_at = utcnow() - timedelta(days=days)
        await session.commit()


async def value(session, name):
    return (await create_value(session, name, f"Why {name} matters and what I do for it")).id


# ----------------------------------------------------------------------- the subagent


def test_the_advisor_routes_a_question_about_the_owners_own_tree_to_compass():
    """CMP-ROUTE-002 — tests/brd/compass.feature"""
    [routed] = [agent for agent in AGENTS if agent.name == COMPASS_AGENT.name]
    assert f'`route("compass")` — {COMPASS_AGENT.purpose}' in SYSTEM_PROMPT
    assert 'or a request starts with "Compass."' in COMPASS_AGENT.purpose
    assert COMPASS_AGENT.mutation_tools == ()
    assert COMPASS_AGENT.views == ("ai_cards", "ai_checks", "ai_values", "ai_tags")
    tools = COMPASS_AGENT.read_tools(SimpleNamespace(chat=None, bot=None))
    assert tools[0] is MORE_ABOUT
    assert [tool.schema["function"]["name"] for tool in tools] == [
        "get_more_about", "show_constellation",
    ]
    assert COMPASS_AGENT.answers_questions and COMPASS_AGENT.workspace_state
    prompt = routed_prompt(routed)
    assert "- `ai_tags(" in prompt and "{views}" not in prompt
    for line in (*PROBLEMS, *NOT_PROBLEMS):
        assert line in prompt
    assert f"Name at most {COMPASS_FINDINGS_MAX} findings, the most important first." in prompt
    assert "Offer every change as a possibility." in prompt
    assert "A change is proposed once the user agrees." in prompt
    assert f"A Goal carries {GOAL_VALUES_MANY} Values or more." in PROBLEMS


async def test_each_step_compass_is_told_what_sql_finds(sessions):
    """CMP-ROUTE-002 — tests/brd/compass.feature"""
    context = SimpleNamespace(sessions=sessions)
    assert await compass_now(context) == "Found without a model: nothing."
    async with sessions() as session:
        await seed_inbox_tag(session)
        inbox_id = await session.scalar(select(Tag.id).where(Tag.name == INBOX_TAG_NAME))
        many = {await value(session, f"Value {n}") for n in range(GOAL_VALUES_MANY)}
        everything = await create_card(session, kind="goal", title="Everything", value_ids=many)
        # The same Value as its Goal is not a finding.
        await create_card(
            session, kind="action", title="Run", parent_ids={everything.id},
            value_ids={min(many)},
        )
        earn = await create_card(session, kind="goal", title="Earn $20k")
        await create_card(session, kind="goal", title="Find clients", parent_ids={earn.id})
        calm = await value(session, "Calm")
        await create_card(
            session, kind="goal", title="Some idea", tag_ids={inbox_id}, value_ids={calm},
        )
        freedom = await value(session, "Freedom")
        old = await create_card(session, kind="goal", title="Old", value_ids={freedom})
        old.effective_stage = "done"
        joy = await value(session, "Joy")
        answered = await create_check(session, title="Smiled today?")
        session.add(CheckValue(check_id=answered.id, value_id=joy))
        answered.outcome = "passed"
        order = await value(session, "Order")
        open_check = await create_check(session, title="Desk clear?")
        session.add(CheckValue(check_id=open_check.id, value_id=order))
        await session.commit()
        ids = (everything.id, earn.id, calm, freedom, joy)
    assert await compass_now(context) == "\n".join([
        "Found without a model:",
        f"- [Everything](card:{ids[0]}): a Goal carrying {GOAL_VALUES_MANY} Values.",
        f"- [Earn $20k](card:{ids[1]}): a root Goal that serves no Value.",
        f"- [Calm](value:{ids[2]}): no open Goal, Action or Check carries it.",
        f"- [Freedom](value:{ids[3]}): no open Goal, Action or Check carries it.",
        f"- [Joy](value:{ids[4]}): no open Goal, Action or Check carries it.",
    ])


# ----------------------------------------------------------------------- the review


def test_one_hook_looks_at_the_tree_in_the_morning_under_its_own_switch():
    """CMP-REVIEW-003 — tests/brd/compass.feature"""
    assert GRAPH_REVIEW_HOOK in REGISTRY.hooks.specs
    assert GRAPH_REVIEW_HOOK.owner == "compass"
    assert GRAPH_REVIEW_HOOK.on == (OnTick(at=morning_time),)
    assert isinstance(GRAPH_REVIEW_HOOK.effect, RunAndAdvise)
    assert GRAPH_REVIEW_HOOK.agent_related and GRAPH_REVIEW_HOOK.switch is None
    assert f"{GRAPH_REVIEW_HOOK.title} in Profile → Hooks" in MANUAL
    for line in (*PROBLEMS, *NOT_PROBLEMS):
        assert line in GRAPH_REVIEW_PROMPT
    assert f"Call findings with at most {COMPASS_FINDINGS_MAX}, the most important first" in (
        GRAPH_REVIEW_PROMPT
    )


@pytest.mark.parametrize("days", [0, 1, COMPASS_REVIEW_DAYS - 1, COMPASS_REVIEW_DAYS,
                                  COMPASS_REVIEW_DAYS + 1, 2 * COMPASS_REVIEW_DAYS])
async def test_the_review_falls_on_every_ninth_day_after_the_first(sessions, days):
    """CMP-REVIEW-003 — tests/brd/compass.feature"""
    async with sessions() as session:
        first = (await require_workspace(session)).created_at
        due = await review_day(session, now=first + timedelta(days=days))
    assert due == (days > 0 and days % COMPASS_REVIEW_DAYS == 0)


async def test_the_mini_session_reads_the_tree_and_hands_its_findings_to_compass(sessions):
    """CMP-REVIEW-003 — tests/brd/compass.feature"""
    await started_days_ago(sessions, COMPASS_REVIEW_DAYS)
    async with sessions() as session:
        health = await value(session, "Health")
        earn = await create_card(session, kind="goal", title="Earn $20k", note="By December")
        sub = await create_card(session, kind="goal", title="Find clients", parent_ids={earn.id})
        run = await create_card(
            session, kind="action", title="Run", parent_ids={sub.id}, value_ids={health},
        )
        slept = await create_check(session, title="Slept 7 hours?")
        session.add(CheckValue(check_id=slept.id, value_id=health))
        await session.commit()
        ids = SimpleNamespace(earn=earn.id, sub=sub.id, run=run.id, slept=slept.id)
    found = [f"[Earn $20k](card:{ids.earn}): serves no Value; ask which it is for."]
    provider = GraphProvider(found)
    items = await review_graph(MORNING.at, work(sessions, provider))
    assert items == tuple(found)
    [request] = provider.calls
    assert request.messages[0]["content"] == GRAPH_REVIEW_PROMPT
    assert [tool["function"]["name"] for tool in request.tools] == ["clear", "findings"]
    findings = request.tools[1]["function"]["parameters"]["properties"]["findings"]
    assert findings["maxItems"] == COMPASS_FINDINGS_MAX
    tree = request.messages[1]["content"]
    for line in (
        f"- [Health](value:{health}): Why Health matters and what I do for it",
        f"- [Earn $20k](card:{ids.earn}), Goal. No Value. Note: By December",
        f"  - [Find clients](card:{ids.sub}), Subgoal. No Value.",
        f"    - [Run](card:{ids.run}), Action. Values: Health.",
        f"- [Slept 7 hours?](check:{ids.slept}). Values: Health.",
        "Found without a model:",
        f"- [Earn $20k](card:{ids.earn}): a root Goal that serves no Value.",
    ):
        assert line in tree.splitlines()
    async with sessions() as session:
        await toggle_card_parent(session, ids.run, ids.earn)
        await session.commit()
        written = tree_text(await read_tree(session))
    # A Card under two Goals is written once, under the last of them, naming the other.
    assert f"    - [Run](card:{ids.run}), Action. Also under: [Earn $20k](card:{ids.earn}). Values: Health." in written.splitlines()
    assert written.count(f"[Run](card:{ids.run})") == 1
    async with sessions() as session:
        words = await graph_request(session, items)
    assert words == GRAPH_REQUEST.format(findings=f"- {found[0]}")
    assert words.startswith("Compass.") and words.endswith('Call route("compass") with these.')
    assert 'The newest message starts with "Compass."' in COMPASS_PROMPT


async def test_a_clear_answer_keeps_what_sql_found_and_nothing_found_asks_nothing(sessions):
    """CMP-REVIEW-003 — tests/brd/compass.feature"""
    await started_days_ago(sessions, 2 * COMPASS_REVIEW_DAYS)
    async with sessions() as session:
        health = await value(session, "Health")
        await create_card(session, kind="goal", title="Get fit", value_ids={health})
        await session.commit()
    provider = GraphProvider()
    assert await review_graph(MORNING.at, work(sessions, provider)) == ()
    assert len(provider.calls) == 1
    async with sessions() as session:
        assert await graph_request(session, ()) is None
        titles = [f"Goal {n}" for n in range(COMPASS_FINDINGS_MAX + 1)]
        cards = [await create_card(session, kind="goal", title=title) for title in titles]
        await session.commit()
    assert await review_graph(MORNING.at, work(sessions, provider)) == tuple(
        f"[{card.title}](card:{card.id}): a root Goal that serves no Value."
        for card in cards[:COMPASS_FINDINGS_MAX]
    )


@pytest.mark.parametrize("days", [0, COMPASS_REVIEW_DAYS + 1])
async def test_no_mini_session_runs_on_another_day(sessions, days):
    """CMP-REVIEW-003 — tests/brd/compass.feature"""
    await started_days_ago(sessions, days)
    async with sessions() as session:
        await create_card(session, kind="goal", title="Earn $20k")
        await session.commit()
    provider = GraphProvider(["Something"])
    assert await review_graph(MORNING.at, work(sessions, provider)) == ()
    assert provider.calls == []


async def test_no_mini_session_runs_on_a_tree_with_nothing_in_it(sessions):
    """CMP-REVIEW-003 — tests/brd/compass.feature"""
    await started_days_ago(sessions, COMPASS_REVIEW_DAYS)
    async with sessions() as session:
        await create_check(session, title="Slept 7 hours?")
        await session.commit()
    provider = GraphProvider(["Something"])
    assert await review_graph(MORNING.at, work(sessions, provider)) == ()
    assert provider.calls == []


async def test_the_request_waits_while_hooks_are_held_and_its_switch_drops_it(sessions):
    """CMP-REVIEW-003 — tests/brd/compass.feature"""
    await started_days_ago(sessions, COMPASS_REVIEW_DAYS)
    async with sessions() as session:
        await create_card(session, kind="goal", title="Earn $20k")
        await session.commit()
    registry = HookRegistry.of(
        (GRAPH_REVIEW_HOOK,), owners=frozenset({"compass"}), policy=hook_switched_on
    )
    provider = GraphProvider(["Earn $20k serves no Value."])
    await queue_advice(registry, sessions, MORNING, work=work(sessions, provider))
    said = []

    async def speak(event_id, text, shown=(), passing=None):
        said.append(text)
        return True

    async def free():
        return True

    async def delivered(event_id):
        return False

    async def prepare(hook, items):
        return await registry.prepare(sessions, hook, items)

    await tick(
        sessions, gate=free, speak=speak, delivered=delivered, prepare=prepare,
        allow_hooks=lambda: False,
    )
    assert said == []
    async with sessions() as session:
        assert [cue.hook for cue in await session.scalars(select(Cue))] == [GRAPH_REVIEW_HOOK.name]
        await set_hook_switch(
            session, GRAPH_REVIEW_HOOK.name, on=False,
            followers=registry.followers(GRAPH_REVIEW_HOOK.name),
        )
        await session.commit()
        assert list(await session.scalars(select(Cue))) == []
    await queue_advice(registry, sessions, MORNING, work=work(sessions, provider))
    assert len(provider.calls) == 1
