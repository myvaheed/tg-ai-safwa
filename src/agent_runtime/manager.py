"""Starting a session, stopping one on a person, resuming it, and the chain it routes through.

One turn may run several sessions: a caller routes to another, which may route on. Only the
session with no caller answers the person, so the turn ends when that one answers — or
when something in the chain opens a screen, and every session in it waits.

Four things happen to a session that stops on a person, and all four are here. It is
checkpointed and handed back an `InteractionRef` (`_suspend`); it is answered and runs on
(`resume`); the person writes instead of deciding, and it is left for the session that
routed to it to come back to — or ended, when nothing routed to it (`interrupt`); or nobody
answers it in time, and it is ended with everything that routed to it (`close`). A host
that had to assemble any of those out of parts would be reimplementing the package.

`_complete` is the shape they share: store what a suspended session needs, stamp the record,
and hand the loop's result to the host to make sense of.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine, Mapping
from typing import Any
from uuid import uuid4

from llm_gateway import LlmProvider, ToolCall

from .context import system_note
from .loop import run_loop
from .model import (
    AgentLoopResult,
    AgentSession,
    InteractionRef,
    Resumption,
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

    async def _run_to_outcome(self, agent: AgentSession, started: float) -> TurnOutcome:
        """One stretch of a session: its loop, and whatever the host makes of the result."""
        return await self._complete(agent, await self.run(agent), started)

    async def _bounded(
        self, agent: AgentSession, work: Coroutine[Any, Any, TurnOutcome]
    ) -> TurnOutcome:
        """Bound a subagent's active stretch by the clock. A session with no caller is not.

        Every stretch, not only the first: a session picked up after a screen blocks the
        turn that routed to it exactly as its first one did. The wait itself is outside the
        bound — a person taking an hour to decide is not a session taking too long.
        """
        if agent.parent_run_id is None:
            return await work
        return await asyncio.wait_for(work, timeout=self.child_deadline_seconds)

    def _timed_out(self, name: str) -> dict[str, Any]:
        """The receipt for a subagent the clock stopped."""
        logger.warning("SUBAGENT %s timed out after %.0fs", name, self.child_deadline_seconds)
        return self._receipt(
            name,
            "",
            [],
            error=f"{name} did not finish within {self.child_deadline_seconds:.0f} seconds.",
        )

    def _restore(self, record: RunRecord) -> tuple[AgentSession, list[dict[str, Any]]]:
        """A stored session, with the transcript it left behind."""
        return AgentSession.restore(record, self.tools.definition(record.kind))

    def _receipt(
        self,
        name: str,
        message: str,
        summaries: list[str],
        *,
        shown: list[str] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """One receipt, in this host's wording. Every hand-back in the chain uses it, and
        carries the blocks to show that the session gathered."""
        return route_receipt(
            name,
            message,
            summaries,
            error=error,
            receipt_prefixes=self.receipt_prefixes,
            shown=shown,
        )

    async def _complete(
        self, agent: AgentSession, result: AgentLoopResult, started: float
    ) -> TurnOutcome:
        """Store, stamp and materialize one finished loop run.

        The host may hand back nothing, which means it corrected this turn's tool results,
        or added to the session's messages, and the session runs again. That is bounded by
        the host, not here: it is the same session, so its own budget is what ends the
        exchange.

        A session that stops on a person is checkpointed here and nowhere else, before the
        record says it is waiting. Whatever the host does about that wait — draw a screen,
        answer it itself — happens after the turn has ended, on a session that is stored
        and released, never on one that is still running.
        """
        while True:
            if result.suspended is not None:
                return await self._suspend(agent, result.suspended, started)
            outcome = await self.materializer.materialize(agent, result)
            if outcome is not None:
                break
            result = await self.run(agent)
        if outcome.waiting:
            return await self._suspend(agent, outcome, started)
        await self._finish(agent.run_id, RunStatus.COMPLETED, started)
        await self._close_unfinished_children(agent.run_id)
        return outcome

    async def _suspend(
        self, agent: AgentSession, outcome: TurnOutcome, started: float
    ) -> TurnOutcome:
        """Store what this session needs to continue, then say it is waiting on a person.

        Only the session the person was actually stopped by mints a reference: the callers
        above it in the chain are checkpointed too, but they are resumed by the receipt
        coming back up, never by anyone naming them.
        """
        if outcome.ref is None:
            agent.interaction_token = uuid4().hex
            outcome.ref = InteractionRef(agent.run_id, agent.interaction_token)
        await self.store.save_state(agent.run_id, agent.state())
        await self._finish(agent.run_id, RunStatus.AWAITING_APPROVAL, started)
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

    async def _fail(self, run_id: int, started: float, error: Exception | str) -> None:
        """End a session that could not finish, and whatever it left unfinished."""
        await self._finish(
            run_id,
            RunStatus.FAILED,
            started,
            error if isinstance(error, str) else type(error).__name__,
        )
        await self._close_unfinished_children(run_id)

    async def _close_unfinished_children(self, run_id: int) -> None:
        """End everything this session started and never finished.

        The session that routed to a subagent is its outer bound.  An interruption leaves it
        unfinished so the same session can route back into it with a correction; once that
        session has answered, or failed, there is nothing left for it to correct.  The store
        ends the whole branch, so how deep the chain went is not this method's business.
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
        host_state: Mapping[str, Any] | None = None,
    ) -> TurnOutcome:
        """One turn. A request the person wrote over continues instead of starting again,
        with the host state it started with."""
        resumed = await self.resume_interrupted(dialogue)
        if resumed is not None:
            return resumed
        return await self.start(
            dialogue, kind=kind, source_message_id=source_message_id, host_state=host_state
        )

    async def start(
        self,
        dialogue: list[dict[str, Any]],
        *,
        kind: str = "advisor",
        source_message_id: int | None = None,
        host_state: Mapping[str, Any] | None = None,
    ) -> TurnOutcome:
        started = self.clock()
        record = await self.store.create(kind=kind, source_message_id=source_message_id)
        try:
            messages = await self.context.messages_for(kind, dialogue)
            agent = AgentSession.start(record.id, self.tools.definition(kind), dialogue=dialogue)
            agent.host_state.update(host_state or {})
            agent.messages = messages
            agent.prefix_len = len(messages)
            result = await self.run(agent)
            return await self._complete(agent, result, started)
        except Exception as error:
            logger.exception("Session %s failed", kind)
            await self._fail(record.id, started, error)
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
        agent, transcript = self._restore(record)
        waiting = dict(agent.awaiting_route or {})
        agent.awaiting_route = None
        agent.dialogue = dialogue
        receipt = self._receipt(
            str(waiting.get("subagent", "")),
            "",
            [summary] if summary else [],
            error=self.interrupted_note,
        )
        try:
            await self._replay(agent, transcript, receipt, str(waiting.get("call_id", "")))
            result = await self.run(agent)
            return await self._complete(agent, result, started)
        except Exception as error:
            logger.exception("The interrupted request could not be resumed")
            await self._fail(agent.run_id, started, error)
            raise

    # ----------------------------------------------------- answering a person

    async def resume(self, ref: InteractionRef, value: Resumption) -> TurnOutcome | None:
        """Answer what a suspended session stopped on, and run the chain on from there.

        ``None`` means the reference resumed nothing: the session is already being resumed,
        or it has left the suspension this reference names. The person still has to be told
        something, and the host is what knows what — it has the decision they just made.
        """
        started = self.clock()
        # Read before claiming: a reference to a suspension the session has already left
        # must change nothing at all, and a claim would have to be put back.
        stored = await self.store.get(ref.run_id)
        if stored is None or stored.state.get("interaction_token") != ref.token:
            logger.warning("Session #%s is not waiting on that decision", ref.run_id)
            return None
        record = await self.store.claim(ref.run_id)
        if record is None:
            logger.warning("Session #%s is already resuming", ref.run_id)
            return None
        agent, transcript = self._restore(record)
        agent.interaction_token = None
        # Taking the reference is durable, so a second answer to the same screen resumes
        # nothing even after this turn has ended and released its claim.
        await self.store.save_state(ref.run_id, {**record.state, "interaction_token": None})
        agent.result_summaries.extend(value.notes)
        agent.display_result_summaries.extend(value.display_notes)
        try:
            # The host already knows what is left to say when it hands an answer over, so
            # the session is ended with those words rather than being asked for its own.
            work = (
                self._complete(agent, AgentLoopResult(value.answer), started)
                if value.answer is not None
                else self._continue(
                    agent, _resumed_transcript(transcript, value.results), started
                )
            )
            outcome = await self._bounded(agent, work)
            if outcome.waiting:
                return outcome
            return await self._hand_up(agent, outcome)
        except TimeoutError:
            # A subagent the clock stopped still owes its caller a receipt: losing the turn
            # would leave the owner with a saved change and no answer about it.
            await self._fail(agent.run_id, started, "timeout")
            if agent.parent_run_id is None:
                raise
            return await self._deliver_to_parent(agent, self._timed_out(agent.kind))
        except Exception as error:
            logger.exception("Session %s could not be resumed", agent.kind)
            await self._fail(agent.run_id, started, error)
            raise

    async def interrupt(
        self, ref: InteractionRef, results: Mapping[str, Any], *, summary: str
    ) -> list[str] | None:
        """End this session's wait because the person wrote instead of deciding.

        A routed session is left **unfinished**: no screen is open on it any more, but it
        keeps its own plan, so a route back on the same turn reaches the session that wrote
        the refused proposal, and the session that routed there is what closes it.

        A session with no caller has nothing that can route back into it — the person is its
        caller, and their words are its next request — so it is **ended** instead. Left
        unfinished it would be a record no turn could ever reach.

        Either way its own calls are answered in its transcript first, so what it stopped on
        is recorded rather than left half-said. Returns what the session had already told the
        person before it stopped, so the host can say the whole of what happened. ``None``
        means the reference names nothing.
        """
        started = self.clock()
        record = await self.store.get(ref.run_id)
        if record is None or record.state.get("interaction_token") != ref.token:
            return None
        state, prior = _answered(record, results)
        state["transcript"].append(system_note(self.interrupted_note))
        if record.parent_run_id is None:
            await self.store.save_state(ref.run_id, state)
            await self._finish(ref.run_id, RunStatus.ABANDONED, started)
            return prior
        await self.store.leave_interrupted(ref.run_id, state, summary)
        return prior

    async def close(self, ref: InteractionRef, results: Mapping[str, Any]) -> list[str] | None:
        """End this session's wait because nobody answered it in time.

        Nothing comes back to it: the session and every caller above it are ended together,
        so the person's next words start a new request rather than continuing this one. Its
        own calls are answered in its transcript first, as `interrupt` answers them. Returns
        what the session had already told the person before it stopped; ``None`` means the
        reference names nothing.
        """
        record = await self.store.get(ref.run_id)
        if record is None or record.state.get("interaction_token") != ref.token:
            return None
        state, prior = _answered(record, results)
        await self.store.close_chain(ref.run_id, state)
        return prior

    async def _continue(
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

    async def _hand_up(self, agent: AgentSession, outcome: TurnOutcome) -> TurnOutcome | None:
        """Hand a finished session's receipt to whoever routed to it, and run them on.

        A session with no caller is the end of its turn and answers as it is. ``None`` means
        the caller could not be taken because something else is already resuming it — that
        flow is what will answer, so this one says nothing over the top of it. The words
        this session wrote were for its caller in any case: only the session with no caller
        speaks to the person.
        """
        if agent.parent_run_id is None:
            return outcome
        receipt = self._receipt(
            agent.kind, outcome.message, agent.display_result_summaries, shown=agent.shown_blocks
        )
        return await self._deliver_to_parent(agent, receipt)

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
        agent.shown_blocks.extend(str(block) for block in receipt.get("shown") or [])
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
            agent, transcript = self._restore(record)
        agent.dialogue = parent.dialogue
        agent.prior_receipts = list(parent.display_result_summaries)
        try:
            messages = await self.context.messages_for(name, agent.dialogue, agent.prior_receipts)
            agent.prefix_len = len(messages)
            messages.extend(transcript)
            agent.messages = messages
            outcome = await self._bounded(agent, self._run_to_outcome(agent, started))
            if outcome.waiting:
                return outcome, None
            # The materialized outcome, not the raw loop result: a repair round answers again.
            return outcome, self._receipt(
                name, outcome.message, agent.display_result_summaries, shown=agent.shown_blocks
            )
        except TimeoutError:
            await self._fail(agent.run_id, started, "timeout")
            return TurnOutcome(message=""), self._timed_out(name)
        except Exception as error:
            logger.exception("Routed subagent %s failed", name)
            await self._fail(agent.run_id, started, error)
            return TurnOutcome(message=""), self._receipt(
                name, "", [], error=failure_reason(error)
            )

    async def _deliver_to_parent(
        self, child: AgentSession, receipt: dict[str, Any]
    ) -> TurnOutcome | None:
        """Answer the `route` call that started this session, and run its caller on.

        The walk covers the whole chain, so the turn ends only when a session with no
        parent answers. ``None`` means a caller could not be taken because something else
        is already resuming it.
        """
        agent = child
        while agent.parent_run_id is not None:
            started = self.clock()
            record = await self.store.claim(agent.parent_run_id)
            if record is None:
                logger.warning("Session #%s is already resuming", agent.parent_run_id)
                return None
            parent, transcript = self._restore(record)
            waiting = dict(parent.awaiting_route or {})
            parent.awaiting_route = None
            parent.display_result_summaries.extend(
                str(line) for line in receipt.get("did") or []
            )
            parent.shown_blocks.extend(str(block) for block in receipt.get("shown") or [])
            await self._replay(parent, transcript, receipt, str(waiting.get("call_id", "")))
            result = await self.run(parent)
            outcome = await self._complete(parent, result, started)
            if outcome.waiting or parent.parent_run_id is None:
                return outcome
            receipt = self._receipt(
                parent.kind,
                outcome.message,
                parent.display_result_summaries,
                shown=parent.shown_blocks,
            )
            agent = parent
        return None


def _answered(
    record: RunRecord, results: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """A waiting session's state with its open calls answered and its reference spent,
    and what it had already told the person before it stopped."""
    state = dict(record.state)
    state["interaction_token"] = None
    prior = [str(line) for line in state.get("display_result_summaries") or []]
    state["transcript"] = _resumed_transcript(
        [dict(item) for item in state.get("transcript") or []], results
    )
    return state, prior


def _resumed_transcript(
    transcript: list[dict[str, Any]],
    results: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Replay this request's own assistant/tool exchanges with the decisions filled in.

    No separate progress digest is injected here: every step of the request is present as
    its own call and result, each carrying its status and what to do next.  Restating them
    in an assistant message would duplicate the request once per approval.
    """
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
        if tool_call_id in results:
            message["content"] = json.dumps(
                results[tool_call_id], ensure_ascii=False, default=str
            )
    return replayed
