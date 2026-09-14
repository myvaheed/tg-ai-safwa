"""Where a session's record lives: the `agent_runs` table, behind the runtime's store port.

`claim` is the guard against two resumes of one session, so it is one conditional update
rather than a read followed by a write. `claim_within` is that update on a transaction this
store already holds: finding a session to take and taking it are one decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, aliased, mapped_column

from agent_runtime import RunRecord, RunStatus

from ..foundation.clock import utcnow
from ..foundation.models import Base, TimestampMixin, UtcDateTime


class AgentRun(Base, TimestampMixin):
    """One model session, from its first turn to whichever turn ends it.

    A session that stops on an approval screen keeps everything it needs to continue in
    `state_json`, so it resumes from its own row.  `claimed_at` is taken before resuming
    and released afterwards: it is what stops two resumes of the same session.  A session
    suspended on a child it routed to keeps the unanswered call in `state_json` too.
    """

    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(30), default="advisor")
    # The session that routed here.  A finished session hands its receipt back up this
    # link, so a turn ends only when the root session answers.
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), index=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    claimed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    source_message_id: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


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

    async def claim(self, run_id: int) -> RunRecord | None:
        async with self.sessions() as session:
            record = await self.claim_within(session, run_id)
            await session.commit()
            return record

    async def claim_within(self, session: AsyncSession, run_id: int) -> RunRecord | None:
        """Take a suspended session for this resume, or report that it is already taken."""
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
            # Unfinished, and free: an unfinished session that stayed claimed is one nothing
            # could adopt again.
            run.claimed_at = None
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

    async def close_chain(self, run_id: int, state: dict[str, Any]) -> None:
        """Store what a session was left with, and end it with every caller above it.

        One transaction, like `leave_interrupted`: a chain half ended would leave a root
        waiting on a screen that is gone.
        """
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            run.state_json = state
            while run is not None:
                run.status = RunStatus.ABANDONED.value
                run.claimed_at = None
                run = (
                    await session.get(AgentRun, run.parent_run_id)
                    if run.parent_run_id is not None
                    else None
                )
            await session.commit()

    async def close_unfinished_children(self, run_id: int) -> int:
        """End everything left unfinished anywhere below this session.

        The walk is here rather than in the caller: a session ends the whole branch it
        started, so the depth of the chain is this statement's business and nobody else's.
        `parent_run_id` is written once, at creation, and always names an older row, so the
        recursion terminates.
        """
        async with self.sessions() as session:
            branch = (
                select(AgentRun.id)
                .where(AgentRun.parent_run_id == run_id)
                .cte("branch", recursive=True)
            )
            deeper = aliased(AgentRun)
            branch = branch.union_all(
                select(deeper.id).join(branch, deeper.parent_run_id == branch.c.id)
            )
            closed = await session.scalars(
                update(AgentRun)
                .where(
                    AgentRun.id.in_(select(branch.c.id)),
                    AgentRun.status == RunStatus.INTERRUPTED.value,
                )
                .values(status=RunStatus.ABANDONED.value, claimed_at=None)
                .returning(AgentRun.id)
                .execution_options(synchronize_session=False)
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
