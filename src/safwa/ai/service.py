from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import CompletionRequest, CompletionTurn, LlmProvider, ToolCall

from ..constants import (
    MAX_REPAIR_ROUNDS,
    MAX_TOOL_CALLS,
    SUBAGENT_DEADLINE_SECONDS,
)
from ..domain import (
    DomainError,
    utcnow,
)
from ..features.continuity.memory import MemoryFileStore
from ..features.proposals.api import (
    ProposalDescription,
    ProposalRegistry,
    ToolPreparationError,
)
from ..features.proposals.model import (
    AUTO_SAVED_RECEIPT,
    RECEIPT_MEANINGS,
    BatchDecision,
    ChangeAction,
    QueueItem,
)
from ..features.proposals.reducer import INTERRUPTED
from ..features.proposals.render import (
    AUTOAPPROVED,
    ProposalRenderer,
    compose_display_outcome,
    resolved_tool_result,
    results_summary,
    with_queued_siblings,
)
from ..features.proposals.store import ProposalStore
from ..features.proposals.use_cases import (
    decide_batch_item,
    interrupt_batch,
    number_queued_proposals,
    open_batch,
    prepare_proposal,
)
from ..foundation.errors import failure_reason
from ..models import AgentRun, AgentRunStatus, AgentStep
from .autoapproval import AutoApprovalCandidate, AutoApprovalReviewer
from .context import DialogueMessage
from .contracts import (
    AgentChange,
    CallHelperInput,
    OpenInput,
    RouteInput,
    ToolResultStatus,
    tool_json_schema,
)
from .messages import ContextBuilder, system_note
from .mini import QUERY_SAFWA_TOOL, ReadToolSpec
from .prepare import ChangePreparer
from .sql import ReadOnlyQueryRunner
from .subagents import RoutedSubagent
from .tools import (
    Helper,
    ToolAdapters,
    json_safe,
    log_preview,
    validation_error_summary,
)

logger = logging.getLogger(__name__)

OPEN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "open",
        "description": (
            "Put one item on the screen, exactly as the user opening it by hand. Call it only "
            "when the user asked to see or open one single item. Otherwise cite the item in "
            "your answer instead."
        ),
        "parameters": tool_json_schema(OpenInput),
    },
}
ROUTE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "route",
        "description": (
            "Hand this turn to a subagent. It reads this same conversation, does the work, "
            "and comes back with a receipt of what it did. You write the message the user "
            "sees. You just pass the name of the subagent."
        ),
        "parameters": tool_json_schema(RouteInput),
    },
}
CALL_HELPER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "call_helper",
        "description": (
            "Ask a helper a question one simple read could not answer. It writes the query "
            "and hands back its result. You keep the turn and you write the answer."
        ),
        "parameters": tool_json_schema(CallHelperInput),
    },
}
# The Advisor reads and routes. Every mutation tool belongs to the subagent that owns that
# feature, so judging *which* change to propose happens where the change is authored.
SAFWA_TOOLS = (QUERY_SAFWA_TOOL, OPEN_TOOL)
# Tools that run during the turn instead of becoming a proposal the owner approves.
IMMEDIATE_TOOLS = frozenset({"query_safwa", "route", "open", "call_helper"})

# The one line an interrupted session reads about what happened to it. It has to say the
# owner wrote *instead* of deciding: on "rejected" alone the session reads its own record
# and proposes the same thing again.
REFUSED_AND_WROTE = (
    "The user did not decide this. They wrote to Safwa instead, and their words are the "
    "newest message in the conversation. Read them, then propose what they ask for now. "
    "Never propose the refused change again."
)


class AIOutcomeKind(StrEnum):
    ANSWER = "answer"
    PROPOSAL = "proposal"


@dataclass
class AIOutcome:
    kind: AIOutcomeKind
    message: str
    proposal_id: int | None = None
    did: list[str] = field(default_factory=list)
    # The item `open` resolved, as a deep-link payload: the chat shows its screen after
    # the answer.
    open_item: str | None = None


@dataclass
class PendingTool:
    call: ToolCall
    result: Any
    change: AgentChange | None = None


