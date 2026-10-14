"""The method each feature words for its items, read by every check and prompt and by Compass."""

from __future__ import annotations

import json

import pytest

from llm_gateway import ToolCall
from safwa.bootstrap.modules import AGENTS, SYSTEM_PROMPT, routed_prompt
from safwa.features.cards.action_review import ACTION_REVIEW_PROMPT
from safwa.features.cards.api import ACTION, GOAL
from safwa.features.cards.goal_review import GOAL_REVIEW_PROMPT
from safwa.features.checks.api import CHECK
from safwa.features.checks.check_review import CHECK_REVIEW_PROMPT
from safwa.features.compass.agent import MORE_ABOUT, TOPICS
from safwa.features.onboarding.agent import ONBOARDING_AGENT, ONBOARDING_PROMPT
from safwa.features.values.api import LINK, VALUE
from safwa.features.values.link_review import VALUE_LINK_PROMPT
from safwa.features.values.value_review import VALUE_REVIEW_PROMPT
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT


@pytest.mark.parametrize(
    ("method", "prompt"),
    [
        (VALUE, VALUE_REVIEW_PROMPT),
        (GOAL, GOAL_REVIEW_PROMPT),
        (ACTION, ACTION_REVIEW_PROMPT),
        (CHECK, CHECK_REVIEW_PROMPT),
    ],
)
def test_each_check_reads_its_criteria_and_examples_from_the_method(method, prompt):
    """CMP-METHOD-001 — tests/brd/compass.feature"""
    assert method.definition in prompt
    for line in (*method.criteria, *method.leaves):
        assert line in prompt
    for example in method.examples:
        assert example.item in prompt
        assert (example.verdict in prompt) and (example.ask in prompt)


def test_the_link_check_reads_the_link_method():
    """CMP-METHOD-001 — tests/brd/compass.feature"""
    for line in (LINK.definition, *LINK.criteria, *LINK.leaves):
        assert line in VALUE_LINK_PROMPT
    for example in LINK.examples:
        assert example.item in VALUE_LINK_PROMPT


def test_every_prompt_that_writes_items_reads_what_each_is_and_how_to_write_one():
    """CMP-METHOD-001 — tests/brd/compass.feature"""
    writers = [SYSTEM_PROMPT, routed_prompt(MUTATOR_AGENT)]
    writers += [routed_prompt(agent) for agent in AGENTS if "{items}" in agent.instructions]
    for prompt in writers:
        for method in (VALUE, GOAL, ACTION, CHECK, LINK):
            for line in (method.definition, *method.writing):
                assert f"- {line}" in prompt
        for line in LINK.criteria:
            assert f"- {line}" in prompt


async def test_onboarding_reads_one_whole_part_of_the_method_when_asked():
    """CMP-METHOD-001 — tests/brd/compass.feature"""
    assert "call `get_more_about` with that topic and answer from it" in ONBOARDING_PROMPT
    assert ONBOARDING_AGENT.read_tools(None) == (MORE_ABOUT,)
    assert MORE_ABOUT.schema["function"]["parameters"]["properties"]["topic"]["enum"] == [
        "values", "goals", "actions", "checks", "links",
    ]
    for topic, method in TOPICS.items():
        answer = await MORE_ABOUT.run(
            ToolCall(id="more", name="get_more_about", arguments_json=json.dumps({"topic": topic}))
        )
        assert answer["topic"] == topic
        for line in (method.definition, *method.criteria, *method.writing):
            assert line in answer["text"]
        for example in method.examples:
            assert example.item in answer["text"]
    assert "Value description: two or three vivid sentences" in (
        await MORE_ABOUT.run(ToolCall(id="more", name="get_more_about", arguments_json='{"topic": "values"}'))
    )["text"]


async def test_a_topic_it_does_not_know_is_refused_with_the_topics_it_does():
    """CMP-METHOD-001 — tests/brd/compass.feature"""
    answer = await MORE_ABOUT.run(
        ToolCall(id="more", name="get_more_about", arguments_json='{"topic": "tags"}')
    )
    assert answer["status"] == "error"
    assert answer["hint"] == "Retry with topic one of: values, goals, actions, checks, links."
