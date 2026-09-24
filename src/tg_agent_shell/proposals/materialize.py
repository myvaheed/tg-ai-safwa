"""Where a tool call becomes a review the owner decides.

This is the seam the whole proposal rule rests on: the model never mutates, so its calls
arrive here as prepared changes, and what leaves is either an open review or words. A call
that could not be prepared, or that a check sent back, goes back to the model as a
corrected tool result, and the session runs again — `None` from `materialize` is what asks
the runtime for that. An answer a check held back runs the session again the same way.

The checks are hooks: `BeforeProposals` before any call of a response is prepared, and
`AfterRequest` before the answer to the owner's message is sent.
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
    json_safe,
    system_note,
)
from llm_gateway import LlmProvider

from ..ai.autoapproval import AutoApprovalCandidate, AutoApprovalChange, AutoApprovalReviewer
from ..ai.contracts import ToolResultStatus
from ..ai.outcome import AIOutcome, AIOutcomeKind, as_turn
from ..ai.tools import (
    REPAIR_EXHAUSTED,
    ToolAdapters,
    WatcherFailed,
    conversation_for,
    response_text,
)
from ..foundation.errors import DomainError
from ..hooks.contracts import AfterRequest, BeforeProposals, HoldAnswer, ProposedCall
from .api import ProposalRegistry, ToolPreparationError
from .model import AUTO_SAVED_RECEIPT, BatchDecision, ProposalChange
from .prepare import ChangePreparer
from .render import (
    AUTOAPPROVED,
    ProposalRenderer,
    compose_display_outcome,
    with_queued_siblings,
)
from .store import ProposalStore
from .use_cases import open_batch, prepare_change, queue_proposals

logger = logging.getLogger(__name__)

MAX_REPAIR_ROUNDS = 5

# What the Advisor reads in place of the words of a subagent whose words are shown as they
# are. It speaks of that block alone, so it never argues with anything else in the turn.
SHOWN_AS_IS = (
    "Shown to the user as is. Do not repeat it. If nothing else was asked, add one short line."
)

# Set on a session that answers a message of the owner's, and taken off by its first answer
# in words, which is the one `AfterRequest` is about.
OWNER_REQUEST = "owner_request"

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
        autoapproval: AutoApprovalReviewer | None = None,
    ) -> None:
        self.sessions = sessions
        self.reviews = reviews
        self.proposals = proposals
        self.renderer = renderer
        self.preparer = preparer
        self.adapters = adapters
        self.resolve = resolve
        self.autoapproval = autoapproval
        # What a check that holds an answer reads it with.
        self.provider = provider

    def answer(self, agent: AgentSession, message: str) -> TurnOutcome:
        """One session's words, and — for the session the owner reads — the blocks shown as
        they are and its receipts.

        A subagent's words go to whoever routed to it, so they are handed over untouched —
        unless it is declared shown as is: then they are a block for the owner to read, and
        its caller is told they were shown instead of being handed them to retell. The root
        is the only participant that writes to the chat, which makes it the one place that
        has to guarantee the owner is never left with nothing.
        """
        if agent.parent_run_id is not None:
            routed = self.adapters.subagents.get(agent.kind)
            if routed is not None and routed.shown_as_is and message.strip():
                agent.shown_blocks.append(message.strip())
                message = SHOWN_AS_IS
            return as_turn(AIOutcome(AIOutcomeKind.ANSWER, message))
        composed = compose_display_outcome(
            message, agent.display_result_summaries, agent.shown_blocks
        )
        return as_turn(
            AIOutcome(
                AIOutcomeKind.ANSWER,
                composed or "⚠️ There was nothing to say about that. You can ask again.",
                open_item=agent.host_state.get("open_item"),
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
        if not result.pending_tools:
            return await self._answer_unless_held(agent, result.message)
        mutation_tools = [tool for tool in result.pending_tools if tool.change is not None]
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
        async with self.sessions() as session:
            # Preparation writes nothing, so every call of the response is made against this
            # one revision.
            revision = (await self.preparer.world(session)).revision
            for tool in mutation_tools:
                try:
                    change = await prepare_change(session, self.preparer, tool.change)
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
                self.reviews, prepared, message=result.message, workspace_revision=revision
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
        self, agent: AgentSession, message: str
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
            words = await self._held(
                AfterRequest(
                    run_id=agent.run_id,
                    conversation=conversation_for(agent.dialogue),
                    done=tuple(agent.display_result_summaries),
                    answer=message,
                )
            )
            if words is not None:
                agent.messages.append({"role": "assistant", "content": message})
                agent.messages.append(system_note(words))
                return None
        return self.answer(agent, message)

    async def _held(self, event: AfterRequest) -> str | None:
        """The words of the first check that holds this answer, or None to send it."""
        async for checked in self.adapters.hooks.evaluate(event, self.sessions):
            effect = checked.spec.effect
            error = checked.error
            if error is None and isinstance(effect, HoldAnswer):
                try:
                    for payload in checked.payloads:
                        words = await effect.review(payload, self.provider)
                        if words and words.strip():
                            return words
                except Exception as failure:
                    error = failure
            if error is not None:
                # A check that cannot decide lets the answer through: the owner still
                # reads it, and decides every screen.
                logger.error("The hook %s failed; the answer goes out: %s", checked.spec.name, error)
        return None

    async def advance_autoapprovals(self, outcome: AIOutcome) -> AIOutcome:
        """Auto-save one eligible head; resolving it advances and checks the next head."""
        if (
            self.autoapproval is None
            or outcome.kind is not AIOutcomeKind.PROPOSAL
            or outcome.proposal_id is None
        ):
            return outcome
        candidate = await self._candidate(outcome.proposal_id)
        if candidate is None:
            return outcome
        verdict = await self.autoapproval.review(candidate)
        if not verdict.approved:
            return outcome
        try:
            advanced = await self.resolve(
                outcome.proposal_id,
                decision=BatchDecision.APPROVED,
                result={
                    "approval_source": AUTOAPPROVED,
                    "autoapproval_reason": verdict.reason,
                },
                apply_proposal=True,
            )
        except Exception as error:
            logger.warning(
                "Autoapproval apply failed for proposal #%s: %s", outcome.proposal_id, error
            )
            if self.reviews.proposal(outcome.proposal_id) is not None:
                # The write was rolled back and the review still stands, so the owner
                # decides it after all.
                return outcome
            # A refusal ends the review with it, so there is no screen left to fall back
            # to: the queue moves on the way it moves on for a failed manual Save.
            advanced = await self.resolve(
                outcome.proposal_id,
                decision=BatchDecision.FAILED,
                result={"error": str(error)},
            )
            return advanced or AIOutcome(
                AIOutcomeKind.ANSWER,
                "⚠️ The proposed change was not saved: the workspace moved on since it "
                "was proposed. Ask for it again.",
            )
        return advanced or AIOutcome(
            AIOutcomeKind.ANSWER, f"{AUTO_SAVED_RECEIPT} the proposed change."
        )

    async def _candidate(self, proposal_id: int) -> AutoApprovalCandidate | None:
        """Build the reviewer's request-only view for the active head of one batch."""
        async with self.sessions() as session:
            batch = self.reviews.batch_for_proposal(proposal_id)
            if batch is None:
                return None
            head = batch.state.head
            proposal = self.reviews.proposal(proposal_id)
            if head is None or head.proposal_id != proposal_id or proposal is None:
                return None
            description = await self.renderer.describe(session, proposal_id)
            return AutoApprovalCandidate(
                user_request=batch.request,
                changes=tuple(
                    AutoApprovalChange(
                        entity=change.entity,
                        action=change.action,
                        entity_id=change.entity_id,
                        values=dict(change.values),
                    )
                    for change in proposal.changes
                ),
                summary=description.summary,
                fields=tuple(description.fields),
            )

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
