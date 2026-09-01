"""The Advisor: the one session that writes to the chat, and what the interface calls.

It owns no mutation tool. It reads, it routes, and it answers. The loop and the routed
chain belong to `agent_runtime`; what is here is the wiring — which store, which tools,
which context — and the two entry points the interface uses: a turn (`handle`) and a
decision on an open review (`resolve_approval`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime import (
    AgentManager,
    InteractionRef,
    Resumption,
    TurnOutcome,
)
from llm_gateway import LlmProvider
from telegram_llm import DialogueMessage

from ...ai.autoapproval import AutoApprovalReviewer
from ...ai.messages import ContextBuilder
from ...ai.outcome import AIOutcome, AIOutcomeKind
from ...ai.runs import AgentRunStore, AgentStepTrail
from ...ai.sql import ReadOnlyQueryRunner
from ...ai.subagents import RoutedSubagent
from ...ai.tools import Helper, ToolAdapters
from ...constants import MAX_REPAIR_ROUNDS, MAX_TOOL_CALLS, SUBAGENT_DEADLINE_SECONDS
from ...foundation.errors import failure_reason
from ...foundation.screens import ScreenCatalogue
from ..board.state import board_context
from ..continuity.memory import MemoryFileStore
from ..proposals.api import ProposalDescription, ProposalRegistry
from ..proposals.materialize import ProposalMaterializer
from ..proposals.model import RECEIPT_MEANINGS, BatchDecision
from ..proposals.prepare import ChangePreparer
from ..proposals.reducer import INTERRUPTED
from ..proposals.render import (
    ProposalRenderer,
    compose_display_outcome,
    resolved_tool_result,
    results_summary,
)
from ..proposals.store import ProposalStore
from ..proposals.use_cases import decide_batch_item, interrupt_batch

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
        screens: ScreenCatalogue,
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
        # One trail for the whole turn: the runtime writes `route` to it and the adapters
        # write every other call, so `agent_steps` has a single writer.
        self.trail = AgentStepTrail(sessions)
        self.adapters = ToolAdapters(
            sessions,
            query_runner,
            proposals,
            self.trail,
            screens,
            helpers,
            subagents=self.subagents,
        )
        self.context = ContextBuilder(
            sessions,
            memory,
            board_context,
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
            observer=self.trail,
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
        return await self._decide_or_show(outcome)

    async def _decide_or_show(self, outcome: TurnOutcome) -> AIOutcome:
        """A turn that stopped on the owner is offered to autoapproval before it is drawn.

        The turn is over by the time this runs: its session is stored and released, so an
        automatic Save resumes it exactly the way the owner's press would.
        """
        answer: AIOutcome = outcome.payload
        if not outcome.waiting or outcome.ref is None:
            return answer
        # The screens this turn opened are answered by naming the session it suspended.
        self.reviews.wait_on(outcome.ref.run_id, outcome.ref.token)
        return await self.materializer.advance_autoapprovals(answer)

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
        apply_proposal: bool = False,
    ) -> AIOutcome | None:
        """Resolve one queued screen, and resume the suspended session once the queue empties."""
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
            await session.commit()
        tools = decided.tool_calls
        head = decided.state.head
        if head is not None:
            return await self.materializer.advance_autoapprovals(
                AIOutcome(
                    AIOutcomeKind.PROPOSAL,
                    "Review the next proposed change.",
                    proposal_id=head.proposal_id,
                )
            )
        # The batch has done its whole job, so the session it suspended is answered with
        # what every one of its calls came back with.
        display_summary = results_summary(
            tools, include_preparation_errors=False, for_display=True
        )
        try:
            outcome = await self.runtime.resume(
                InteractionRef(decided.run_id, decided.interaction_token),
                Resumption(
                    results=_call_results(tools),
                    notes=_lines(results_summary(tools)),
                    display_notes=_lines(display_summary),
                    answer=(
                        REPAIR_EXHAUSTED_ON_RESUME
                        if decided.state.repair_exhausted
                        else None
                    ),
                ),
            )
        except Exception as error:
            logger.exception("AI continuation failed after the approval queue was resolved")
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
        if outcome is None:
            # Nothing was resumed, so nothing was generated and nothing is said over the
            # top of whoever is resuming it. The interface writes its own receipt for the
            # decision, the same one a proposal outside a batch gets.
            return None
        if outcome.waiting:
            return await self._decide_or_show(outcome)
        return outcome.payload

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
        prior_summaries = await self.runtime.interrupt(
            InteractionRef(interrupted.run_id, interrupted.interaction_token),
            _call_results(tools),
            summary=summary,
        )
        return compose_display_outcome(
            "", [line for line in (*(prior_summaries or ()), summary) if line]
        )


def _call_results(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """What each of the suspended session's own calls came back with, by call id."""
    return {str(tool["id"]): tool.get("result") for tool in tools if tool.get("id")}


def _lines(summary: str) -> tuple[str, ...]:
    return (summary,) if summary else ()
