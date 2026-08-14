from __future__ import annotations

import html
import logging
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.service import AIOutcome, failure_reason
from ..domain import (
    CARD_REFERENCE_SPECS,
    DomainError,
    ReferenceSpec,
    card_progress,
    resolve_references,
)
from ..enums import (
    CHECK_ANSWER_ACTIONS,
    CHECK_OUTCOME_LABELS,
    CardKind,
    CardStage,
    MessageKind,
)
from ..models import (
    Card,
    CardCategory,
    CardEnergyType,
    ChangeProposal,
    Check,
    ProposalChange,
    Tag,
    Value,
)
from ._core import Services
from ._messaging import edit_registered_message, send_registered, token_button
from ._presentation import (
    card_overview_text,
    category_expression,
    energy_expression,
    proposal_change_summary,
)
from .screens import render_citations

logger = logging.getLogger(__name__)

# Every Card relationship diffs and renders through its spec, so a new one shows up here
# without a second table to update.
_REFERENCE_BY_PLURAL = {spec.plural_key: spec for spec in CARD_REFERENCE_SPECS}


async def _proposal_item_state(
    session: AsyncSession, change: ProposalChange
) -> tuple[dict[str, Any], dict[str, Any]]:
    current: dict[str, Any] = {}
    archived = False
    if change.entity == "card" and change.entity_id:
        card = await session.get(Card, change.entity_id)
        if card is not None:
            archived = card.archived_at is not None
            current = {
                "id": card.id,
                "kind": card.kind,
                "title": card.title,
                "note": card.note,
                "stage": card.effective_stage,
                "priority": card.priority,
                "hard_time": card.hard_time,
                "blocked": card.blocked,
                "blocked_description": card.blocked_description,
                "effort_points": card.effort_points,
                "repeatable": card.repeatable,
                "parent_id": card.parent_id,
                "categories": sorted(
                    await session.scalars(
                        select(CardCategory.category).where(CardCategory.card_id == card.id)
                    )
                ),
                "energy_types": sorted(
                    await session.scalars(
                        select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
                    )
                ),
            }
            for spec in CARD_REFERENCE_SPECS:
                current[spec.plural_key] = sorted(
                    await session.scalars(
                        select(spec.link_column).where(spec.link_model.card_id == card.id)
                    )
                )
    elif change.entity == "tag" and change.entity_id:
        tag = await session.get(Tag, change.entity_id)
        if tag is not None:
            archived = tag.archived_at is not None
            current = {"name": tag.name, "description": tag.description}
    elif change.entity == "value" and change.entity_id:
        value = await session.get(Value, change.entity_id)
        if value is not None:
            archived = value.archived_at is not None
            current = {
                "name": value.name,
                "description": value.description,
                "active": value.active,
            }
    elif change.entity == "check" and change.entity_id:
        check = await session.get(Check, change.entity_id)
        if check is not None:
            archived = check.archived_at is not None
            current = {
                "title": check.title,
                "repeatable": check.repeatable,
                "status": CHECK_OUTCOME_LABELS[check.outcome or "pending"],
            }
    proposed = {**current, **dict(change.values)}
    if change.entity == "check" and change.action in CHECK_ANSWER_ACTIONS:
        proposed["status"] = CHECK_OUTCOME_LABELS[CHECK_ANSWER_ACTIONS[change.action]]
    if change.entity in {"tag", "value", "check"} and change.action in {"archive", "delete"}:
        current["status"] = "Archived" if archived else "Active"
        proposed["status"] = "Archived" if change.action == "archive" else "Deleted"
    if change.entity == "card":
        if change.action == "complete":
            proposed["stage"] = CardStage.DONE.value
        elif change.action == "cancel":
            proposed["stage"] = CardStage.CANCELLED.value
        elif change.action == "reopen":
            proposed["stage"] = change.values.get("stage", CardStage.BACKLOG.value)
        elif change.action in {"archive", "delete"}:
            current["status"] = "Archived" if archived else "Active"
            proposed["status"] = "Archived" if change.action == "archive" else "Deleted"

        unresolved_references: list[tuple[str, str]] = []
        for spec in CARD_REFERENCE_SPECS:
            if not spec.mentioned_in(change.values):
                continue
            resolved = await resolve_references(session, spec, change.values)
            target_ids = resolved.ids | set(resolved.unknown_ids)
            unresolved_references.extend((spec.label, name) for name in resolved.unresolved)
            existing = set(current.get(spec.plural_key, []))
            if change.action == "link":
                proposed[spec.plural_key] = sorted(existing | target_ids)
            elif change.action == "unlink":
                proposed[spec.plural_key] = sorted(existing - target_ids)
            else:
                proposed[spec.plural_key] = sorted(target_ids)
        if unresolved_references:
            proposed["_unresolved_references"] = unresolved_references
    return current, proposed


