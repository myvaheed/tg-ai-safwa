"""A session store that keeps everything in memory.

Enough to run the whole loop, the routed chain and an interruption without a database,
which is what an example or a test of the runtime itself needs.
"""

from __future__ import annotations

from typing import Any

from .model import RunRecord, RunStatus


class InMemorySessionStore:
    """`SessionStore` over a dict. Records survive as long as the object does."""

    def __init__(self) -> None:
        self._records: dict[int, RunRecord] = {}
        self._status: dict[int, RunStatus] = {}
        self._claimed: set[int] = set()
        self._next_id = 1

    async def create(
        self,
        *,
        kind: str,
        parent_run_id: int | None = None,
        source_message_id: int | None = None,
    ) -> RunRecord:
        record = RunRecord(id=self._next_id, kind=kind, parent_run_id=parent_run_id)
        self._next_id += 1
        self._records[record.id] = record
        self._status[record.id] = RunStatus.RUNNING
        # A child is running under a caller that already holds the turn.
        if parent_run_id is not None:
            self._claimed.add(record.id)
        return record

    async def get(self, run_id: int) -> RunRecord | None:
        return self._records.get(run_id)

    async def save_state(self, run_id: int, state: dict[str, Any]) -> None:
        record = self._records.get(run_id)
        if record is not None:
            record.state = state

    async def claim(self, run_id: int, *, held_run_id: int | None = None) -> RunRecord | None:
        if held_run_id == run_id:
            return self._records.get(run_id)
        if run_id in self._claimed:
            return None
        self._claimed.add(run_id)
        self._status[run_id] = RunStatus.RUNNING
        return self._records.get(run_id)

    async def finish(
        self,
        run_id: int,
        *,
        status: RunStatus,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None:
        self._status[run_id] = status
        self._claimed.discard(run_id)

    async def adopt_interrupted_child(
        self, *, kind: str, parent_run_id: int
    ) -> RunRecord | None:
        for run_id in sorted(self._records, reverse=True):
            record = self._records[run_id]
            if (
                record.kind == kind
                and record.parent_run_id == parent_run_id
                and self._status.get(run_id) is RunStatus.INTERRUPTED
                and run_id not in self._claimed
            ):
                return await self.claim(run_id)
        return None

    async def take_interrupted_root(self) -> tuple[RunRecord, str] | None:
        for run_id in sorted(self._records, reverse=True):
            record = self._records[run_id]
            if (
                record.parent_run_id is not None
                or self._status.get(run_id) is not RunStatus.AWAITING_APPROVAL
                or run_id in self._claimed
            ):
                continue
            state = dict(record.state or {})
            if "interruption" not in state or not state.get("awaiting_route"):
                return None
            if await self.claim(run_id) is None:
                return None
            summary = str(state.pop("interruption") or "")
            record.state = state
            return record, summary
        return None

    async def close_unfinished_children(self, run_id: int) -> int:
        closed = 0
        for child_id, record in self._records.items():
            if record.parent_run_id == run_id and self._status.get(child_id) is RunStatus.INTERRUPTED:
                self._status[child_id] = RunStatus.ABANDONED
                self._claimed.discard(child_id)
                closed += 1
        return closed

    def status(self, run_id: int) -> RunStatus | None:
        """What the store last recorded. For a test that wants to look."""
        return self._status.get(run_id)

    def interrupt(self, run_id: int, state: dict[str, Any]) -> None:
        """Leave a session unfinished with the state it should resume from."""
        record = self._records.get(run_id)
        if record is not None:
            record.state = state
        self._status[run_id] = RunStatus.INTERRUPTED
        self._claimed.discard(run_id)
