"""Where a tool call becomes a review the owner decides.

This is the seam the whole proposal rule rests on: the model never mutates, so its calls
arrive here as prepared changes, and what leaves is either an open review or words — or a
subsession opened or closed, when the session ended on `sub` or `finish`. A call
that could not be prepared, or that a check sent back, goes back to the model as a
corrected tool result, and the session runs again — `None` from `materialize` is what asks
the runtime for that. An answer a check held back runs the session again the same way.

The checks are hooks: `BeforeProposals` before any call of a response is prepared,
`BeforeReview` before a proposal's screen is drawn, and `AfterRequest` before the answer to
the owner's message is sent.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime import (
    AgentLoopResult,
    AgentSession,
    PendingTool,
    TurnOutcome,
    answered_calls,
    call_key,
    json_safe,
    system_note,
)
from llm_gateway import LlmProvider

from ..ai.contracts import ToolResultStatus
from ..ai.conversation import kept_turn
from ..ai.outcome import AIOutcome, AIOutcomeKind, as_turn
from ..ai.steps import announce, preparing_line
from ..ai.subagents import SUBAGENT_HISTORY_LAST_MESSAGES
from ..ai.tools import (
    REPAIR_EXHAUSTED,
    ToolAdapters,
    WatcherFailed,
    conversation_for,
    response_text,
    reviewed,
)
from ..foundation.errors import DomainError
from ..hooks.contracts import (
    AfterRequest,
    BeforeProposals,
    BeforeReview,
    HoldAnswer,
    ProposedCall,
    ReviewedChange,
    SaveProposal,
    SessionRead,
)
from .api import ProposalRegistry, ToolPreparationError
from .model import AUTO_SAVED_RECEIPT, BatchDecision, ProposalChange
from .prepare import ChangePreparer
from .render import (
    AUTOAPPROVED,
    ProposalRenderer,
    already_decided,
    compose_display_outcome,
    with_queued_siblings,
    without_echoed_receipts,
)
from .store import ProposalStore
from .use_cases import open_batch, prepare_change, queue_proposals

logger = logging.getLogger(__name__)

MAX_REPAIR_ROUNDS = 5

# Set on a session that answers a message of the owner's, and taken off by its first answer
# in words, which is the one `AfterRequest` is about.
OWNER_REQUEST = "owner_request"
# Set on a session nobody asked for: the request that caused it, which its answer keeps.
CUE_REQUEST = "cue_request"

ResolveApproval = Callable[..., Awaitable[AIOutcome | None]]


class ProposalMaterializer:
    """The runtime's `Materializer`: one turn's tool calls become reviews, or words."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        reviews: ProposalStore,
        proposals: ProposalRegistry,
        renderer: ProposalRenderer,
        preparer: ChangePreparer,
        adapters: ToolAdapters,
        *,
        provider: LlmProvider,
        resolve: ResolveApproval,
        mark_answer_source: bool = False,
    ) -> None:
        self.sessions = sessions
        self.reviews = reviews
        self.proposals = proposals
        self.renderer = renderer
        self.preparer = preparer
        self.adapters = adapters
        self.resolve = resolve
        # What a check that holds an answer or saves a proposal reads it with.
        self.provider = provider
        # Each answer ends with who wrote it: a subagent whose words were forwarded, or the
        # root itself.
        self.mark_answer_source = mark_answer_source

    def answer(
        self, agent: AgentSession, message: str, forwarded: str | None = None
    ) -> TurnOutcome:
        """One session's words, and — for the session the owner reads — its receipts.

        A subagent's words go to whoever routed to it, without the receipt lines it echoed:
        its receipts travel beside them, and the owner reads each once. The root supplies
        the model's dialogue answer, which makes it the one place that has to guarantee the
        owner is never left with nothing. Words it `forwarded` are a subagent's, sent as
        they are: first and whole, like a block the host wrote, and never the body the
        receipts are taken out of.
        """
        summaries = agent.display_result_summaries
        if agent.parent_run_id is not None:
            return as_turn(
                AIOutcome(AIOutcomeKind.ANSWER, without_echoed_receipts(message, summaries))
            )
        if forwarded:
            body, shown = "", [*agent.shown_blocks, message]
        else:
            body, shown = message, agent.shown_blocks
        composed = compose_display_outcome(body, summaries, shown)
        if composed and self.mark_answer_source:
            composed += "\n\n" + (f"↪️ {forwarded}" if forwarded else f"✍️ {agent.kind}")
        return as_turn(
            AIOutcome(
                AIOutcomeKind.ANSWER,
                composed or "⚠️ There was nothing to say about that. You can ask again.",
                open_item=agent.host_state.get("open_item"),
                turn=kept_turn(
                    json_safe(agent.transcript),
                    message,
                    request=agent.host_state.get(CUE_REQUEST),
                    forwarded=forwarded,
                ),
            )
        )

    async def materialize(
        self, agent: AgentSession, result: AgentLoopResult
    ) -> TurnOutcome | None:
        """Turn one finished loop run into a review, or into words.

        ``None`` means the session's own tool results were corrected in place and it should
        run again — the repair round, bounded by `MAX_REPAIR_ROUNDS` — or that its answer
        was held back with what is missing, which happens once per request.
        """
        if result.ended is not None:
            return as_turn(_subsession_outcome(result.ended))
        if not result.pending_tools:
            return await self._answer_unless_held(agent, result.message, result.forwarded)
        mutation_tools = [tool for tool in result.pending_tools if tool.change is not None]
        if mutation_tools:
            await announce(preparing_line(agent.kind, len(mutation_tools)))
        preparation_results = {
            tool.call.id: json_safe(tool.result) for tool in result.pending_tools
        }
        failed_call_ids = {
            tool.call.id
            for tool in result.pending_tools
            if not self.adapters.is_immediate(agent, tool.call.name) and tool.change is None
        }
        sent_back = await self._sent_back(agent, mutation_tools)
        if sent_back is not None:
            for tool in mutation_tools:
                failed_call_ids.add(tool.call.id)
                preparation_results[tool.call.id] = sent_back
            mutation_tools = []
        proposal_details: dict[str, list[str]] = {}
        proposal_displays: dict[str, str] = {}
        # In the order the model made the calls, which is the order the owner reviews them in.
        prepared: list[tuple[str, ProposalChange]] = []
        decided = _decided_calls(agent.messages)
        # A call's own short sessions read the conversation as the session that made it
        # read it (AG-SESSION-063).
        routed = self.adapters.subagents.get(agent.kind)
        conversation = conversation_for(
            agent.dialogue,
            routed.history_messages if routed else SUBAGENT_HISTORY_LAST_MESSAGES,
        )
        async with self.sessions() as session:
            # Preparation writes nothing, so every call of the response is made against this
            # one revision.
            revision = (await self.preparer.world(session)).revision
            for tool in mutation_tools:
                decision = decided.get(call_key(tool.call.name, tool.call.arguments_json))
                if decision is not None:
                    failed_call_ids.add(tool.call.id)
                    preparation_results[tool.call.id] = already_decided(decision)
                    logger.info("AI TOOL %s already %s", tool.call.name, decision.value)
                    continue
                try:
                    change = await prepare_change(
                        session, self.preparer, tool.change, conversation=conversation
                    )
                except ToolPreparationError as error:
                    failed_call_ids.add(tool.call.id)
                    preparation_results[tool.call.id] = error.as_tool_result()
                    logger.info(
                        "AI TOOL %s preparation error [%s]: %s",
                        tool.call.name,
                        error.code,
                        error,
                    )
                    continue
                except DomainError as error:
                    failed_call_ids.add(tool.call.id)
                    preparation_results[tool.call.id] = ToolPreparationError(
                        "invalid_arguments",
                        str(error),
                        "Correct only this unfinished tool call and retry it.",
                    ).as_tool_result()
                    logger.info("AI TOOL %s preparation error: %s", tool.call.name, error)
                    continue
                proposal_details[tool.call.id] = await self.renderer.details(
                    session, change, tool.change
                )
                proposal_displays[tool.call.id] = await self.renderer.line(
                    session, change, proposal_details[tool.call.id]
                )
                prepared.append((tool.call.id, change))
            queue = queue_proposals(
                self.reviews, prepared, workspace_revision=revision
            )
            queued_proposal_ids = {
                call_id: item.proposal_id for item in queue for call_id in item.call_ids
            }
            tool_results = []
            for tool in result.pending_tools:
                queued_id = queued_proposal_ids.get(tool.call.id)
                tool_results.append(
                    {
                        "id": tool.call.id,
                        "name": tool.call.name,
                        "arguments": tool.call.arguments_json,
                        # A call still on screen has no result yet; that is what waiting is.
                        "result": None
                        if queued_id
                        else with_queued_siblings(
                            preparation_results[tool.call.id], len(prepared)
                        ),
                        "details": proposal_details.get(tool.call.id)
                        or self.renderer.raw_details(tool.change),
                        "display": proposal_displays.get(tool.call.id),
                        "proposal_id": queued_id,
                        "change": (
                            {
                                "entity": tool.change.entity,
                                "action": tool.change.action,
                                "id": tool.change.id,
                                "values": json_safe(tool.change.values),
                            }
                            if tool.change is not None
                            else None
                        ),
                    }
                )
            if queue:
                repair_exhausted = bool(
                    failed_call_ids and agent.repair_rounds >= MAX_REPAIR_ROUNDS
                )
                if failed_call_ids:
                    agent.repair_rounds += 1
                self.reviews.open_batch(
                    open_batch(
                        run_id=agent.run_id,
                        items=queue,
                        tool_calls=tool_results,
                        repair_exhausted=repair_exhausted,
                        request=_owner_request(agent.dialogue),
                    )
                )
            await session.commit()
        if not queue:
            if failed_call_ids:
                if agent.repair_rounds >= MAX_REPAIR_ROUNDS:
                    return as_turn(AIOutcome(AIOutcomeKind.ANSWER, REPAIR_EXHAUSTED))
                _fill_in_results(agent, tool_results)
                agent.repair_rounds += 1
                return None
            return self.answer(agent, result.message)
        # Waiting, and nothing more. Whether the owner ever sees this screen is decided once
        # the turn has been stored and released — never from inside the session it suspends.
        return as_turn(
            AIOutcome(AIOutcomeKind.PROPOSAL, result.message, proposal_id=queue[0].proposal_id)
        )

    async def _sent_back(
        self, agent: AgentSession, mutation_tools: list[PendingTool]
    ) -> dict[str, Any] | None:
        """What every call of this response comes back with, when a check sends it back.

        Read before anything is prepared, because preparing some calls already asks the
        model. The first hook that answers decides, and one that fails ends the turn.
        """
        hooks = self.adapters.hooks
        if not mutation_tools or not hooks.listens(BeforeProposals):
            return None
        event = BeforeProposals(
            run_id=agent.run_id,
            agent_kind=agent.kind,
            # The text of the response carrying the calls, which is where the plan is.
            text=response_text(agent.messages),
            calls=tuple(
                ProposedCall(
                    call_id=tool.call.id,
                    tool=tool.call.name,
                    entity=tool.change.entity,
                    action=tool.change.action.value,
                    entity_id=tool.change.id,
                    values=dict(tool.change.values),
                )
                for tool in mutation_tools
            ),
            reads=tuple(
                SessionRead(tool=message["name"], result=str(message.get("content") or ""))
                for message in agent.messages
                if message.get("role") == "tool" and message.get("name") in agent.read_specs
            ),
        )
        async for checked in hooks.evaluate(event, self.sessions):
            name = checked.spec.name
            if checked.error is not None:
                raise WatcherFailed(
                    f"The hook {name}, which checks a response's calls, failed: {checked.error}"
                ) from checked.error
            if not checked.payloads:
                continue
            if not all(isinstance(words, str) and words.strip() for words in checked.payloads):
                raise WatcherFailed(f"The hook {name} sent calls back without words")
            logger.info("HOOK %s sent back %d call(s)", name, len(mutation_tools))
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": checked.spec.effect.code,
                "error": " ".join(checked.payloads),
                "retryable": True,
            }
        return None

    async def _answer_unless_held(
        self, agent: AgentSession, message: str, forwarded: str | None = None
    ) -> TurnOutcome | None:
        """The session's words, unless a check holds the answer to the owner's message back.

        Read once per request, and only there: a subagent's words go to its caller, and a
        request of Safwa's own was never the owner's. Held back, the words stay in the
        session as its own, and it runs on with what the check said.
        """
        hooks = self.adapters.hooks
        if (
            agent.parent_run_id is None
            and hooks.listens(AfterRequest)
            and agent.host_state.pop(OWNER_REQUEST, False)
            and message.strip()
        ):
            words = await self._reviewed(
                AfterRequest(
                    run_id=agent.run_id,
                    conversation=conversation_for(agent.dialogue),
                    done=tuple(agent.display_result_summaries),
                    answer=message,
                ),
                HoldAnswer,
                # A check that cannot decide lets the answer through: the owner still reads
                # it, and decides every screen.
                failed="The hook %s failed; the answer goes out: %s",
            )
            if words is not None:
                agent.messages.append({"role": "assistant", "content": message})
                agent.messages.append(system_note(words))
                return None
        return self.answer(agent, message, forwarded)

    async def _reviewed(
        self,
        event: AfterRequest | BeforeReview,
        effect_type: type[HoldAnswer] | type[SaveProposal],
        *,
        failed: str,
    ) -> str | None:
        """The words of the first check whose review answers this event, or None.

        A check that fails is logged with `failed` and counts as no answer.
        """
        async for checked in self.adapters.hooks.evaluate(event, self.sessions):
            if isinstance(checked.spec.effect, effect_type):
                words = await reviewed(checked, self.provider, failed=failed)
                if words is not None:
                    return words
        return None

    async def before_review(self, outcome: AIOutcome) -> AIOutcome:
        """The head of a queue, before its screen is drawn: a check may save it unseen, and
        resolving it moves on to the next head, which is read the same way."""
        if (
            outcome.kind is not AIOutcomeKind.PROPOSAL
            or outcome.proposal_id is None
            or not self.adapters.hooks.listens(BeforeReview)
        ):
            return outcome
        event = await self._head(outcome.proposal_id)
        if event is None:
            return outcome
        reason = await self._reviewed(
            event,
            SaveProposal,
            # A check that cannot decide shows the screen: the owner decides it after all.
            failed="The hook %s failed; the screen is shown: %s",
        )
        if reason is None:
            return outcome
        # Kept like the owner's Save, and written with the rest once the queue ends.
        advanced = await self.resolve(
            outcome.proposal_id,
            decision=BatchDecision.APPROVED,
            result={"approval_source": AUTOAPPROVED, "autoapproval_reason": reason},
        )
        return advanced or AIOutcome(
            AIOutcomeKind.ANSWER, f"{AUTO_SAVED_RECEIPT} the proposed change."
        )

    async def _head(self, proposal_id: int) -> BeforeReview | None:
        """The active head of one batch, as a check reads it: the owner's words and the
        proposal, never the conversation around them."""
        async with self.sessions() as session:
            batch = self.reviews.batch_for_proposal(proposal_id)
            if batch is None:
                return None
            head = batch.state.head
            proposal = self.reviews.proposal(proposal_id)
            if head is None or head.proposal_id != proposal_id or proposal is None:
                return None
            description = await self.renderer.describe(session, proposal_id)
            return BeforeReview(
                proposal_id=proposal_id,
                request=batch.request,
                changes=tuple(
                    ReviewedChange(
                        entity=change.entity,
                        action=change.action.value,
                        entity_id=change.entity_id,
                        values=dict(change.values),
                        criterion=self.proposals.criterion(change),
                    )
                    for change in proposal.changes
                ),
                summary=description.summary,
                fields=tuple(description.fields),
            )

