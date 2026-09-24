from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from advisor_e2e_helpers import PLAN, mutation_turn, route_turn

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card
from tg_agent_shell.ai.mini import MINI_SESSION_REPAIR_ROUNDS
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.ai.tools import REPAIR_EXHAUSTED
from tg_agent_shell.hooks.contracts import (
    AfterRequest,
    BeforeProposals,
    HoldAnswer,
    HookSpec,
    OnAfterRequest,
    OnBeforeProposals,
    ReturnProposals,
)
from tg_agent_shell.proposals.hooks import (
    REQUEST_REVIEW_PROMPT,
    REQUEST_UNFINISHED,
)
from tg_agent_shell.proposals.materialize import MAX_REPAIR_ROUNDS
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e

REQUEST = "Сделай Actions: подтянуться 20 раз и отжаться 30 раз"
PULL_UPS = (
    "card",
    {"mode": "create", "kind": "action", "title": "Подтянуться 20 раз", "effort_points": 1},
)
PUSH_UPS = (
    "card",
    {"mode": "create", "kind": "action", "title": "Отжаться 30 раз", "effort_points": 1},
)
# What a review that never calls one of its tools says, one more time than it is asked again.
UNDECIDED = ["I am not sure."] * (MINI_SESSION_REPAIR_ROUNDS + 1)


def verdict(name: str, **arguments: str) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(id=f"review-{name}", name=name, arguments_json=json.dumps(arguments)),
        ),
    )


def tool_results(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        json.loads(str(message["content"])) for message in messages if message["role"] == "tool"
    ]


def returning(
    name: str, code: str, answer: Callable[[BeforeProposals], tuple[str, ...]], seen: list
) -> HookSpec:
    """A check that answers each response it reads with what `answer` says of it."""

    async def check(event: BeforeProposals) -> tuple[str, ...]:
        seen.append((name, event))
        return answer(event)

    return HookSpec(
        name=name,
        owner="proposals",
        on=(OnBeforeProposals(),),
        evaluate=check,
        effect=ReturnProposals(code=code),
        title=name,
        description=name,
    )


def holding(answers: list[str | None], seen: list) -> HookSpec:
    """A check that holds each answer it reads with the next of `answers`."""

    async def candidate(event: AfterRequest) -> tuple[AfterRequest, ...]:
        seen.append(event)
        return (event,)

    async def review(event: AfterRequest, _provider) -> str | None:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return HookSpec(
        name="test.hold",
        owner="proposals",
        on=(OnAfterRequest(),),
        evaluate=candidate,
        effect=HoldAnswer(review),
        title="hold",
        description="hold",
    )


async def test_a_check_reads_a_response_once_before_any_call_is_prepared(e2e_harness):
    """AG-HOOK-046 — tests/brd/tg_agent_shell/agents.feature"""
    seen: list = []
    open_then: list[int] = []

    async def check(event: BeforeProposals) -> tuple[str, ...]:
        seen.append(event)
        open_then.append(len(e2e_harness.reviews.open_proposals))
        return ()

    watcher = HookSpec(
        name="test.watch",
        owner="proposals",
        on=(OnBeforeProposals(),),
        evaluate=check,
        effect=ReturnProposals(code="watched"),
        title="watch",
        description="watch",
    )
    advisor, _provider = e2e_harness.advisor(
        [route_turn("workspace_mutator"), mutation_turn(PULL_UPS, PUSH_UPS)], checks=(watcher,)
    )

    outcome = await advisor.handle(REQUEST)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    [event] = seen
    assert open_then == [0]
    assert event.agent_kind == "workspace_mutator"
    assert event.text == PLAN
    assert [(call.tool, call.entity, call.action) for call in event.calls] == [
        ("card", "card", "create"),
        ("card", "card", "create"),
    ]
    assert event.calls[1].values["title"] == "Отжаться 30 раз"
    assert len(e2e_harness.reviews.open_proposals) == 2


