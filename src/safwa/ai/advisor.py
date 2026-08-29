"""The Advisor: the one session that writes to the chat, and what the interface calls.

It owns no mutation tool. It reads, it routes, and it answers. The loop and the routed
chain belong to `agent_runtime`; what is here is the wiring — which store, which tools,
which context — and the two entry points the interface uses: a turn (`handle`) and a
decision on an open review (`resolve_approval`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime import (
    AgentManager,
    AgentSession,
    RunStatus,
    TurnOutcome,
    resumed_transcript,
    route_receipt,
)
from llm_gateway import LlmProvider

from ..constants import MAX_REPAIR_ROUNDS, MAX_TOOL_CALLS, SUBAGENT_DEADLINE_SECONDS
from ..features.continuity.memory import MemoryFileStore
from ..features.proposals.api import ProposalDescription, ProposalRegistry
from ..features.proposals.model import RECEIPT_MEANINGS, BatchDecision
from ..features.proposals.reducer import INTERRUPTED
from ..features.proposals.render import (
    ProposalRenderer,
    compose_display_outcome,
    resolved_tool_result,
    results_summary,
)
from ..features.proposals.store import ProposalStore
from ..features.proposals.use_cases import decide_batch_item, interrupt_batch
from ..foundation.errors import failure_reason
from .autoapproval import AutoApprovalReviewer
from .context import DialogueMessage
from .materialize import ProposalMaterializer
from .messages import ContextBuilder, system_note
from .outcome import AIOutcome, AIOutcomeKind, as_turn
from .prepare import ChangePreparer
from .runs import AgentRunStore, AgentStepTrail
from .sql import ReadOnlyQueryRunner
from .subagents import RoutedSubagent
from .tools import Helper, ToolAdapters

logger = logging.getLogger(__name__)

# The one line an interrupted session reads about what happened to it. It has to say the
# owner wrote *instead* of deciding: on "rejected" alone the session reads its own record
# and proposes the same thing again.
REFUSED_AND_WROTE = (
    "The user did not decide this. They wrote to Safwa instead, and their words are the "
    "newest message in the conversation. Read them, then propose what they ask for now. "
    "Never propose the refused change again."
)

REPAIR_EXHAUSTED_ON_RESUME = (
    "I could not prepare the remaining requested changes after five repair "
    "attempts. No unfinished operation was applied."
)


class AIAdvisor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: LlmProvider,
        memory: MemoryFileStore,
        query_runner: ReadOnlyQueryRunner,
        proposals: ProposalRegistry,
        *,
        system_prompt: str,
        model_name: str,
        provider_name: str = "openai-compatible",
        cache_breakpoints: bool = False,
        subagents: tuple[RoutedSubagent, ...] = (),
        helpers: Mapping[str, Helper] | None = None,
        autoapproval: AutoApprovalReviewer | None = None,
        reviews: ProposalStore | None = None,
    ) -> None:
        self.sessions = sessions
        self.proposals = proposals
        self.subagents = {routed.name: routed for routed in subagents}
        # Every review this process still owes an answer to. It is memory, not a table: a
        # restart is what ends them, and nothing outside this process ever reads one.
        self.reviews = reviews if reviews is not None else ProposalStore()
        self.review_view = ProposalRenderer(self.reviews, self.proposals)
        self.store = AgentRunStore(
            sessions, provider_name=provider_name, model_name=model_name
        )
        self.adapters = ToolAdapters(
            sessions, query_runner, proposals, helpers, subagents=self.subagents
        )
        self.context = ContextBuilder(
            sessions,
            memory,
            system_prompt=system_prompt,
            subagents=self.subagents,
            cache_breakpoints=cache_breakpoints,
        )
        self.preparer = ChangePreparer(provider, query_runner, proposals)
        self.materializer = ProposalMaterializer(
            sessions,
            self.reviews,
            proposals,
            self.review_view,
            self.preparer,
            self.adapters,
            self.store,
            resolve=self.resolve_approval,
            autoapproval=autoapproval,
        )
        self.runtime = AgentManager(
            self.store,
            provider,
            self.adapters,
            self.context,
            self.materializer,
            routed_kinds=frozenset(self.subagents),
            max_tool_calls=MAX_TOOL_CALLS,
            max_repair_rounds=MAX_REPAIR_ROUNDS,
            child_deadline_seconds=SUBAGENT_DEADLINE_SECONDS,
            receipt_prefixes=tuple(RECEIPT_MEANINGS),
            interrupted_note=REFUSED_AND_WROTE,
            observer=AgentStepTrail(sessions),
        )

    async def handle(
        self,
        text: str,
        *,
        source_message_id: int | None = None,
        dialogue: list[DialogueMessage] | None = None,
    ) -> AIOutcome:
        turn_dialogue = (
            [{"role": item.role, "content": item.content} for item in dialogue]
            if dialogue
            else [{"role": "user", "content": text}]
        )
        # Words typed over a screen are an answer to the request that opened it, so that
        # request continues rather than being replaced by a second one.
        outcome = await self.runtime.handle(
            turn_dialogue, source_message_id=source_message_id
        )
        return outcome.payload

    async def describe_proposal(
        self, session: AsyncSession, proposal_id: int
    ) -> ProposalDescription:
        """How one proposal reads to the owner, for whoever is drawing a screen."""
        return await self.review_view.describe(session, proposal_id)

    def has_pending_approval(self, proposal_id: int) -> bool:
        """Return whether this screen belongs to a suspended agent turn."""
        return self.reviews.batch_for_proposal(proposal_id) is not None

    async def resolve_approval(
        self,
        proposal_id: int,
        *,
        decision: BatchDecision,
        result: dict[str, Any],
        dialogue: list[DialogueMessage] | None = None,
        apply_proposal: bool = False,
        held_run_id: int | None = None,
    ) -> AIOutcome | None:
        """Resolve one queued screen and resume the suspended tool turn once complete."""
        started = time.monotonic()
        async with self.sessions() as session:
            decided = await decide_batch_item(
                session,
                self.reviews,
                self.proposals,
                proposal_id,
                decision=decision,
                apply_change=apply_proposal,
                render=lambda tool, affected: resolved_tool_result(
                    tool,
                    decision,
                    {**result, "affected_ids": affected} if apply_proposal else result,
                ),
            )
            if decided is None:
                return None
            tools = decided.tool_calls
            head = decided.state.head
            if head is not None:
                await session.commit()
                return await self.materializer.advance_autoapprovals(
                    AIOutcome(
                        AIOutcomeKind.PROPOSAL,
                        "Review the next proposed change.",
                        proposal_id=head.proposal_id,
                    ),
                    held_run_id=held_run_id,
                )
            # The queue is empty, so the batch has done its whole job.  Closing it and
            # claiming the session in the same commit is what makes a crash here cost
            # nothing: no half-open batch is left to route a later press into, and the
            # session is left plainly interrupted.
            record = await self.store.claim_within(
                session, decided.run_id, held_run_id=held_run_id
            )
            if record is None:
                logger.warning("Session #%s is already resuming", decided.run_id)
                await session.commit()
                return None
            repair_exhausted = decided.state.repair_exhausted
            agent, stored_transcript = self.runtime.restore(record)
            if not agent.dialogue:
                agent.dialogue = [
                    {"role": item.role, "content": item.content} for item in dialogue or []
                ]
            await session.commit()

        try:
            result_summary = results_summary(tools)
            if result_summary:
                agent.result_summaries.append(result_summary)
            display_summary = results_summary(
                tools, include_preparation_errors=False, for_display=True
            )
            if display_summary:
                agent.display_result_summaries.append(display_summary)
            if repair_exhausted:
                await self.store.finish(
                    agent.run_id,
                    status=RunStatus.COMPLETED,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                answered = self.materializer.answer(agent, REPAIR_EXHAUSTED_ON_RESUME)
                outcome = await self._answer_or_deliver(agent, answered)
            else:
                outcome = await self.runtime.continue_session(
                    agent, resumed_transcript(stored_transcript, tools), started
                )
                if outcome.waiting:
                    return outcome.payload
                outcome = await self._answer_or_deliver(agent, outcome)
            answer: AIOutcome = outcome.payload
            answer.did.extend(display_summary.splitlines())
            return answer
        except Exception as error:
            logger.exception("AI continuation failed after the approval queue was resolved")
            await self.store.finish(
                agent.run_id,
                status=RunStatus.FAILED,
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code=type(error).__name__,
            )
            result_summary = results_summary(tools, for_display=True)
            if result_summary:
                return AIOutcome(
                    AIOutcomeKind.ANSWER,
                    compose_display_outcome(
                        f"⚠️ Safwa could not generate its follow-up ({failure_reason(error)}). "
                        "You can continue with a new message.",
                        [result_summary],
                    ),
                )
            raise

    async def _answer_or_deliver(
        self, agent: AgentSession, outcome: TurnOutcome
    ) -> TurnOutcome:
        """End a resumed session: hand its receipt to its caller, or answer the owner."""
        if agent.parent_run_id is None:
            return outcome
        receipt = route_receipt(
            agent.kind,
            outcome.message,
            agent.display_result_summaries,
            receipt_prefixes=tuple(RECEIPT_MEANINGS),
        )
        delivered = await self.runtime.deliver_to_parent(agent, receipt)
        if delivered is not None:
            return delivered
        # The caller is already resuming elsewhere; the owner still gets the receipts.
        return as_turn(
            AIOutcome(
                AIOutcomeKind.ANSWER,
                compose_display_outcome(outcome.message, agent.display_result_summaries)
                or "✅ Done.",
            )
        )

    async def cancel_approval_for_proposal(self, proposal_id: int) -> str | None:
        """End the batch behind a screen the owner wrote over, and record what it did.

        Returns the consolidated result of the interrupted request, or ``None`` when the
        screen does not belong to a suspended batch.  The caller needs that text because
        earlier items in the queue may already be saved: freezing the screen as a plain
        "discarded" notice would tell both the owner and the model something untrue.

        Nothing is cancelled.  The session that wrote the refused proposal keeps its own
        plan, so "the same, but capitalise the name" reaches the session that wrote it,
        and the request that routed there keeps its turn: those words are its answer, and
        `handle` resumes it with them rather than starting a second request over the top.
        """
        interrupted = interrupt_batch(self.reviews, proposal_id, reason=INTERRUPTED)
        if interrupted is None:
            return None
        tools = interrupted.tool_calls
        summary = results_summary(tools, include_preparation_errors=False, for_display=True)
        prior_summaries = await self._leave_unfinished(interrupted.run_id, tools, summary)
        return compose_display_outcome(
            "", [line for line in (*prior_summaries, summary) if line]
        )

    async def _leave_unfinished(
        self, run_id: int, tools: list[dict[str, Any]], summary: str
    ) -> list[str]:
        """Store what the refused session knows, and tell its chain's root it was written over.

        Unfinished rather than waiting: no screen is open on it any more.  It stays for a
        `route` back on this same turn, and the turn that routed to it is what closes it.
        """
        record = await self.store.get(run_id)
        if record is None:
            return []
        state = dict(record.state)
        prior_summaries = list(state.get("display_result_summaries") or [])
        state["transcript"] = [
            *resumed_transcript([dict(item) for item in state.get("transcript") or []], tools),
            system_note(REFUSED_AND_WROTE),
        ]
        await self.store.leave_interrupted(run_id, state, summary)
        return prior_summaries