def _display_diff_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _reference_names_key(spec: ReferenceSpec) -> str:
    return spec.plural_key.replace("_ids", "_names")


async def _reference_names(session: AsyncSession, spec: ReferenceSpec, value: Any) -> list[str]:
    ids = list(value or [])
    entities = (
        list(await session.scalars(select(spec.model).where(spec.model.id.in_(ids))))
        if ids
        else []
    )
    by_id = {entity.id: getattr(entity, spec.name_attr) for entity in entities}
    return [by_id[item_id] for item_id in ids if item_id in by_id]


async def _proposal_card_display_state(
    session: AsyncSession, state: dict[str, Any]
) -> dict[str, Any]:
    display = dict(state)
    parent = await session.get(Card, state.get("parent_id")) if state.get("parent_id") else None
    display["parent_name"] = parent.title if parent else None
    for spec in CARD_REFERENCE_SPECS:
        display[_reference_names_key(spec)] = await _reference_names(
            session, spec, state.get(spec.plural_key)
        )
    if display.get("id") and display.get("kind") in {
        CardKind.GOAL.value,
        CardKind.IDEA.value,
    }:
        display.update(await card_progress(session, int(display["id"])))
    return display


async def _proposal_diff_value(session: AsyncSession, field: str, value: Any) -> str:
    if field == "parent_id":
        if value is None:
            return "Root"
        parent = await session.get(Card, value)
        return parent.title if parent else f"Card #{value}"
    spec = _REFERENCE_BY_PLURAL.get(field)
    if spec is not None:
        return ", ".join(await _reference_names(session, spec, value)) or "—"
    if field == "categories":
        return category_expression(value)
    if field == "energy_types":
        return energy_expression(value)
    if field in {"stage", "priority"} and value:
        return str(value).title()
    return _display_diff_value(value)


async def _proposal_card_diffs(
    session: AsyncSession, current: dict[str, Any], proposed: dict[str, Any]
) -> list[str]:
    labels = {
        "title": "Title",
        "note": "Note",
        "parent_id": "Parent",
        "stage": "Stage",
        "priority": "Priority",
        "hard_time": "Hard Time",
        "blocked": "Blocked",
        "blocked_description": "Blocked Description",
        "effort_points": "Effort",
        "repeatable": "Repeatable",
        "categories": "Categories",
        "energy_types": "Energy",
        **{spec.plural_key: f"{spec.label}s" for spec in CARD_REFERENCE_SPECS},
        "status": "Status",
    }
    diffs: list[str] = []
    for field, label in labels.items():
        if current.get(field) == proposed.get(field):
            continue
        old = await _proposal_diff_value(session, field, current.get(field))
        new = await _proposal_diff_value(session, field, proposed.get(field))
        diffs.append(f"• {label}: {html.escape(old)} → {html.escape(new)}")
    for label, name in proposed.get("_unresolved_references", []):
        diffs.append(f"• {label}: — → {html.escape(name)} (not found)")
    return diffs


