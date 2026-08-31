from __future__ import annotations

import html
import logging
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import InlineKeyboardMarkup, Message

from telegram_llm import markdown_to_telegram_html

from ..ai.outcome import AIOutcome
from ..domain import DomainError
from ..enums import MessageKind
from ..features.proposals.model import DECISION_RECEIPTS, BatchDecision
from ..foundation.errors import failure_reason
from ._core import Services
from ._messaging import edit_registered_message, send_prose, send_registered, token_button
from ._presentation import proposal_change_summary
from .screens import open_citation, render_citations

logger = logging.getLogger(__name__)


async def render_proposal(
    message: Message,
    services: Services,
    proposal_id: int,
    *,
    replace_message_id: int | None = None,
    notice: str | None = None,
    event_id: str | None = None,
) -> None:
    proposal = services.advisor.reviews.proposal(proposal_id)
    if proposal is None:
        raise DomainError("Proposal is no longer pending")
    async with services.sessions() as session:
        changes = proposal.changes
        text_parts = ["<b>Review proposal</b>"]
        if notice:
            text_parts.append(html.escape(notice))
        text_parts.append(html.escape(proposal.message))
        # One change of a kind whose feature draws it gets that screen; anything else is
        # the plain list, which needs to know nothing about the entities in it.
        screen = None
        if len(changes) == 1:
            presenter = services.advisor.proposals.presenter(changes[0].entity)
            if presenter is not None:
                screen = await presenter.screen(session, changes[0])
        if screen is not None:
            text_parts[0] = f"<b>{screen.mode} {screen.item} · AI proposal</b>"
            text_parts.extend(screen.blocks)
            if screen.diffs:
                text_parts.append("<b>Proposed changes</b>\n" + "\n".join(screen.diffs))
        else:
            text_parts.append(
                "\n".join(f"• {html.escape(proposal_change_summary(change))}" for change in changes)
            )
        # Every proposal screen is read-only: exactly Save and Discard, never a field control.
        rows = [
            [
                await token_button(
                    session, services.owner_id, "✅ Save", "proposal_approve", {"id": proposal.id}
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "🗑 Discard",
                    "proposal_reject",
                    {"id": proposal.id},
                ),
            ]
        ]
        await session.commit()
    text = "\n\n".join(part for part in text_parts if part)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.APPROVAL,
            markup=markup,
            related_id=proposal_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.APPROVAL,
            markup=markup,
            related_id=proposal_id,
            event_id=event_id,
        )


async def render_ai_outcome(
    message: Message,
    services: Services,
    outcome: AIOutcome,
    *,
    kind: MessageKind = MessageKind.DIALOGUE_ASSISTANT,
    event_id: str | None = None,
) -> None:
    """Render one agent state; suspended approval batches expose only their head item."""
    if outcome.proposal_id is not None:
        try:
            await render_proposal(message, services, outcome.proposal_id, event_id=event_id)
        except Exception:
            # A review is committed before its screen is drawn. One that never reached the
            # chat is unanswerable and holds the Cue gate shut for the life of the process,
            # so it ends here; saying what failed stays the caller's.
            try:
                await services.advisor.cancel_approval_for_proposal(outcome.proposal_id)
            except Exception:
                logger.exception("Could not end a review whose screen failed to send")
            raise
        return
    async with services.sessions() as session:
        text = await render_citations(
            session, services, markdown_to_telegram_html(outcome.message)
        )
    await send_prose(message, services, text, kind=kind, event_id=event_id)
    if outcome.open_item:
        await open_citation(message, services, outcome.open_item)


async def continue_agent_approval(
    message: Message,
    services: Services,
    proposal_id: int,
    *,
    decision: BatchDecision,
    result: dict[str, Any],
) -> bool:
    """Advance an open approval queue, resuming the model only after its last item."""
    if getattr(services, "advisor", None) is None or getattr(services, "history", None) is None:
        return False
    if not services.advisor.has_pending_approval(proposal_id):
        return False
    resolved_text = f"{DECISION_RECEIPTS[decision]}."

    await send_registered(
        message,
        services,
        f"{resolved_text} Safwa is continuing…",
        kind=MessageKind.RECEIPT,
    )
    try:
        await services.guard.acquire(message.message_id)
    except Exception:
        logger.exception(
            "Could not acquire continuation lease after %s #%s", decision, proposal_id
        )
        await send_registered(
            message,
            services,
            f"{resolved_text}\n"
            "⚠️ The change is resolved, but the advisor follow-up was deferred. "
            "You can continue with a new message.",
            kind=MessageKind.DIALOGUE_ASSISTANT,
        )
        return True
    try:
        try:
            await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            outcome = await services.advisor.resolve_approval(
                proposal_id,
                decision=decision,
                result=result,
            )
        except Exception as error:
            logger.exception(
                "AI continuation failed after %s #%s", decision, proposal_id
            )
            await send_registered(
                message,
                services,
                f"{resolved_text}\n"
                "⚠️ The change is resolved, but Safwa could not generate its follow-up "
                f"({html.escape(failure_reason(error))}). "
                "You can continue with a new message.",
                kind=MessageKind.DIALOGUE_ASSISTANT,
            )
            return True
        if outcome is None:
            return False
        await render_ai_outcome(message, services, outcome)
        return True
    finally:
        services.guard.release(message.message_id)
