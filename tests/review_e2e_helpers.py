from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from telegram_fakes import (
    QueueTestCallback,
    QueueTestHistory,
    QueueTestMessage,
    spawn_timer,
)

from safwa.bootstrap.modules import (
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_COMMANDS,
    FEATURE_TEXT_INPUTS,
    SCREENS,
)
from telegram_llm import ChatHost
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.model import CallbackToken
from tg_agent_shell.turn import TurnManager

__all__ = [
    "QueueTestCallback",
    "QueueTestHistory",
    "QueueTestMessage",
    "resolve_queued_proposal",
    "review_services",
    "standalone_tag_proposal",
]


async def resolve_queued_proposal(
    e2e_harness, services, message, proposal_id: int, action: str
) -> None:
    async with e2e_harness.sessions() as session:
        token = next(
            candidate
            for candidate in await session.scalars(
                select(CallbackToken).where(
                    CallbackToken.action == action,
                    CallbackToken.consumed_at.is_(None),
                )
            )
            if candidate.payload["id"] == proposal_id
        )
    await callback_token_handler(QueueTestCallback(token.token, message), services)


async def standalone_tag_proposal(e2e_harness, advisor, name: str) -> int:
    """A proposal with no live approval batch, which is the plain receipt path."""
    outcome = await advisor.handle(f"Create a {name} tag")
    assert outcome.proposal_id is not None
    async with e2e_harness.sessions() as session:
        [e2e_harness.reviews.close_batch(batch) for batch in e2e_harness.reviews.open_batches]
        await session.commit()
    return outcome.proposal_id


def review_services(e2e_harness, advisor) -> SimpleNamespace:
    return SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        similarity=None,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
        commands=FEATURE_COMMANDS,
    )