@dataclass
class AgentSession:
    """One model session: what it may call, what it has said, and what it has spent.

    The budget and the transcript belong to the session rather than to a single turn,
    because both survive an approval: the same session resumes once the owner decides.
    """

    run_id: int
    tools: tuple[dict[str, Any], ...]
    kind: str = "advisor"
    read_specs: dict[str, ReadToolSpec] = field(default_factory=dict)
    dialogue: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    prefix_len: int = 0
    tool_count: int = 0
    repair_rounds: int = 0
    result_summaries: list[str] = field(default_factory=list)
    display_result_summaries: list[str] = field(default_factory=list)
    # The session this one routed to, and the `route` call still waiting for its receipt.
    parent_run_id: int | None = None
    awaiting_route: dict[str, Any] | None = None
    # What this turn had already saved when the caller routed here.
    prior_receipts: list[str] = field(default_factory=list)
    # The item `open` resolved, kept until the session answers the owner.
    open_item: str | None = None
    # Whether a read in this session was complex enough to be offered a helper. The tool
    # is added when that happens, and `tools` is rebuilt from the kind on a resume — so a
    # session that routed a change and came back would lose a tool it had been shown.
    helper_offered: bool = False

    def offer_helper(self) -> None:
        """Put `call_helper` on this session's tools, once, and remember that it is there."""
        if self.helper_offered:
            return
        self.helper_offered = True
        self.tools = (*self.tools, CALL_HELPER_TOOL)

    @property
    def immediate(self) -> frozenset[str]:
        """Tool names that run inside the turn instead of becoming a proposal."""
        return frozenset({*IMMEDIATE_TOOLS, *self.read_specs})

    @property
    def transcript(self) -> list[dict[str, Any]]:
        """The assistant/tool exchanges this request produced, without its context prefix.

        Everything before ``prefix_len`` is rebuilt from live state on every turn; this
        tail is what an approval must replay so the model keeps its own intermediate
        steps instead of re-planning the request from the last tool call alone.
        """
        return self.messages[self.prefix_len :]

    def state(self) -> dict[str, Any]:
        """Everything the session needs to continue once the owner has decided."""
        return {
            "dialogue": self.dialogue,
            "transcript": json_safe(self.transcript),
            "tool_count": self.tool_count,
            "repair_rounds": self.repair_rounds,
            "result_summaries": self.result_summaries,
            "display_result_summaries": self.display_result_summaries,
            "awaiting_route": self.awaiting_route,
            "prior_receipts": self.prior_receipts,
            "open_item": self.open_item,
            "helper_offered": self.helper_offered,
        }

    @classmethod
    def restore(
        cls,
        run: AgentRun,
        tools: tuple[dict[str, Any], ...],
        read_specs: dict[str, ReadToolSpec] | None = None,
    ) -> tuple[AgentSession, list[dict[str, Any]]]:
        """Rebuild a suspended session from its row, with the transcript it left behind.

        The context prefix is not restored — it is rebuilt from live state, so the owner's
        planning data and clock are current while the session's own steps are not replayed
        from anything but its own record.
        """
        state = dict(run.state_json or {})
        session = cls(
            run_id=run.id,
            tools=tools,
            kind=run.kind,
            read_specs=dict(read_specs or {}),
            dialogue=[dict(item) for item in state.get("dialogue") or []],
            tool_count=int(state.get("tool_count", 0)),
            repair_rounds=int(state.get("repair_rounds", 0)),
            result_summaries=list(state.get("result_summaries") or []),
            display_result_summaries=list(state.get("display_result_summaries") or []),
            parent_run_id=run.parent_run_id,
            awaiting_route=state.get("awaiting_route") or None,
            prior_receipts=list(state.get("prior_receipts") or []),
            open_item=state.get("open_item") or None,
        )
        if state.get("helper_offered"):
            session.offer_helper()
        return session, [dict(item) for item in state.get("transcript") or []]


@dataclass
class AgentLoopResult:
    """What one turn of a session produced: its words, its changes, or a suspension."""

    message: str
    pending_tools: list[PendingTool] = field(default_factory=list)
    # Set when a subagent this session routed to opened a screen: the whole chain waits
    # for the owner, and this is what they see meanwhile.
    suspended: AIOutcome | None = None


