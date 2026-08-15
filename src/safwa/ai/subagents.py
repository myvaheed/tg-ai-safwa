"""Subagents: a named prompt with its own read tools that reports and never mutates.

The advisor blocks on `call_subagent`, so a subagent is bounded by a wall clock rather
than by a provider-call count — a deadline aborts a stalled read loop, which a call cap
only notices after the call returns.  A subagent's tool set holds reads and one terminal
report; it never carries a mutation tool, and never carries `call_subagent` itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..constants import SUBAGENT_DEADLINE_SECONDS
from ..models import AgentRun, AgentStep
from .mini import Trace

logger = logging.getLogger(__name__)


class Subagent(Protocol):
    """One named reader; `run` returns the tool result the advisor reads."""

    name: str

    async def run(self, request: str, *, trace: Trace) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SubagentOutcome:
    """The report, and the `AgentRun` it was traced under when one was started."""

    result: dict[str, Any]
    run_id: int | None = None


class SubagentRunner:
    """Dispatch a named subagent, trace it as its own `AgentRun`, and time it out."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        subagents: tuple[Subagent, ...],
        *,
        provider_name: str,
        model_name: str,
        deadline_seconds: float = SUBAGENT_DEADLINE_SECONDS,
    ) -> None:
        self.sessions = sessions
        self.subagents = {agent.name: agent for agent in subagents}
        self.provider_name = provider_name
        self.model_name = model_name
        self.deadline_seconds = deadline_seconds

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.subagents)

    async def run(self, name: str, request: str) -> SubagentOutcome:
        agent = self.subagents.get(name)
        if agent is None:
            return SubagentOutcome(
                {
                    "status": "error",
                    "code": "unknown_subagent",
                    "error": f"There is no subagent named {name!r}.",
                    "hint": f"Call one of: {', '.join(self.subagents) or 'none'}.",
                    "retryable": True,
                }
            )
        started = time.monotonic()
        run = AgentRun(
            provider=self.provider_name,
            model=self.model_name,
            status="running",
        )
        async with self.sessions() as session:
            session.add(run)
            await session.commit()
            run_id = run.id
        position = 0

        async def trace(kind: str, payload: dict[str, Any]) -> None:
            nonlocal position
            position += 1
            async with self.sessions() as session:
                session.add(
                    AgentStep(
                        run_id=run_id,
                        position=position,
                        kind=f"subagent_{kind}",
                        metadata_json={"subagent": name, **payload},
                    )
                )
                await session.commit()

        try:
            result = await asyncio.wait_for(
                agent.run(request, trace=trace), timeout=self.deadline_seconds
            )
        except TimeoutError:
            logger.warning("SUBAGENT %s timed out after %.0fs", name, self.deadline_seconds)
            await self._finish(run_id, "failed", started, "timeout")
            return SubagentOutcome(
                {
                    "status": "error",
                    "code": "subagent_timeout",
                    "error": (
                        f"The {name} subagent did not finish within "
                        f"{self.deadline_seconds:.0f} seconds."
                    ),
                    # Retrying costs the owner the same wait again with nothing new to
                    # go on, so the advisor answers without it instead.
                    "retryable": False,
                    "next": "Answer without it and tell the owner it did not finish.",
                },
                run_id,
            )
        # Any failure is reported, never raised: a subagent is one tool call inside the
        # owner's turn, and losing the whole answer to it would cost far more than it did.
        except Exception as error:
            logger.warning("SUBAGENT %s failed: %s", name, error, exc_info=True)
            await self._finish(run_id, "failed", started, type(error).__name__)
            return SubagentOutcome(
                {
                    "status": "error",
                    "code": "subagent_failed",
                    "error": str(error),
                    "retryable": False,
                    "next": "Answer without it and tell the owner it did not finish.",
                },
                run_id,
            )
        await self._finish(run_id, "completed", started)
        logger.info("SUBAGENT %s -> %s", name, result.get("status"))
        return SubagentOutcome(result, run_id)

    async def _finish(
        self, run_id: int, status: str, started: float, error_code: str | None = None
    ) -> None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            run.status = status
            run.error_code = error_code
            run.duration_ms = int((time.monotonic() - started) * 1000)
            await session.commit()