def _subsession_outcome(ended: PendingTool) -> AIOutcome:
    """What the call that ended the session asks of the chat: `sub` opens a subsession
    with the agent it named, `finish` closes the open one with its result."""
    if ended.call.name == "sub":
        return AIOutcome(AIOutcomeKind.ENTER, "", subsession=ended.result["agent"])
    return AIOutcome(AIOutcomeKind.FINISH, ended.result["result"])


def _owner_request(dialogue: list[dict[str, Any]]) -> str:
    """The owner's own last words in the session that proposed this."""
    return next(
        (
            str(item.get("content", ""))
            for item in reversed(dialogue)
            if item.get("role") == "user"
        ),
        "",
    )


def _decided_calls(messages: list[dict[str, Any]]) -> dict[tuple[str, str], BatchDecision]:
    """Each call of this session whose change the owner saved or discarded, by `call_key`.

    A failed or expired one is not here: the owner did not decide it, and the same call
    may be proposed again.
    """
    decided: dict[tuple[str, str], BatchDecision] = {}
    for key, content in answered_calls(messages):
        try:
            status = json.loads(content).get("status")
        except (json.JSONDecodeError, AttributeError):
            continue
        if status in (BatchDecision.APPROVED, BatchDecision.DISCARDED):
            decided[key] = BatchDecision(status)
    return decided


def _fill_in_results(agent: AgentSession, tool_results: list[dict[str, Any]]) -> None:
    """Replace this turn's tool messages with the corrected results, for one more round."""
    messages = json_safe(agent.messages)
    results_by_id = {tool["id"]: tool["result"] for tool in tool_results}
    for message in messages:
        if message.get("role") != "tool":
            continue
        tool_call_id = str(message.get("tool_call_id"))
        if tool_call_id in results_by_id:
            message["content"] = json.dumps(
                results_by_id[tool_call_id], ensure_ascii=False, default=str
            )
    agent.messages = messages
