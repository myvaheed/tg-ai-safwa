"""Where a tool call becomes a review the owner decides.

This is the seam the whole proposal rule rests on: the model never mutates, so its calls
arrive here as prepared changes, and what leaves is either an open review or words. A call
that could not be prepared goes back to the model as a corrected tool result, and the
session runs again — `None` from `materialize` is what asks the runtime for that.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime import AgentLoopResult, AgentSession, TurnOutcome, json_safe

from ..ai.autoapproval import AutoApprovalCandidate, AutoApprovalReviewer
from ..ai.outcome import AIOutcome, AIOutcomeKind, as_turn
from ..ai.tools import REPAIR_EXHAUSTED, ToolAdapters
from ..foundation.errors import DomainError
from .api import ProposalRegistry, ToolPreparationError
from .model import AUTO_SAVED_RECEIPT, BatchDecision, QueueItem
from .prepare import ChangePreparer
from .render import (
    AUTOAPPROVED,
    ProposalRenderer,
    compose_display_outcome,
    with_queued_siblings,
)
from .store import ProposalStore
from .use_cases import number_queued_proposals, open_batch, prepare_proposal

logger = logging.getLogger(__name__)

MAX_REPAIR_ROUNDS = 5

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

    def answer(self, agent: AgentSession, message: str) -> TurnOutcome:
        """One session's words, and — for the session the owner reads — its receipts.

        A subagent's words go to whoever routed to it, so they are handed over untouched.
        The root is the only participant that writes to the chat, which makes it the one
        place that has to guarantee the owner is never left with nothing.
        """
        if agent.parent_run_id is not None:
            return as_turn(AIOutcome(AIOutcomeKind.ANSWER, message))
        composed = compose_display_outcome(message, agent.display_result_summaries)
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
        run again — the repair round, bounded by `MAX_REPAIR_ROUNDS`.
        """
        if not result.pending_tools:
            return self.answer(agent, result.message)
        mutation_tools = [tool for tool in result.pending_tools if tool.change is not None]
        preparation_results = {
            tool.call.id: json_safe(tool.result) for tool in result.pending_tools
        }
        failed_call_ids = {
            tool.call.id
            for tool in result.pending_tools
            if not self.adapters.is_immediate(agent, tool.call.name) and tool.change is None
        }
        proposal_details: dict[str, list[str]] = {}
        proposal_displays: dict[str, str] = {}
        # In the order the model made the calls, which is the order the owner reviews them in.
        queued_proposal_ids: dict[str, int] = {}
        async with self.sessions() as session:
            for tool in mutation_tools:
                try:
                    proposal = await prepare_proposal(
                        session,
                        self.reviews,
                        self.preparer,
                        message=result.message,
                        change=tool.change,
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
                proposal_details[tool.call.id] = await self.renderer.result_details(
                    session, proposal.id, tool.change
                )
                proposal_displays[tool.call.id] = await self.renderer.display_line(
                    session, proposal.id, tool.change, proposal_details[tool.call.id]
                )
                queued_proposal_ids[tool.call.id] = proposal.id
            queue = [
                QueueItem(proposal_id=proposal_id, call_ids=(call_id,))
                for call_id, proposal_id in queued_proposal_ids.items()
            ]
            number_queued_proposals(self.reviews, queue, result.message)
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
                            preparation_results[tool.call.id], len(queue)
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
            # `approve_proposal` and the batch decision share one transaction. A failure
            # therefore leaves the original pending proposal safe to render as-is.
            logger.warning(
                "Autoapproval apply failed for proposal #%s; keeping manual review: %s",
                outcome.proposal_id,
                error,
            )
            return outcome
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
            if head is None or head.proposal_id != proposal_id:
                return None
            change = self.renderer.only_change(proposal_id)
            if change is None:
                return None
            description = await self.renderer.describe(session, proposal_id)
            return AutoApprovalCandidate(
                user_request=batch.request,
                entity=change.entity,
                action=change.action,
                entity_id=change.entity_id,
                values=dict(change.values),
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
