from __future__ import annotations

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn

from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.ai.steps import STEP_ERROR_CHARS, listening
from tg_agent_shell.hooks.contracts import (
    BeforeProposals,
    HookSpec,
    OnBeforeProposals,
    ReturnProposals,
)
from tg_agent_shell.proposals.hooks import REQUEST_REVIEW_HOOK

pytestmark = pytest.mark.e2e

REQUEST = "Сделай Action: подтянуться 20 раз"
PULL_UPS = (
    "card",
    {"mode": "create", "kind": "action", "title": "Подтянуться 20 раз", "effort_points": 1},
)


async def steps_of(advisor, request: str) -> tuple[list[str], object]:
    steps: list[str] = []

    async def heard(line: str) -> None:
        steps.append(line)

    with listening(heard):
        outcome = await advisor.handle(request, source_message_id=1)
    return steps, outcome


async def test_each_request_to_the_model_and_each_check_is_a_step(e2e_harness):
    """AG-TURN-053 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, _provider = e2e_harness.advisor(
        [route_turn("workspace_mutator"), "Готово.", "Сделал.", "done"],
        checks=(REQUEST_REVIEW_HOOK,),
    )

    steps, outcome = await steps_of(advisor, "Что у меня сегодня?")

    assert outcome.message == "Сделал."
    assert steps == [
        "Advisor is thinking.",
        "Workspace mutator is thinking.",
        "Advisor continues after Workspace mutator.",
        "Checking: Request review.",
    ]


async def test_changes_and_a_retry_are_steps(e2e_harness):
    """AG-TURN-053 — tests/brd/tg_agent_shell/agents.feature"""
    error = "Merge them. " + "x" * STEP_ERROR_CHARS
    answers = [(error,), ()]

    async def once(_event: BeforeProposals) -> tuple[str, ...]:
        return answers.pop(0)

    sending_back = HookSpec(
        name="test.once",
        owner="proposals",
        on=(OnBeforeProposals(),),
        evaluate=once,
        effect=ReturnProposals(code="once"),
        title="once",
        description="once",
    )
    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(PULL_UPS),
            mutation_turn(PULL_UPS, prefix="again"),
        ],
        checks=(sending_back,),
    )

    steps, outcome = await steps_of(advisor, REQUEST)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert steps[:3] == [
        "Advisor is thinking.",
        "Workspace mutator is thinking.",
        "Workspace mutator is preparing 1 change.",
    ]
    retry = steps[3]
    assert retry.startswith("Workspace mutator retries: Merge them. xxx")
    assert len(retry) == len("Workspace mutator retries: ") + STEP_ERROR_CHARS
    assert steps[4:] == ["Workspace mutator is preparing 1 change."]