async def test_the_first_check_that_answers_sends_the_whole_response_back(e2e_harness):
    """AG-HOOK-046 — tests/brd/tg_agent_shell/agents.feature"""
    seen: list = []
    # Both would send the first response back; neither sends the second one back.
    def first_response(words: str) -> Callable[[BeforeProposals], tuple[str, ...]]:
        return lambda event: (words,) if event.calls[0].call_id.startswith("mutation") else ()

    first = returning("test.first", "first_code", first_response("Merge them."), seen)
    second = returning("test.second", "second_code", first_response("Never read."), seen)
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(PULL_UPS, PUSH_UPS),
            mutation_turn(PULL_UPS, PUSH_UPS, prefix="again"),
        ],
        checks=(first, second),
    )

    outcome = await advisor.handle(REQUEST)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    # The first answer decided: the second check never read that response.
    assert [name for name, _event in seen] == ["test.first", "test.first", "test.second"]
    sent_back = tool_results(provider.calls[2])
    assert [result["code"] for result in sent_back] == ["first_code", "first_code"]
    assert {result["error"] for result in sent_back} == {"Merge them."}
    assert all(result["retryable"] is True for result in sent_back)
    assert len(e2e_harness.reviews.open_proposals) == 2


async def test_responses_sent_back_count_toward_the_five_tries(e2e_harness):
    """AG-HOOK-046 — tests/brd/tg_agent_shell/agents.feature"""
    seen: list = []
    always = returning("test.always", "always", lambda _event: ("No.",), seen)
    tries = MAX_REPAIR_ROUNDS + 1
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            *(mutation_turn(PULL_UPS, prefix=f"try{index}") for index in range(tries)),
            "Не получилось.",
        ],
        checks=(always,),
    )

    outcome = await advisor.handle(REQUEST)

    assert "Не получилось." in outcome.message
    assert len(seen) == tries
    assert not e2e_harness.reviews.open_proposals
    assert REPAIR_EXHAUSTED in str(provider.calls[-1][-1]["content"])


async def test_a_check_that_fails_prepares_nothing_and_says_the_subagent_did_not_finish(
    e2e_harness,
):
    """AG-HOOK-046 — tests/brd/tg_agent_shell/agents.feature"""

    async def broken(event: BeforeProposals) -> tuple[str, ...]:
        raise RuntimeError("the check broke")

    failing = HookSpec(
        name="test.broken",
        owner="proposals",
        on=(OnBeforeProposals(),),
        evaluate=broken,
        effect=ReturnProposals(code="broken"),
        title="broken",
        description="broken",
    )
    advisor, provider = e2e_harness.advisor(
        [route_turn("workspace_mutator"), mutation_turn(PULL_UPS), "Не получилось."],
        checks=(failing,),
    )

    outcome = await advisor.handle(REQUEST)

    assert "Не получилось." in outcome.message
    assert not e2e_harness.reviews.open_proposals
    [receipt] = tool_results(provider.calls[-1])
    assert "test.broken" in receipt["error"]


async def test_a_check_holds_the_answer_once_and_the_request_goes_on(e2e_harness):
    """AG-HOOK-047 — tests/brd/tg_agent_shell/agents.feature"""
    seen: list = []
    advisor, provider = e2e_harness.advisor(
        ["Первый ответ.", "Второй ответ."],
        checks=(holding(["Say it again.", "Never used."], seen),),
    )

    outcome = await advisor.handle("Как дела?", source_message_id=1)

    assert outcome.message == "Второй ответ."
    [event] = seen
    assert "<User>Как дела?</User>" in event.conversation
    assert event.done == ()
    assert event.answer == "Первый ответ."
    held_back, note = provider.calls[1][-2:]
    assert held_back == {"role": "assistant", "content": "Первый ответ."}
    assert note["content"] == "[System]: Say it again."


