"""Save, Discard, and the second confirmation a destructive change needs.

A failed decision is recovered here rather than by the dispatcher: only this feature knows
whether the screen is still answerable, and whether a suspended session is waiting on it.
"""

from __future__ import annotations

import html
import logging

from aiogram.types import InlineKeyboardMarkup

from ...adapters.kinds import MessageKind
from ...foundation.errors import DomainError, StaleStateError
from ...telegram import (
    CallbackContext,
    CallbackHandler,
    send_registered,
    token_button,
)
from ..model import BatchDecision
from ..render import proposal_outcome_text
from ..use_cases import approve_proposal
from .answer import continue_agent_approval
from .screens import render_proposal

logger = logging.getLogger(__name__)

# Failing one of these leaves a suspended session waiting, so it is told the change did
# not apply instead of being left to time out.
_APPLYING = frozenset({"proposal_approve", "proposal_delete_confirm"})


async def _on_approve(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        # Which changes need a second confirmation is the owning feature's rule, not this
        # screen's: a Card deletion takes a whole subtree and its historical contribution.
        advisor = context.services.advisor
        review = advisor.reviews.proposal(proposal_id)
        if review is not None and any(
            advisor.proposals.needs_confirmation(change) for change in review.changes
        ):
            confirm = await token_button(
                session,
                context.owner_id,
                "Permanently delete",
                "proposal_delete_confirm",
                {"id": proposal_id},
            )
            await session.commit()
            await send_registered(
                context.message,
                context.services,
                "<b>Final destructive confirmation</b>\nThis permanently removes the "
                "selected tree and its historical contribution.",
                kind=MessageKind.APPROVAL,
                markup=InlineKeyboardMarkup(inline_keyboard=[[confirm]]),
            )
            return
        # Read the description before applying: its field lines diff against committed
        # state, which the apply is about to become.
        description = await context.services.advisor.describe_proposal(session, proposal_id)
        affected = await approve_proposal(
            session, advisor.reviews, advisor.proposals, proposal_id
        )
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        proposal_outcome_text(BatchDecision.APPROVED, description.summary, description.fields),
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


async def _on_delete_confirm(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        description = await context.services.advisor.describe_proposal(session, proposal_id)
        affected = await approve_proposal(
            session,
            context.services.advisor.reviews,
            context.services.advisor.proposals,
            proposal_id,
            allow_destructive=True,
        )
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected, "destructive": True},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        proposal_outcome_text(
            BatchDecision.APPROVED,
            description.summary,
            description.fields,
            notice=f"Permanently deleted {len(affected)} item(s).",
        ),
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


async def _on_reject(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        description = await context.services.advisor.describe_proposal(session, proposal_id)
    context.services.advisor.reviews.end_proposal(proposal_id)
    if await continue_agent_approval(
        context.message,
        context.services,
        proposal_id,
        decision=BatchDecision.DISCARDED,
        result={"message": "The user discarded this proposed change."},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        proposal_outcome_text(BatchDecision.DISCARDED, description.summary, description.fields),
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


async def _restore(context: CallbackContext, *, notice: str, fallback: str) -> None:
    """Redraw a still-pending screen; a review that is gone leaves the words instead."""
    if context.payload.get("id"):
        try:
            await render_proposal(
                context.message, context.services, context.payload["id"], notice=notice
            )
            return
        except DomainError:
            pass
        except Exception:
            logger.exception("Could not restore proposal UI after callback failure")
    await send_registered(context.message, context.services, fallback, kind=MessageKind.ERROR)


async def _resume(context: CallbackContext, error: Exception) -> bool:
    """Let a suspended agent turn observe an approval that could not be applied."""
    if context.action not in _APPLYING:
        return False
    return await continue_agent_approval(
        context.message,
        context.services,
        context.payload["id"],
        decision=BatchDecision.FAILED,
        result={"error": str(error)},
    )


def _recovering(handler: CallbackHandler) -> CallbackHandler:
    async def guarded(context: CallbackContext) -> None:
        try:
            await handler(context)
        except StaleStateError as error:
            # The refusal already ended the review, so the screen is unanswerable by then.
            if not await _resume(context, error):
                await send_registered(
                    context.message,
                    context.services,
                    html.escape(str(error)),
                    kind=MessageKind.ERROR,
                )
        except DomainError as error:
            if not await _resume(context, error):
                await _restore(
                    context, notice=f"⚠️ {error}", fallback=html.escape(str(error))
                )
        except Exception:
            logger.exception("A proposal decision failed: action=%s", context.action)
            await _restore(
                context,
                notice=(
                    "⚠️ This action failed. The proposal is still pending; "
                    "you can retry or discard it."
                ),
                fallback="Safwa could not finish this action. Reopen the screen and try again.",
            )

    return guarded


PROPOSAL_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    name: _recovering(handler)
    for name, handler in {
        "proposal_approve": _on_approve,
        "proposal_delete_confirm": _on_delete_confirm,
        "proposal_reject": _on_reject,
    }.items()
}
