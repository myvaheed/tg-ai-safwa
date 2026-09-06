from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from llm_gateway import CompletionRequest, CompletionTurn
from safwa.constants import SUMMARY_TRIGGER_TOKENS
from safwa.features.summary.summary import DialogueSummary
from safwa.features.summary.window import SUMMARY_HEADER
from telegram_llm import HistoryEntry
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.turn import TurnManager


class SequenceProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    async def complete(self, _request: CompletionRequest) -> CompletionTurn:
        return CompletionTurn(self.responses.pop(0))

    async def aclose(self) -> None:
        return None


class RecordingProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.requests.append(request)
        return CompletionTurn(self.responses.pop(0))

    async def aclose(self) -> None:
        return None


class SequenceHistory:
    def __init__(self, *snapshots: list[HistoryEntry]) -> None:
        self.snapshots = list(snapshots)
        self.reads: list[dict[str, Any]] = []

    async def recent(self, *_args, **kwargs) -> list[HistoryEntry]:
        self.reads.append(kwargs)
        if len(self.snapshots) == 1:
            return list(self.snapshots[0])
        return list(self.snapshots.pop(0))


def said(message_id: int, text: str, kind: MessageKind = MessageKind.DIALOGUE_USER) -> HistoryEntry:
    return HistoryEntry(
        message_id=message_id,
        sender_id=42,
        role="user",
        text=text,
        created_at=datetime.now(UTC),
        kind=kind.value,
    )


async def test_background_gate_does_not_start_work_while_foreground_is_active() -> None:
    """AG-TURN-024 — tests/brd/tg_agent_shell/agents.feature"""
    guard = TurnManager()
    guard.begin(101)
    called = False

    async def background(_still_current) -> bool:
        nonlocal called
        called = True
        return True

    assert await guard.run_background(background) is None
    assert called is False


async def test_background_gate_invalidates_currentness_after_dialogue_revision_changes() -> None:
    """AG-TURN-024 — tests/brd/tg_agent_shell/agents.feature"""
    guard = TurnManager()

    async def background(still_current) -> bool:
        assert still_current() is True
        guard.cancel()
        return still_current()

    assert await guard.run_background(background) is False


async def test_owner_message_wins_race_with_in_flight_summary() -> None:
    """AG-TURN-024 — tests/brd/tg_agent_shell/agents.feature"""
    first = said(10, "A long enough request")
    changed = said(11, "A newer request")
    summary = DialogueSummary(
        SequenceHistory([first], [first, changed]),  # type: ignore[arg-type]
        SequenceProvider("stale summary"),  # type: ignore[arg-type]
        summary_trigger_tokens=1,
        chars_per_token=1,
    )
    sent: list[str] = []

    async def write(text: str) -> None:
        sent.append(text)

    assert await summary.close_window(42, write) is False
    assert sent == []


async def test_sum_write_002_a_new_summary_is_written_from_the_previous_one() -> None:
    """SUM-WRITE-002 — tests/brd/summary.feature"""
    previous = said(9, "Everything before today.", MessageKind.SUMMARY)
    entry = said(10, "A long enough request")
    provider = RecordingProvider("The rewritten summary")
    summary = DialogueSummary(
        SequenceHistory([previous, entry]),  # type: ignore[arg-type]
        cast(Any, provider),
        summary_trigger_tokens=1,
        chars_per_token=1,
    )
    sent: list[str] = []

    async def write(text: str) -> None:
        sent.append(text)

    assert await summary.close_window(42, write) is True
    request = provider.requests[0].messages[-1]["content"]
    assert request.startswith("Everything before today.")
    assert "A long enough request" in request
    assert sent == [f"{SUMMARY_HEADER}\nThe rewritten summary"]


async def test_sum_write_001_below_the_trigger_only_an_outright_request_writes_one() -> None:
    """SUM-WRITE-001 — tests/brd/summary.feature"""
    provider = RecordingProvider("Forced summary")
    summary = DialogueSummary(
        SequenceHistory([said(10, "Short")]),  # type: ignore[arg-type]
        cast(Any, provider),
        summary_trigger_tokens=SUMMARY_TRIGGER_TOKENS,
    )
    sent: list[str] = []

    async def write(text: str) -> None:
        sent.append(text)

    assert await summary.close_window(42, write) is False
    assert await summary.close_window(42, write, force=True) is True
    assert sent == [f"{SUMMARY_HEADER}\nForced summary"]