async def test_the_answer_is_read_after_the_last_screen_with_what_became_of_each_change(
    e2e_harness,
):
    """AG-HOOK-047 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, kind="action", title="Приготовить еду", stage="backlog", effort_points=2
        )
        await session.commit()
    seen: list = []
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("card", {"mode": "update", "id": card.id, "title": "Приготовить пиццу"})),
        ],
        checks=(holding([None], seen),),
    )

    first = await advisor.handle("Переименуй действие в Приготовить пиццу", source_message_id=1)

    # A screen stops the request before any answer, so nothing is read yet.
    assert first.kind is AIOutcomeKind.PROPOSAL
    assert seen == []
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()
    provider.responses.extend(["Карточка переименована.", "Переименовал."])

    final = await advisor.resolve_approval(
        first.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": affected}
    )

    assert final is not None and "Переименовал." in final.message
    [event] = seen
    [done] = event.done
    assert "Saved" in done and "Приготовить пиццу" in done
    assert event.answer == "Переименовал."
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card.id)).title == "Приготовить пиццу"


async def test_safwas_own_request_is_not_read_and_a_failed_check_lets_the_answer_out(
    e2e_harness,
):
    """AG-HOOK-047 — tests/brd/tg_agent_shell/agents.feature"""
    seen: list = []
    # A Cue's turn has no message of the owner's behind it.
    own, _provider = e2e_harness.advisor(["Ответ."], checks=(holding(["Held."], seen),))
    assert (await own.handle("A Reminder came due.")).message == "Ответ."
    assert seen == []

    broken, _provider = e2e_harness.advisor(
        ["Ответ."], checks=(holding([RuntimeError("broke")], seen),)
    )
    assert (await broken.handle("Как дела?", source_message_id=1)).message == "Ответ."


async def test_an_unfinished_request_is_not_answered_yet(e2e_harness):
    """AG-DONE-045 — tests/brd/tg_agent_shell/agents.feature"""
    request = "Создай Действие и напомни мне в пятницу"
    advisor, provider = e2e_harness.advisor(
        [
            "Готово, всё сделал.",
            verdict("request_unfinished", missing="Напоминание в пятницу."),
            "Напоминание я пока не создал.",
        ],
        request_review=True,
    )

    outcome = await advisor.handle(request, source_message_id=1)

    assert "Напоминание я пока не создал." in outcome.message
    assert "Готово" not in outcome.message
    assert len(provider.calls) == 3
    system, context = provider.calls[1]
    assert system["content"] == REQUEST_REVIEW_PROMPT
    assert "discarded, refused or taken back" in REQUEST_REVIEW_PROMPT
    assert "The answer asks the user about that change" in REQUEST_REVIEW_PROMPT
    read = json.loads(str(context["content"]))
    assert f"<User>{request}</User>" in read["conversation"]
    assert read["done"] == []
    assert read["answer"] == "Готово, всё сделал."
    note = provider.calls[2][-1]["content"]
    assert note == "[System]: " + REQUEST_UNFINISHED.format(missing="Напоминание в пятницу.")


async def test_a_done_or_undecided_review_sends_the_answer_as_it_is(e2e_harness):
    """AG-DONE-045 — tests/brd/tg_agent_shell/agents.feature"""
    done, done_provider = e2e_harness.advisor(
        ["Ответ.", verdict("request_done", reason="A question.")], request_review=True
    )
    assert (await done.handle("Как дела?", source_message_id=1)).message == "Ответ."
    assert len(done_provider.calls) == 2

    undecided, undecided_provider = e2e_harness.advisor(
        ["Ответ.", *UNDECIDED], request_review=True
    )
    assert (await undecided.handle("Как дела?", source_message_id=1)).message == "Ответ."
    assert len(undecided_provider.calls) == 1 + len(UNDECIDED)


async def test_request_review_off_reads_no_answer(e2e_harness):
    """AG-DONE-045 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider = e2e_harness.advisor(["Ответ."])

    assert (await advisor.handle("Как дела?", source_message_id=1)).message == "Ответ."
    assert len(provider.calls) == 1