def _flatten_content(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return str(content or "")


def _route_receipt(
    name: str,
    message: str,
    summaries: list[str],
    *,
    did: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """What a finished subagent hands back to whoever routed to it.

    `did` is the same Saved/Discarded/Failed lines the owner reads, so there is one shape
    of receipt in the system.  `text` is the subagent's own words with its own citations —
    real ids the caller can reuse — and never the body of what it proposed.
    """
    receipt_lines = [line for summary in summaries for line in summary.splitlines() if line.strip()]
    receipt_lines.extend(line for line in did or [] if line.strip())
    receipt: dict[str, Any] = {
        "subagent": name,
        "outcome": "error" if error else "done",
        "did": list(dict.fromkeys(receipt_lines)),
    }
    if receipt["did"]:
        message = "\n".join(
            line for line in message.splitlines() if not line.strip().startswith(tuple(RECEIPT_MEANINGS))
        )
    if message.strip():
        receipt["text"] = message.strip()
    if error:
        receipt["error"] = error
    return receipt


def _resumed_transcript(
    transcript: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replay this request's own assistant/tool exchanges with the decisions filled in.

    No separate progress digest is injected here: every step of the request is present as
    its own call and result, each carrying its status and what to do next.  Restating them
    in an assistant message would duplicate the request once per approval.
    """
    results_by_id = {str(tool["id"]): tool.get("result") for tool in tools if tool.get("id")}
    replayed = [dict(message) for message in transcript]
    last_assistant = max(
        (index for index, message in enumerate(replayed) if message.get("role") == "assistant"),
        default=None,
    )
    if last_assistant is None:
        return replayed
    # Only the suspended turn's own results are unresolved; every earlier tool message
    # already carries its final content and must be replayed untouched.
    for message in replayed[last_assistant + 1 :]:
        if message.get("role") != "tool":
            continue
        tool_call_id = str(message.get("tool_call_id"))
        if tool_call_id in results_by_id:
            message["content"] = json.dumps(
                results_by_id[tool_call_id], ensure_ascii=False, default=str
            )
    return replayed


def _log_provider_request(messages: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    for message in messages:
        content = _flatten_content(message.get("content"))
        if message.get("tool_calls"):
            content = "tool calls: " + ", ".join(
                call["function"]["name"] for call in message["tool_calls"]
            )
        elif message.get("role") == "tool":
            content = f"{message.get('name')}: {content}"
        lines.append(f"  {message['role']:<9} {log_preview(content)}")
    logger.info("AI REQUEST ->\n%s\n%s", "\n".join(lines), "-" * 72)


def _log_provider_response(turn: CompletionTurn) -> None:
    if turn.tool_calls:
        details = "\n".join(
            f"  tool {call.name}({log_preview(call.arguments_json, 700)})"
            for call in turn.tool_calls
        )
    else:
        details = "  " + log_preview(turn.content, 1_000)
    if turn.usage is not None:
        usage = turn.usage
        cost = "" if usage.cost is None else f" cost={usage.cost}"
        details += (
            f"\n  usage prompt={usage.prompt_tokens} cached={usage.cached_tokens} "
            f"cache_write={usage.cache_write_tokens} completion={usage.completion_tokens}{cost}"
        )
    logger.info("AI RESPONSE <-\n%s\n%s", details, "-" * 72)


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
        self.provider = provider
        self.memory = memory
        self.query_runner = query_runner
        self.proposals = proposals
        self.system_prompt = system_prompt
        self.model_name = model_name
        self.provider_name = provider_name
        self.cache_breakpoints = cache_breakpoints
        self.subagents = {routed.name: routed for routed in subagents}
        self.adapters = ToolAdapters(sessions, query_runner, proposals, helpers)
        self.context = ContextBuilder(
            sessions,
            memory,
            system_prompt=system_prompt,
            subagents=self.subagents,
            cache_breakpoints=cache_breakpoints,
        )
        self.autoapproval = autoapproval
        # Every review this process still owes an answer to. It is memory, not a table: a
        # restart is what ends them, and nothing outside this process ever reads one.
        self.reviews = reviews if reviews is not None else ProposalStore()
        self.review_view = ProposalRenderer(self.reviews, self.proposals)
        self.preparer = ChangePreparer(provider, query_runner, proposals)
        # An empty roster means there is nothing to route to, so the tool is not offered.
        self.tools = (*SAFWA_TOOLS, ROUTE_TOOL) if subagents else SAFWA_TOOLS

    def _tools_for(self, kind: str) -> tuple[dict[str, Any], ...]:
        routed = self.subagents.get(kind)
        if routed is None:
            return self.tools
        return (
            *(spec.schema for spec in routed.read_tools),
            *(self.proposals.tools[name].schema() for name in routed.mutation_tools),
        )

    def _read_specs_for(self, kind: str) -> dict[str, ReadToolSpec]:
        routed = self.subagents.get(kind)
        if routed is None:
            return {}
        return {spec.name: spec for spec in routed.read_tools}

    async def handle(
        self,
        text: str,
        *,
        source_message_id: int | None = None,
        dialogue: list[DialogueMessage] | None = None,
    ) -> AIOutcome:
        started = time.monotonic()
        turn_dialogue = (
            [{"role": item.role, "content": item.content} for item in dialogue]
            if dialogue
            else [{"role": "user", "content": text}]
        )
        # Words typed over a screen are an answer to the request that opened it, so that
        # request continues rather than being replaced by a second one.
        resumed = await self._resume_interrupted_turn(turn_dialogue)
        if resumed is not None:
            return resumed

        run = AgentRun(
            kind="advisor",
            provider=self.provider_name,
            model=self.model_name,
            status=AgentRunStatus.RUNNING.value,
            source_message_id=source_message_id,
        )
        async with self.sessions() as session:
            session.add(run)
            await session.commit()

        try:
            messages = await self.context.advisor(
                dialogue or [DialogueMessage(role="user", content=text)]
            )
            agent = AgentSession(
                run_id=run.id,
                tools=self.tools,
                dialogue=turn_dialogue,
                messages=messages,
                prefix_len=len(messages),
            )
            result = await self._run_agent_loop(agent)
            if result.suspended is not None:
                # A subagent opened a screen, so this session waits for its receipt.
                await self._suspend_for_child(agent)
                await self._finish_run(run.id, AgentRunStatus.AWAITING_APPROVAL, started)
                return result.suspended
            outcome = await self._materialize(agent, result)
            status = (
                AgentRunStatus.AWAITING_APPROVAL
                if outcome.kind is AIOutcomeKind.PROPOSAL
                else AgentRunStatus.COMPLETED
            )
            await self._finish_run(run.id, status, started)
            if status is AgentRunStatus.COMPLETED:
                await self._close_unfinished_children(run.id)
            return outcome
        except Exception as error:
            logger.exception("AI advisor run failed")
            await self._finish_run(
                run.id, AgentRunStatus.FAILED, started, type(error).__name__
            )
            await self._close_unfinished_children(run.id)
            raise

    @staticmethod
    def _answer(agent: AgentSession, message: str) -> AIOutcome:
        """One session's words, and — for the session the owner reads — its receipts.

        A subagent's words go to whoever routed to it, so they are handed over untouched.
        The root is the only participant that writes to the chat, which makes it the one
        place that has to guarantee the owner is never left with nothing.
        """
        if agent.parent_run_id is not None:
            return AIOutcome(AIOutcomeKind.ANSWER, message)
        composed = compose_display_outcome(message, agent.display_result_summaries)
        return AIOutcome(
            AIOutcomeKind.ANSWER,
            composed or "⚠️ Safwa had nothing to say about that. You can ask again.",
            open_item=agent.open_item,
        )

    async def _suspend_for_child(self, agent: AgentSession) -> None:
        """Store a session that is waiting on a subagent it routed to."""
        async with self.sessions() as session:
            run = await session.get(AgentRun, agent.run_id)
            if run is not None:
                run.state_json = agent.state()
            await session.commit()

    async def _answer_or_deliver(self, agent: AgentSession, outcome: AIOutcome) -> AIOutcome:
        """End a resumed session: hand its receipt to its caller, or answer the owner."""
        if agent.parent_run_id is None:
            return outcome
        receipt = _route_receipt(agent.kind, outcome.message, agent.display_result_summaries)
        delivered = await self._deliver_to_parent(agent, receipt)
        if delivered is not None:
            return delivered
        # The caller is already resuming elsewhere; the owner still gets the receipts.
        return AIOutcome(
            AIOutcomeKind.ANSWER,
            compose_display_outcome(outcome.message, agent.display_result_summaries)
            or "✅ Done.",
        )

    async def _deliver_to_parent(
        self, child: AgentSession, receipt: dict[str, Any]
    ) -> AIOutcome | None:
        """Answer the `route` call that started this session, and run its caller on.

        Returns ``None`` when there is no caller — the session was the root of its turn.
        The loop walks the whole chain, so the turn ends only when a session with no
        parent answers.
        """
        agent = child
        while agent.parent_run_id is not None:
            started = time.monotonic()
            async with self.sessions() as session:
                run = await self._claim_session(session, agent.parent_run_id, held_run_id=None)
                if run is None:
                    logger.warning("Session #%s is already resuming", agent.parent_run_id)
                    return None
                parent, transcript = AgentSession.restore(
                    run, self._tools_for(run.kind), self._read_specs_for(run.kind)
                )
                await session.commit()
            waiting = dict(parent.awaiting_route or {})
            parent.awaiting_route = None
            parent.display_result_summaries.extend(
                str(line) for line in receipt.get("did") or []
            )
            messages = await self.context.for_session(parent.kind, parent.dialogue, parent.prior_receipts)
            parent.prefix_len = len(messages)
            messages.extend(transcript)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(waiting.get("call_id", "")),
                    "name": "route",
                    "content": json.dumps(receipt, ensure_ascii=False, default=str),
                }
            )
            parent.messages = messages
            result = await self._run_agent_loop(parent)
            if result.suspended is not None:
                await self._suspend_for_child(parent)
                await self._finish_run(
                    parent.run_id, AgentRunStatus.AWAITING_APPROVAL, started
                )
                return result.suspended
            outcome = await self._materialize(parent, result)
            if outcome.kind is AIOutcomeKind.PROPOSAL:
                await self._finish_run(
                    parent.run_id, AgentRunStatus.AWAITING_APPROVAL, started
                )
                return outcome
            await self._finish_run(parent.run_id, AgentRunStatus.COMPLETED, started)
            if parent.parent_run_id is None:
                await self._close_unfinished_children(parent.run_id)
                return outcome
            receipt = _route_receipt(
                parent.kind, outcome.message, parent.display_result_summaries
            )
            agent = parent
        return None

    async def _run_child(
        self, name: str, parent: AgentSession
    ) -> tuple[AIOutcome, dict[str, Any] | None]:
        """Run the named subagent to its own end, resuming its session if it has one.

        A saved session is picked up as it stands, so a correction to a proposal the owner
        just refused is answered by the session that wrote it, not by a rewrite.

        The receipt is ``None`` when the subagent opened a screen: it has not finished, and
        the outcome is what the owner sees while the whole chain waits for them.
        """
        routed = self.subagents[name]
        started = time.monotonic()
        async with self.sessions() as session:
            run = await self._resume_interrupted_child(session, name, parent.run_id)
            if run is None:
                run = AgentRun(
                    kind=name,
                    provider=self.provider_name,
                    model=self.model_name,
                    status=AgentRunStatus.RUNNING.value,
                    claimed_at=utcnow(),
                    parent_run_id=parent.run_id,
                )
                session.add(run)
                await session.commit()
                agent = AgentSession(
                    run_id=run.id,
                    tools=self._tools_for(name),
                    kind=name,
                    read_specs=self._read_specs_for(name),
                    parent_run_id=parent.run_id,
                )
                transcript: list[dict[str, Any]] = []
            else:
                run.parent_run_id = parent.run_id
                agent, transcript = AgentSession.restore(
                    run, self._tools_for(name), self._read_specs_for(name)
                )
            await session.commit()
        run_id = agent.run_id
        agent.dialogue = parent.dialogue
        agent.prior_receipts = list(parent.display_result_summaries)
        try:
            messages = await self.context.routed(
                routed, agent.dialogue, agent.prior_receipts
            )
            agent.prefix_len = len(messages)
            messages.extend(transcript)
            agent.messages = messages
            # The deadline bounds one active stretch of the session, never a suspension:
            # a session waiting on the owner is not a session that is taking too long.
            result = await asyncio.wait_for(
                self._run_agent_loop(agent), timeout=SUBAGENT_DEADLINE_SECONDS
            )
            outcome = await self._materialize(agent, result)
            if outcome.kind is AIOutcomeKind.PROPOSAL:
                await self._finish_run(run_id, AgentRunStatus.AWAITING_APPROVAL, started)
                return outcome, None
            await self._finish_run(run_id, AgentRunStatus.COMPLETED, started)
            # The materialized outcome, not the raw loop result: a repair round answers again.
            return outcome, _route_receipt(
                name,
                outcome.message,
                agent.display_result_summaries,
                did=outcome.did,
            )
        except TimeoutError:
            logger.warning("SUBAGENT %s timed out after %.0fs", name, SUBAGENT_DEADLINE_SECONDS)
            await self._finish_run(run_id, AgentRunStatus.FAILED, started, "timeout")
            return AIOutcome(AIOutcomeKind.ANSWER, ""), _route_receipt(
                name,
                "",
                [],
                error=f"{name} did not finish within {SUBAGENT_DEADLINE_SECONDS:.0f} seconds.",
            )
        except Exception as error:
            logger.exception("Routed subagent %s failed", name)
            await self._finish_run(
                run_id, AgentRunStatus.FAILED, started, type(error).__name__
            )
            return AIOutcome(AIOutcomeKind.ANSWER, ""), _route_receipt(
                name, "", [], error=failure_reason(error)
            )

    async def _close_unfinished_children(self, run_id: int) -> None:
        """End the sessions this turn routed to and never finished.

        The turn that routed to a subagent is its outer bound.  An interruption leaves it
        unfinished so the same turn can route back into it with a correction; when that
        turn answers the owner, or fails, there is nothing left for it to correct.
        """
        async with self.sessions() as session:
            closed = await session.scalars(
                update(AgentRun)
                .where(
                    AgentRun.parent_run_id == run_id,
                    AgentRun.status == AgentRunStatus.INTERRUPTED.value,
                )
                .values(status=AgentRunStatus.ABANDONED.value, claimed_at=None)
                .returning(AgentRun.id)
            )
            count = len(list(closed))
            await session.commit()
        if count:
            logger.info("Closed %d unfinished subagent session(s) with the turn", count)

    async def _resume_interrupted_child(
        self, session: AsyncSession, name: str, parent_run_id: int
    ) -> AgentRun | None:
        """Claim this turn's own unfinished session of that subagent, if it left one."""
        run_id = await session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.kind == name,
                AgentRun.parent_run_id == parent_run_id,
                AgentRun.status == AgentRunStatus.INTERRUPTED.value,
                AgentRun.claimed_at.is_(None),
            )
            .order_by(AgentRun.id.desc())
            .limit(1)
        )
        if run_id is None:
            return None
        return await self._claim_session(session, int(run_id), held_run_id=None)

    async def _provider_turn(self, agent: AgentSession) -> CompletionTurn:
        _log_provider_request(agent.messages)
        turn = await self.provider.complete(
            CompletionRequest(
                messages=tuple(agent.messages),
                tools=tuple(agent.tools),
                # A subagent was routed to for the work, so its first move is the work.
                # Only the first: the loop ends on a turn that calls no tool, and a
                # session that must always call one never ends.
                tool_choice=(
                    "required" if agent.tool_count == 0 and agent.kind in self.subagents else None
                ),
            )
        )
        _log_provider_response(turn)
        return turn

    async def _run_agent_loop(self, agent: AgentSession) -> AgentLoopResult:
        """Run the model until it answers in words.

        Stopping with no content is not an answer, so the session says what it is waiting
        for and runs on.  Repair rounds bound that, and an empty result after them is
        `_materialize`'s to turn into something the owner can read.
        """
        messages = agent.messages
        while True:
            turn = await self._provider_turn(agent)
            if turn.tool_calls:
                assistant_tool_calls = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments_json},
                    }
                    for call in turn.tool_calls
                ]
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn.content or None,
                        "tool_calls": assistant_tool_calls,
                    }
                )
                if len(turn.tool_calls) > 1 and any(
                    call.name == "route" for call in turn.tool_calls
                ):
                    # A route can suspend the whole chain, and a suspended response cannot
                    # carry results for its siblings: the transcript would resume malformed.
                    for call in turn.tool_calls:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": call.name,
                                "content": json.dumps(
                                    {
                                        "status": ToolResultStatus.ERROR.value,
                                        "code": "route_is_not_shared",
                                        "error": "route must be the only tool call in a response.",
                                        "next": "Send route alone, then use what it hands back.",
                                        "retryable": True,
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    continue
                pending_tools: list[PendingTool] = []
                immediate = agent.immediate
                has_reads = any(call.name in immediate for call in turn.tool_calls)
                has_mutations = any(call.name not in immediate for call in turn.tool_calls)
                for call in turn.tool_calls:
                    agent.tool_count += 1
                    if agent.tool_count > MAX_TOOL_CALLS:
                        raise DomainError("The advisor exceeded the tool-call limit")
                    change = None
                    if call.name == "route":
                        result, suspended = await self._execute_route_tool(agent, call)
                        if suspended is not None:
                            return AgentLoopResult(message="", suspended=suspended)
                    elif call.name == "query_safwa":
                        result = await self.adapters.query(agent, call)
                    elif call.name == "open":
                        result = await self.adapters.open(agent, call)
                    elif call.name == "call_helper":
                        result = await self.adapters.call_helper(agent, call)
                    elif call.name in agent.read_specs:
                        result = await self.adapters.read(agent, call)
                    elif has_reads and has_mutations:
                        result = {
                            "status": ToolResultStatus.ERROR.value,
                            "code": "mixed_read_and_mutation_tools",
                            "error": (
                                "Mutation tools cannot share a response with a read tool or route."
                            ),
                            "next": (
                                "Use the read result, then retry this mutation in the next response."
                            ),
                            "retryable": True,
                        }
                    else:
                        change, result = await self.adapters.mutation(agent, call)
                    pending_tools.append(PendingTool(call=call, result=result, change=change))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
                changes = [tool.change for tool in pending_tools if tool.change is not None]
                if changes:
                    card_creates = [
                        change
                        for change in changes
                        if change.entity == "card" and change.action is ChangeAction.CREATE
                    ]
                    if card_creates and len(card_creates) == len(changes):
                        message = "I prepared the Card proposal for your review."
                    elif card_creates:
                        message = "I prepared Card proposals and other changes for your review."
                    else:
                        message = "I prepared the proposed changes for your approval."
                    return AgentLoopResult(
                        message=message,
                        pending_tools=pending_tools,
                    )
                invalid_mutations = [
                    tool
                    for tool in pending_tools
                    if tool.call.name not in immediate and tool.change is None
                ]
                if invalid_mutations:
                    if agent.repair_rounds >= MAX_REPAIR_ROUNDS:
                        return AgentLoopResult(
                            message=(
                                "I could not prepare the requested change after five repair attempts. "
                                "No unfinished operation was applied."
                            ),
                        )
                    agent.repair_rounds += 1
                continue

            if turn.content:
                return AgentLoopResult(turn.content)
            if agent.repair_rounds >= MAX_REPAIR_ROUNDS:
                return AgentLoopResult("")
            agent.repair_rounds += 1
            # A turn that stops with nothing leaves the owner with nothing.  The empty
            # assistant message is dropped rather than kept: it carries no information and
            # some chat templates reject it.
            messages.append(
                system_note(
                    "You stopped without answering. Write the answer to the owner now, "
                    "in their language, using what the tool results already gave you."
                )
            )

    async def _execute_route_tool(
        self, agent: AgentSession, call: ToolCall
    ) -> tuple[dict[str, Any], AIOutcome | None]:
        """Run the named subagent and hand back its receipt.

        The second value is set only when the subagent opened a screen: it has not
        finished, so this session suspends with it and the owner sees that screen.
        """
        try:
            name = RouteInput.model_validate(json.loads(call.arguments_json or "{}")).name.strip()
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": (
                    validation_error_summary(error)
                    if isinstance(error, ValidationError)
                    else str(error)
                ),
                "hint": f'Send {{"name": "<subagent>"}}. One of: {", ".join(self.subagents)}.',
                "retryable": True,
            }, None
        if name not in self.subagents:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "unknown_subagent",
                "error": f"There is no subagent named {name!r}.",
                "hint": f"Route to one of: {', '.join(self.subagents) or 'none'}.",
                "retryable": True,
            }, None
        # Saved before the subagent runs, because the subagent may suspend and this
        # session then has to come back to a call it has not answered yet.
        agent.awaiting_route = {"call_id": call.id, "subagent": name}
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="route",
                    metadata_json={"tool_call_id": call.id, "subagent": name},
                )
            )
            run = await session.get(AgentRun, agent.run_id)
            if run is not None:
                run.state_json = agent.state()
            await session.commit()
        logger.info("ROUTE -> %s", name)
        outcome, receipt = await self._run_child(name, agent)
        if receipt is None:
            return {}, outcome
        agent.awaiting_route = None
        # The interface owns the Saved/Discarded/Failed lines, so they travel with the
        # session that will write to the chat rather than being left for the model to echo.
        agent.display_result_summaries.extend(str(line) for line in receipt.get("did") or [])
        return receipt, None

    async def describe_proposal(
        self, session: AsyncSession, proposal_id: int
    ) -> ProposalDescription:
        """How one proposal reads to the owner, for whoever is drawing a screen."""
        return await self.review_view.describe(session, proposal_id)

    async def _materialize(
        self,
        agent: AgentSession,
        result: AgentLoopResult,
    ) -> AIOutcome:
        if not result.pending_tools:
            return self._answer(agent, result.message)
        mutation_tools = [tool for tool in result.pending_tools if tool.change is not None]
        preparation_results = {tool.call.id: json_safe(tool.result) for tool in result.pending_tools}
        failed_call_ids = {
            tool.call.id
            for tool in result.pending_tools
            if tool.call.name not in IMMEDIATE_TOOLS and tool.change is None
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
                proposal_details[tool.call.id] = await self.review_view.result_details(
                    session, proposal.id, tool.change
                )
                proposal_displays[tool.call.id] = await self.review_view.display_line(
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
                        or self.review_view.raw_details(tool.change),
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
                    )
                )
                run = await session.get(AgentRun, agent.run_id)
                if run is not None:
                    run.state_json = agent.state()
            await session.commit()
        if not queue:
            if failed_call_ids:
                if agent.repair_rounds >= MAX_REPAIR_ROUNDS:
                    return AIOutcome(
                        AIOutcomeKind.ANSWER,
                        "I could not prepare the requested change after five repair attempts. "
                        "No unfinished operation was applied.",
                    )
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
                agent.repair_rounds += 1
                repaired = await self._run_agent_loop(agent)
                return await self._materialize(agent, repaired)
            return self._answer(agent, result.message)
        return await self._advance_autoapprovals(
            AIOutcome(AIOutcomeKind.PROPOSAL, result.message, proposal_id=queue[0].proposal_id),
            held_run_id=agent.run_id,
        )

    async def _autoapproval_candidate(self, proposal_id: int) -> AutoApprovalCandidate | None:
        """Build the reviewer's request-only view for the active head of one batch."""
        async with self.sessions() as session:
            batch = self.reviews.batch_for_proposal(proposal_id)
            if batch is None:
                return None
            head = batch.state.head
            if head is None or head.proposal_id != proposal_id:
                return None
            change = self.review_view.only_change(proposal_id)
            if change is None:
                return None
            description = await self.describe_proposal(session, proposal_id)
            run = await session.get(AgentRun, batch.run_id)
            request = next(
                (
                    str(item.get("content", ""))
                    for item in reversed((run.state_json or {}).get("dialogue") or [])
                    if item.get("role") == "user"
                ),
                "",
            ) if run is not None else ""
            return AutoApprovalCandidate(
                user_request=request,
                entity=change.entity,
                action=change.action,
                entity_id=change.entity_id,
                values=dict(change.values),
                summary=description.summary,
                fields=tuple(description.fields),
            )

    async def _advance_autoapprovals(
        self, outcome: AIOutcome, *, held_run_id: int | None = None
    ) -> AIOutcome:
        """Auto-save one eligible head; resolving it advances and checks the next head."""
        if (
            self.autoapproval is None
            or outcome.kind is not AIOutcomeKind.PROPOSAL
            or outcome.proposal_id is None
        ):
            return outcome
        candidate = await self._autoapproval_candidate(outcome.proposal_id)
        if candidate is None:
            return outcome
        verdict = await self.autoapproval.review(candidate)
        if not verdict.approved:
            return outcome
        try:
            advanced = await self.resolve_approval(
                outcome.proposal_id,
                decision=BatchDecision.APPROVED,
                result={
                    "approval_source": AUTOAPPROVED,
                    "autoapproval_reason": verdict.reason,
                },
                apply_proposal=True,
                held_run_id=held_run_id,
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
        next_outcome: AIOutcome | None = None
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
                next_outcome = AIOutcome(
                    AIOutcomeKind.PROPOSAL,
                    "Review the next proposed change.",
                    proposal_id=head.proposal_id,
                )
            else:
                # The queue is empty, so the batch has done its whole job.  Closing it and
                # claiming the session in the same commit is what makes a crash here cost
                # nothing: no half-open batch is left to route a later press into, and the
                # session is left plainly interrupted.
                run = await self._claim_session(
                    session, decided.run_id, held_run_id=held_run_id
                )
                if run is None:
                    logger.warning("Session #%s is already resuming", decided.run_id)
                    await session.commit()
                    return None
                run_id = run.id
                repair_exhausted = decided.state.repair_exhausted
                agent, stored_transcript = AgentSession.restore(
                    run, self._tools_for(run.kind), self._read_specs_for(run.kind)
                )
                if not agent.dialogue:
                    agent.dialogue = [
                        {"role": item.role, "content": item.content} for item in dialogue or []
                    ]
                await session.commit()

        if next_outcome is not None:
            return await self._advance_autoapprovals(next_outcome, held_run_id=held_run_id)

        try:
            result_summary = results_summary(tools)
            if result_summary:
                agent.result_summaries.append(result_summary)
            display_summary = results_summary(
                tools, include_preparation_errors=False, for_display=True
            )
            if display_summary:
                agent.display_result_summaries.append(display_summary)
            exhausted = (
                "I could not prepare the remaining requested changes after five repair "
                "attempts. No unfinished operation was applied."
            )
            if repair_exhausted:
                await self._finish_run(run_id, AgentRunStatus.COMPLETED, started)
                outcome = await self._answer_or_deliver(agent, self._answer(agent, exhausted))
                outcome.did.extend(display_summary.splitlines())
                return outcome
            messages = await self.context.for_session(agent.kind, agent.dialogue, agent.prior_receipts)
            agent.prefix_len = len(messages)
            messages.extend(_resumed_transcript(stored_transcript, tools))
            agent.messages = messages
            loop_result = await self._run_agent_loop(agent)
            if loop_result.suspended is not None:
                await self._suspend_for_child(agent)
                await self._finish_run(run_id, AgentRunStatus.AWAITING_APPROVAL, started)
                return loop_result.suspended
            outcome = await self._materialize(agent, loop_result)
            if outcome.kind is AIOutcomeKind.PROPOSAL:
                await self._finish_run(run_id, AgentRunStatus.AWAITING_APPROVAL, started)
                return outcome
            await self._finish_run(run_id, AgentRunStatus.COMPLETED, started)
            outcome = await self._answer_or_deliver(agent, outcome)
            outcome.did.extend(display_summary.splitlines())
            return outcome
        except Exception as error:
            logger.exception("AI continuation failed after the approval queue was resolved")
            await self._finish_run(
                run_id, AgentRunStatus.FAILED, started, type(error).__name__
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
        async with self.sessions() as session:
            interrupted = interrupt_batch(self.reviews, proposal_id, reason=INTERRUPTED)
            if interrupted is None:
                return None
            tools = interrupted.tool_calls
            run = await session.get(AgentRun, interrupted.run_id)
            prior_summaries: list[str] = []
            summary = results_summary(
                tools, include_preparation_errors=False, for_display=True
            )
            if run is not None:
                state = dict(run.state_json or {})
                prior_summaries = list(state.get("display_result_summaries") or [])
                state["transcript"] = [
                    *_resumed_transcript(
                        [dict(item) for item in state.get("transcript") or []], tools
                    ),
                    system_note(REFUSED_AND_WROTE),
                ]
                run.state_json = state
                # Unfinished rather than waiting: no screen is open on it any more.  It
                # stays for a `route` back on this same turn, and the turn that routed to
                # it is what closes it.
                run.status = AgentRunStatus.INTERRUPTED.value
                root = await self._root_run(session, run)
                if root is not None and root.id != run.id:
                    root_state = dict(root.state_json or {})
                    root_state["interruption"] = summary
                    root.state_json = root_state
            await session.commit()
        return compose_display_outcome(
            "", [line for line in (*prior_summaries, summary) if line]
        )

    async def _root_run(self, session: AsyncSession, run: AgentRun) -> AgentRun | None:
        """The session at the top of this chain — the one that answers the owner."""
        current = run
        while current.parent_run_id is not None:
            parent = await session.get(AgentRun, current.parent_run_id)
            if parent is None:
                break
            current = parent
        return current

    async def _resume_interrupted_turn(
        self, dialogue: list[dict[str, Any]]
    ) -> AIOutcome | None:
        """Continue the request the owner wrote over, instead of starting a new one.

        `None` means there was nothing to continue, and the caller starts a fresh turn.
        """
        started = time.monotonic()
        async with self.sessions() as session:
            candidate = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.parent_run_id.is_(None),
                    AgentRun.status == AgentRunStatus.AWAITING_APPROVAL.value,
                    AgentRun.claimed_at.is_(None),
                )
                .order_by(AgentRun.id.desc())
                .limit(1)
            )
            if candidate is None:
                return None
            state = dict(candidate.state_json or {})
            if "interruption" not in state or not state.get("awaiting_route"):
                return None
            run = await self._claim_session(session, candidate.id, held_run_id=None)
            if run is None:
                return None
            summary = str(state.pop("interruption") or "")
            run.state_json = state
            agent, transcript = AgentSession.restore(
                run, self._tools_for(run.kind), self._read_specs_for(run.kind)
            )
            await session.commit()

        run_id = agent.run_id
        waiting = dict(agent.awaiting_route or {})
        agent.awaiting_route = None
        agent.dialogue = dialogue
        try:
            receipt = _route_receipt(
                str(waiting.get("subagent", "")),
                "",
                [summary] if summary else [],
                error=REFUSED_AND_WROTE,
            )
            messages = await self.context.for_session(agent.kind, agent.dialogue, agent.prior_receipts)
            agent.prefix_len = len(messages)
            messages.extend(transcript)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(waiting.get("call_id", "")),
                    "name": "route",
                    "content": json.dumps(receipt, ensure_ascii=False, default=str),
                }
            )
            agent.messages = messages
            result = await self._run_agent_loop(agent)
            if result.suspended is not None:
                await self._suspend_for_child(agent)
                await self._finish_run(run_id, AgentRunStatus.AWAITING_APPROVAL, started)
                return result.suspended
            outcome = await self._materialize(agent, result)
            status = (
                AgentRunStatus.AWAITING_APPROVAL
                if outcome.kind is AIOutcomeKind.PROPOSAL
                else AgentRunStatus.COMPLETED
            )
            await self._finish_run(run_id, status, started)
            if status is AgentRunStatus.COMPLETED:
                await self._close_unfinished_children(run_id)
            return outcome
        except Exception as error:
            logger.exception("The interrupted request could not be resumed")
            await self._finish_run(
                run_id, AgentRunStatus.FAILED, started, type(error).__name__
            )
            await self._close_unfinished_children(run_id)
            raise

    async def _claim_session(
        self, session: AsyncSession, run_id: int, *, held_run_id: int | None
    ) -> AgentRun | None:
        """Take a suspended session for this resume, or report that it is already taken.

        The claim is the whole guard: two resumes of one session would replay the same
        transcript twice, and only one of them could own the answer.  ``held_run_id`` is
        the session the caller is already running inside — an autoapproval resolves the
        screen its own turn just opened, which is that turn continuing, not a second one.
        """
        if held_run_id == run_id:
            return await session.get(AgentRun, run_id)
        claimed = await session.scalar(
            update(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.claimed_at.is_(None))
            .values(claimed_at=utcnow(), status=AgentRunStatus.RUNNING.value)
            .returning(AgentRun.id)
        )
        return None if claimed is None else await session.get(AgentRun, run_id)

    async def _finish_run(
        self,
        run_id: int,
        status: AgentRunStatus,
        started: float,
        error_code: str | None = None,
    ) -> None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run:
                run.status = status.value
                run.duration_ms = int((time.monotonic() - started) * 1000)
                run.error_code = error_code
                # The turn is over either way, so the session is free for the next resume.
                run.claimed_at = None
                await session.commit()
