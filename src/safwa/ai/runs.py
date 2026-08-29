"""Where a session's record lives: the `agent_runs` table, behind the runtime's store port.

`claim` is the whole guard against two resumes of one session, so it is one conditional
update rather than a read followed by a write. `claim_within` is the same claim on a
transaction the caller already owns — an approval closes its batch and claims the session
it belongs to together, so a crash between the two cannot leave a half-open batch.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime import RunRecord, RunStatus

from ..foundation.clock import utcnow
from ..models import AgentRun, AgentStep


def _record(run: AgentRun) -> RunRecord:
    return RunRecord(
        id=run.id,
        kind=run.kind,
        parent_run_id=run.parent_run_id,
        state=dict(run.state_json or {}),
    )


class AgentRunStore:
    """The runtime's `SessionStore` over `agent_runs`."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        provider_name: str,
        model_name: str,
    ) -> None:
        self.sessions = sessions
        self.provider_name = provider_name
        self.model_name = model_name

    async def create(
        self,
        *,
        kind: str,
        parent_run_id: int | None = None,
        source_message_id: int | None = None,
    ) -> RunRecord:
        run = AgentRun(
            kind=kind,
            provider=self.provider_name,
            model=self.model_name,
            status=RunStatus.RUNNING.value,
            source_message_id=source_message_id,
            parent_run_id=parent_run_id,
            # A child runs inside a caller that already holds the turn, so it is claimed
            # from the moment it exists; a root is claimed only when something resumes it.
            claimed_at=utcnow() if parent_run_id is not None else None,
        )
        async with self.sessions() as session:
            session.add(run)
            await session.commit()
            return _record(run)

    async def get(self, run_id: int) -> RunRecord | None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            return None if run is None else _record(run)

    async def save_state(self, run_id: int, state: dict[str, Any]) -> None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run is not None:
                run.state_json = state
            await session.commit()

    async def claim(self, run_id: int, *, held_run_id: int | None = None) -> RunRecord | None:
        async with self.sessions() as session:
            record = await self.claim_within(session, run_id, held_run_id=held_run_id)
            await session.commit()
            return record

    async def claim_within(
        self, session: AsyncSession, run_id: int, *, held_run_id: int | None = None
    ) -> RunRecord | None:
        """Take a suspended session for this resume, or report that it is already taken.

        ``held_run_id`` is the session the caller is already running inside — an
        autoapproval resolves the screen its own turn just opened, which is that turn
        continuing, not a second one.
        """
        if held_run_id == run_id:
            run = await session.get(AgentRun, run_id)
            return None if run is None else _record(run)
        claimed = await session.scalar(
            update(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.claimed_at.is_(None))
            .values(claimed_at=utcnow(), status=RunStatus.RUNNING.value)
            .returning(AgentRun.id)
        )
        if claimed is None:
            return None
        run = await session.get(AgentRun, run_id)
        return None if run is None else _record(run)

    async def finish(
        self,
        run_id: int,
        *,
        status: RunStatus,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run:
                run.status = status.value
                run.duration_ms = duration_ms
                run.error_code = error_code
                # The turn is over either way, so the session is free for the next resume.
                run.claimed_at = None
                await session.commit()

    async def adopt_interrupted_child(
        self, *, kind: str, parent_run_id: int
    ) -> RunRecord | None:
        """Claim this turn's own unfinished session of that subagent, if it left one."""
        async with self.sessions() as session:
            run_id = await session.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.kind == kind,
                    AgentRun.parent_run_id == parent_run_id,
                    AgentRun.status == RunStatus.INTERRUPTED.value,
                    AgentRun.claimed_at.is_(None),
                )
                .order_by(AgentRun.id.desc())
                .limit(1)
            )
            if run_id is None:
                return None
            record = await self.claim_within(session, int(run_id))
            await session.commit()
            return record

    async def take_interrupted_root(self) -> tuple[RunRecord, str] | None:
        """The root session the owner wrote over, claimed, with what it had already done."""
        async with self.sessions() as session:
            candidate = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.parent_run_id.is_(None),
                    AgentRun.status == RunStatus.AWAITING_APPROVAL.value,
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
            if await self.claim_within(session, candidate.id) is None:
                return None
            summary = str(state.pop("interruption") or "")
            candidate.state_json = state
            record = _record(candidate)
            await session.commit()
            return record, summary

    async def leave_interrupted(
        self, run_id: int, state: dict[str, Any], summary: str
    ) -> None:
        """Store what a refused session knows, mark it unfinished, and tell its chain's root.

        One transaction: the root's note is what a later turn looks for, and a crash
        between the two writes would leave a session nothing can find its way back into.
        """
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            run.state_json = state
            run.status = RunStatus.INTERRUPTED.value
            root = run
            while root.parent_run_id is not None:
                parent = await session.get(AgentRun, root.parent_run_id)
                if parent is None:
                    break
                root = parent
            if root.id != run.id:
                root_state = dict(root.state_json or {})
                root_state["interruption"] = summary
                root.state_json = root_state
            await session.commit()

    async def close_unfinished_children(self, run_id: int) -> int:
        async with self.sessions() as session:
            closed = await session.scalars(
                update(AgentRun)
                .where(
                    AgentRun.parent_run_id == run_id,
                    AgentRun.status == RunStatus.INTERRUPTED.value,
                )
                .values(status=RunStatus.ABANDONED.value, claimed_at=None)
                .returning(AgentRun.id)
            )
            count = len(list(closed))
            await session.commit()
            return count


class AgentStepTrail:
    """The runtime's `Observer` over `agent_steps`: what a session did, in order."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def step(
        self, run_id: int, position: int, kind: str, metadata: dict[str, Any]
    ) -> None:
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=run_id, position=position, kind=kind, metadata_json=metadata
                )
            )
            await session.commit()