async def render_proposal(
    message: Message,
    services: Services,
    proposal_id: int,
    *,
    replace_message_id: int | None = None,
    notice: str | None = None,
) -> None:
    async with services.sessions() as session:
        proposal = await session.get(ChangeProposal, proposal_id)
        if proposal is None or proposal.status != "pending":
            raise DomainError("Proposal is no longer pending")
        changes = list(
            await session.scalars(
                select(ProposalChange)
                .where(ProposalChange.proposal_id == proposal.id)
                .order_by(ProposalChange.position)
            )
        )
        text_parts = ["<b>Review proposal</b>"]
        if notice:
            text_parts.append(html.escape(notice))
        text_parts.append(html.escape(proposal.message))
        if len(changes) == 1 and changes[0].entity in {"card", "tag", "value", "check"}:
            change = changes[0]
            current, proposed = await _proposal_item_state(session, change)
            item_name = change.entity.title()
            if change.action == "create":
                mode_name = "Create"
            elif change.action in CHECK_ANSWER_ACTIONS and change.entity == "check":
                mode_name = "Answer"
            else:
                mode_name = "Edit"
            text_parts[0] = f"<b>{mode_name} {item_name} · AI proposal</b>"
            if change.entity in {"tag", "value"}:
                text_parts.append(
                    f"Name: {html.escape(_display_diff_value(proposed.get('name')))}\n"
                    f"Description: "
                    f"{html.escape(_display_diff_value(proposed.get('description')))}"
                )
            elif change.entity == "check":
                text_parts.append(
                    f"Title: {html.escape(_display_diff_value(proposed.get('title')))}\n"
                    f"Status: {html.escape(_display_diff_value(proposed.get('status')))}\n"
                    f"Repeatable: "
                    f"{html.escape(_display_diff_value(proposed.get('repeatable')))}"
                )
            elif change.entity == "card":
                display = await _proposal_card_display_state(session, proposed)
                text_parts.append(card_overview_text(display, heading="Card overview"))
            if change.entity == "card" and change.action != "create":
                diffs = await _proposal_card_diffs(session, current, proposed)
            elif change.entity == "card":
                diffs = []
            else:
                diffs = [
                    f"• {field.replace('_', ' ').title()}: "
                    f"{html.escape(_display_diff_value(current.get(field)))} → "
                    f"{html.escape(_display_diff_value(new_value))}"
                    for field, new_value in proposed.items()
                    if current.get(field) != new_value
                ]
            if diffs:
                text_parts.append("<b>Proposed changes</b>\n" + "\n".join(diffs))
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
        )


async def render_ai_outcome(
    message: Message,
    services: Services,
    outcome: AIOutcome,
    *,
    kind: MessageKind = MessageKind.DIALOGUE_ASSISTANT,
) -> None:
    """Render one agent state; suspended approval batches expose only their head item."""
    if outcome.proposal_id is not None:
        await render_proposal(message, services, outcome.proposal_id)
        return
    async with services.sessions() as session:
        text = await render_citations(session, services, html.escape(outcome.message))
    await send_registered(
        message,
        services,
        text,
        kind=kind,
    )


async def continue_agent_approval(
    message: Message,
    services: Services,
    target_type: str,
    target_id: int,
    *,
    decision: str,
    result: dict[str, Any],
) -> bool:
    """Advance a persisted approval queue, resuming the model only after its last item."""
    if getattr(services, "advisor", None) is None or getattr(services, "history", None) is None:
        return False
    if not await services.advisor.has_pending_approval(target_type, target_id):
        return False
    resolved_text = {
        "approved": "✅ Saved.",
        "discarded": "🗑 Discarded.",
        "failed": "⚠️ Failed.",
    }.get(decision, "Resolved.")
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
            "Could not acquire continuation lease after %s %s #%s",
            decision,
            target_type,
            target_id,
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
                target_type,
                target_id,
                decision=decision,
                result=result,
            )
        except Exception as error:
            logger.exception(
                "AI continuation failed after %s %s #%s",
                decision,
                target_type,
                target_id,
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
