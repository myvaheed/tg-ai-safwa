"""Starting a session, resuming one, and the chain of sessions a turn routes through.

One turn may run several sessions: a caller routes to another, which may route on. Only the
session with no caller answers the person, so the turn ends when that one answers — or
when something in the chain opens a screen, and every session in it waits.

`_complete` is the shape all of that shares: store what a suspended session needs, stamp
the record, and hand the loop's result to the host to make sense of.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from llm_gateway import LlmProvider, ToolCall

from .loop import run_loop
from .model import (
    AgentLoopResult,
    AgentSession,
    RunRecord,
    RunStatus,
    TurnOutcome,
    route_receipt,
)
from .ports import ContextSource, Materializer, Observer, SessionStore, ToolRunner

logger = logging.getLogger(__name__)


def failure_reason(error: Exception, limit: int = 160) -> str:
    """One short readable clause; the traceback stays in the log."""
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


class AgentManager:
    """Runs sessions, keeps their records straight, and walks the routed chain."""

    def __init__(
        self,
        store: SessionStore,
        provider: LlmProvider,
        tools: ToolRunner,
        context: ContextSource,
        materializer: Materializer,
        *,
        routed_kinds: frozenset[str] = frozenset(),
        max_tool_calls: int,
        max_repair_rounds: int,
        child_deadline_seconds: float,
        receipt_prefixes: tuple[str, ...] = (),
        interrupted_note: str = "",
        observer: Observer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.provider = provider
        self.tools = tools
        self.context = context
        self.materializer = materializer
        self.routed_kinds = routed_kinds
        self.max_tool_calls = max_tool_calls
        self.max_repair_rounds = max_repair_rounds
        self.child_deadline_seconds = child_deadline_seconds
        self.receipt_prefixes = receipt_prefixes
        self.interrupted_note = interrupted_note
        self.observer = observer
        self.clock = clock

    # ---------------------------------------------------------------- running

    async def run(self, agent: AgentSession) -> AgentLoopResult:
        """One session's loop, with this manager's route handler wired into it."""
        return await run_loop(
            agent,
            provider=self.provider,
            tools=self.tools,
            route=self._route,
            routed_kinds=self.routed_kinds,
            max_tool_calls=self.max_tool_calls,
            max_repair_rounds=self.max_repair_rounds,
        )

    def restore(self, record: RunRecord) -> tuple[AgentSession, list[dict[str, Any]]]:
        """A stored session, with the transcript it left behind."""
        return AgentSession.restore(record, self.tools.definition(record.kind))

    async def _complete(
        self,
        agent: AgentSession,
        result: AgentLoopResult,
        started: float,
        *,
        close_children: bool = False,
    ) -> TurnOutcome:
        """Store, stamp and materialize one finished loop run.

        The host may hand back nothing, which means it corrected this turn's tool results
        and the session runs again. That is bounded by the host, not here: it is the same
        session, so its own repair budget is what ends the exchange.
        """
        while True:
            if result.suspended is not None:
                await self.store.save_state(agent.run_id, agent.state())
                await self._finish(agent.run_id, RunStatus.AWAITING_APPROVAL, started)
                return result.suspended
            outcome = await self.materializer.materialize(agent, result)
            if outcome is not None:
                break
            result = await self.run(agent)
        if outcome.waiting:
            await self._finish(agent.run_id, RunStatus.AWAITING_APPROVAL, started)
            return outcome
        await self._finish(agent.run_id, RunStatus.COMPLETED, started)
        if close_children:
            await self._close_unfinished_children(agent.run_id)
        return outcome

    async def _finish(
        self, run_id: int, status: RunStatus, started: float, error_code: str | None = None
    ) -> None:
        await self.store.finish(
            run_id,
            status=status,
            duration_ms=int((self.clock() - started) * 1000),
            error_code=error_code,
        )

    async def _close_unfinished_children(self, run_id: int) -> None:
        """End the sessions this turn routed to and never finished.

        The turn that routed to a subagent is its outer bound.  An interruption leaves it
        unfinished so the same turn can route back into it with a correction; when that
        turn answers, or fails, there is nothing left for it to correct.
        """
        count = await self.store.close_unfinished_children(run_id)
        if count:
            logger.info("Closed %d unfinished subagent session(s) with the turn", count)

    # ----------------------------------------------------------------- a turn

    async def handle(
        self,
        dialogue: list[dict[str, Any]],
        *,
        kind: str = "advisor",
        source_message_id: int | None = None,
    ) -> TurnOutcome:
        """One turn. A request the person wrote over continues instead of starting again."""
        resumed = await self.resume_interrupted(dialogue)
        if resumed is not None:
            return resumed
        return await self.start(dialogue, kind=kind, source_message_id=source_message_id)

    async def start(
        self,
        dialogue: list[dict[str, Any]],
        *,
        kind: str = "advisor",
        source_message_id: int | None = None,
    ) -> TurnOutcome:
        started = self.clock()
        record = await self.store.create(kind=kind, source_message_id=source_message_id)
        try:
            messages = await self.context.messages_for(kind, dialogue)
            agent = AgentSession.start(record.id, self.tools.definition(kind), dialogue=dialogue)
            agent.messages = messages
            agent.prefix_len = len(messages)
            result = await self.run(agent)
            return await self._complete(agent, result, started, close_children=True)
        except Exception as error:
            logger.exception("Session %s failed", kind)
            await self._finish(record.id, RunStatus.FAILED, started, type(error).__name__)
            await self._close_unfinished_children(record.id)
            raise

    async def resume_interrupted(self, dialogue: list[dict[str, Any]]) -> TurnOutcome | None:
        """Continue the request the person wrote over, instead of starting a new one.

        `None` means there was nothing to continue, and the caller starts a fresh turn.
        """
        started = self.clock()
        taken = await self.store.take_interrupted_root()
        if taken is None:
            return None
        record, summary = taken
        agent, transcript = self.restore(record)
        waiting = dict(agent.awaiting_route or {})
        agent.awaiting_route = None
        agent.dialogue = dialogue
        receipt = route_receipt(
            str(waiting.get("subagent", "")),
            "",
            [summary] if summary else [],
            error=self.interrupted_note,
            receipt_prefixes=self.receipt_prefixes,
        )
        try:
            await self._replay(agent, transcript, receipt, str(waiting.get("call_id", "")))
            result = await self.run(agent)
            return await self._complete(agent, result, started, close_children=True)
        except Exception as error:
            logger.exception("The interrupted request could not be resumed")
            await self._finish(agent.run_id, RunStatus.FAILED, started, type(error).__name__)
            await self._close_unfinished_children(agent.run_id)
            raise

    async def continue_session(
        self,
        agent: AgentSession,
        transcript: list[dict[str, Any]],
        started: float,
    ) -> TurnOutcome:
        """Run a restored session on from the transcript it left behind."""
        messages = await self.context.messages_for(
            agent.kind, agent.dialogue, agent.prior_receipts
        )
        agent.prefix_len = len(messages)
        messages.extend(transcript)
        agent.messages = messages
        result = await self.run(agent)
        return await self._complete(agent, result, started)

    async def _replay(
        self,
        agent: AgentSession,
        transcript: list[dict[str, Any]],
        receipt: dict[str, Any],
        call_id: str,
    ) -> None:
        """Rebuild a waiting session's messages and answer the `route` call it left open."""
        messages = await self.context.messages_for(
            agent.kind, agent.dialogue, agent.prior_receipts
        )
        agent.prefix_len = len(messages)
        messages.extend(transcript)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "route",
                "content": json.dumps(receipt, ensure_ascii=False, default=str),
            }
        )
        agent.messages = messages

    # ------------------------------------------------------------- the chain

    async def _route(
        self, agent: AgentSession, call: ToolCall
    ) -> tuple[dict[str, Any], TurnOutcome | None]:
        """Run the named subagent and hand back its receipt.

        The second value is set only when the subagent opened a screen: it has not
        finished, so this session suspends with it and the person sees that screen.
        """
        name, refusal = self.tools.route_target(call)
        if name is None:
            return refusal or {}, None
        # Saved before the subagent runs, because the subagent may suspend and this
        # session then has to come back to a call it has not answered yet.
        agent.awaiting_route = {"call_id": call.id, "subagent": name}
        if self.observer is not None:
            await self.observer.step(
                agent.run_id,
                agent.tool_count,
                "route",
                {"tool_call_id": call.id, "subagent": name},
            )
        await self.store.save_state(agent.run_id, agent.state())
        logger.info("ROUTE -> %s", name)
        outcome, receipt = await self._run_child(name, agent)
        if receipt is None:
            return {}, outcome
        agent.awaiting_route = None
        # The interface owns the result receipts, so they travel with the session that will
        # write to the chat rather than being left for the model to echo.
        agent.display_result_summaries.extend(str(line) for line in receipt.get("did") or [])
        return receipt, None

    async def _run_child(
        self, name: str, parent: AgentSession
    ) -> tuple[TurnOutcome, dict[str, Any] | None]:
        """Run the named subagent to its own end, resuming its session if it has one.

        A saved session is picked up as it stands, so a correction to something the person
        just refused is answered by the session that wrote it, not by a rewrite.

        The receipt is ``None`` when the subagent opened a screen: it has not finished, and
        the outcome is what the person sees while the whole chain waits for them.
        """
        started = self.clock()
        record = await self.store.adopt_interrupted_child(kind=name, parent_run_id=parent.run_id)
        if record is None:
            record = await self.store.create(kind=name, parent_run_id=parent.run_id)
            agent = AgentSession.start(
                record.id, self.tools.definition(name), parent_run_id=parent.run_id
            )
            transcript: list[dict[str, Any]] = []
        else:
            agent, transcript = self.restore(record)
        agent.dialogue = parent.dialogue
        agent.prior_receipts = list(parent.display_result_summaries)
        try:
            messages = await self.context.messages_for(name, agent.dialogue, agent.prior_receipts)
            agent.prefix_len = len(messages)
            messages.extend(transcript)
            agent.messages = messages
            # The deadline bounds one active stretch of the session, never a suspension:
            # a session waiting on the person is not a session that is taking too long.
            result = await asyncio.wait_for(
                self.run(agent), timeout=self.child_deadline_seconds
            )
            outcome = await self._complete(agent, result, started)
            if outcome.waiting:
                return outcome, None
            # The materialized outcome, not the raw loop result: a repair round answers again.
            return outcome, route_receipt(
                name,
                outcome.message,
                agent.display_result_summaries,
                did=outcome.did,
                receipt_prefixes=self.receipt_prefixes,
            )
        except TimeoutError:
            logger.warning(
                "SUBAGENT %s timed out after %.0fs", name, self.child_deadline_seconds
            )
            await self._finish(agent.run_id, RunStatus.FAILED, started, "timeout")
            return TurnOutcome(message=""), route_receipt(
                name,
                "",
                [],
                error=(
                    f"{name} did not finish within {self.child_deadline_seconds:.0f} seconds."
                ),
                receipt_prefixes=self.receipt_prefixes,
            )
        except Exception as error:
            logger.exception("Routed subagent %s failed", name)
            await self._finish(agent.run_id, RunStatus.FAILED, started, type(error).__name__)
            return TurnOutcome(message=""), route_receipt(
                name,
                "",
                [],
                error=failure_reason(error),
                receipt_prefixes=self.receipt_prefixes,
            )

    async def deliver_to_parent(
        self, child: AgentSession, receipt: dict[str, Any]
    ) -> TurnOutcome | None:
        """Answer the `route` call that started this session, and run its caller on.

        Returns ``None`` when there is no caller — the session was the root of its turn.
        The walk covers the whole chain, so the turn ends only when a session with no
        parent answers.
        """
        agent = child
        while agent.parent_run_id is not None:
            started = self.clock()
            record = await self.store.claim(agent.parent_run_id)
            if record is None:
                logger.warning("Session #%s is already resuming", agent.parent_run_id)
                return None
            parent, transcript = self.restore(record)
            waiting = dict(parent.awaiting_route or {})
            parent.awaiting_route = None
            parent.display_result_summaries.extend(
                str(line) for line in receipt.get("did") or []
            )
            await self._replay(parent, transcript, receipt, str(waiting.get("call_id", "")))
            result = await self.run(parent)
            outcome = await self._complete(
                parent, result, started, close_children=parent.parent_run_id is None
            )
            if result.suspended is not None or outcome.waiting:
                return outcome
            if parent.parent_run_id is None:
                return outcome
            receipt = route_receipt(
                parent.kind,
                outcome.message,
                parent.display_result_summaries,
                receipt_prefixes=self.receipt_prefixes,
            )
            agent = parent
        return None
