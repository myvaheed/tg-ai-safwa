"""How a Card reads: its labels, its overview text, its order in a list and its citation."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.telegram import Page, paginate, short_citation_title, with_citation_fields

from ....foundation.marks import title_marks
from ..model import (
    Card,
    CardCategory,
    CardEnergyType,
    CardKind,
    CardStage,
    Category,
    EnergyType,
    Priority,
)
from ..use_cases import card_progress

_KIND_EMOJIS = {
    CardKind.GOAL.value: "🎯",
    CardKind.IDEA.value: "💡",
    CardKind.ACTION.value: "⭐️",
}


CATEGORY_EMOJIS = {
    Category.SELF.value: "🌱",
    Category.CONTRIBUTION.value: "❤️",
    Category.WORK.value: "💰",
    Category.REST.value: "🔋",
}


ENERGY_EMOJIS = {
    EnergyType.PHYSICAL.value: "💪",
    EnergyType.COGNITIVE.value: "🧠",
    EnergyType.SOCIAL.value: "🤝",
    EnergyType.VALUES.value: "💎",
}


def typed_label(value: Any, emojis: dict[str, str]) -> str:
    raw = str(getattr(value, "value", value)).strip()
    normalized = raw.casefold()
    emoji = emojis.get(normalized)
    return f"{emoji} {normalized.title()}" if emoji else raw


def _typed_expression(values: Any, emojis: dict[str, str]) -> str:
    """Label an overlapping set of Categories or Energy types, or an em dash if empty."""
    items = values.split(",") if isinstance(values, str) else (values or [])
    labels = [typed_label(item, emojis) for item in items if str(item).strip()]
    return ", ".join(labels) or "—"


def kind_label(value: Any) -> str:
    return typed_label(value, _KIND_EMOJIS)


def kind_emoji(value: Any) -> str:
    """Return the compact Card-kind marker without repeating its text label."""
    return _KIND_EMOJIS.get(str(getattr(value, "value", value)).strip().casefold(), "")


def category_expression(values: Any) -> str:
    return _typed_expression(values, CATEGORY_EMOJIS)


def energy_expression(values: Any) -> str:
    return _typed_expression(values, ENERGY_EMOJIS)


_PRIORITY_ORDER = {Priority.CRITICAL.value: 0, Priority.MEDIUM.value: 1, Priority.LOW.value: 2}


def _live_card_order(card: Card) -> tuple[bool, int, datetime]:
    """Hard Time first, then priority, then oldest — one ordering for every Card list."""
    return (not card.hard_time, _PRIORITY_ORDER[card.priority], card.created_at)


def paginate_cards(cards: list[Card], page: int) -> Page:
    return paginate(sorted(cards, key=_live_card_order), page)


def card_overview_text(state: dict[str, Any], *, heading: str = "Card") -> str:
    kind = str(state.get("kind") or "")
    lines = [
        f"Kind: {html.escape(kind_label(kind))}",
        f"Title: <b>{html.escape(str(state.get('title') or '—'))}</b>",
    ]
    if state.get("parent_name"):
        lines.append(f"Parent: {html.escape(str(state['parent_name']))}")
    lines.append(f"Stage: {html.escape(str(state.get('stage') or 'backlog').title())}")
    if state.get("closed_at"):
        closed = (
            "Cancelled at"
            if state.get("stage") == CardStage.CANCELLED.value
            else "Completed at"
        )
        lines.append(f"{closed}: {html.escape(str(state['closed_at']))}")
    lines.extend(
        [
            f"Note: {html.escape(str(state.get('note') or '—'))}",
            f"Priority: {html.escape(str(state.get('priority') or 'medium').title())} · "
            f"Hard Time: {'Yes' if state.get('hard_time') else 'No'}",
            f"Blocked: {'Yes' if state.get('blocked') else 'No'}",
        ]
    )
    if state.get("blocked"):
        # A Goal and an Idea read as blocked for the Actions under them, and each of those
        # gave its own reason, so the screen quotes them instead of inventing one.
        blocking = state.get("blocking_actions") or []
        if blocking:
            lines.extend(
                f"Blocked by {html.escape(str(title))}: {html.escape(str(reason))}"
                for title, reason in blocking
            )
        else:
            lines.append(
                "Blocked description: "
                + html.escape(str(state.get("blocked_description") or "Required"))
            )
    if kind == CardKind.ACTION.value:
        lines.extend(
            [
                f"Effort: {state.get('effort_points') or '—'}",
                f"Repeatable: {'Yes' if state.get('repeatable') else 'No'}",
                f"Categories: {html.escape(category_expression(state.get('categories', [])))}",
                f"Energy: {html.escape(energy_expression(state.get('energy_types', [])))}",
            ]
        )
    else:
        lines.extend(
            [
                f"Effort: {state.get('completed_effort', 0)}/{state.get('effort_points') or 0} EP",
                "Children: "
                f"{state.get('completed_children', 0)}/{state.get('total_children', 0)} completed",
            ]
        )
    lines.extend(
        [
            f"Values: {html.escape(', '.join(state.get('value_names', [])) or '—')}",
            f"Tags: {html.escape(', '.join(state.get('tag_names', [])) or '—')}",
        ]
    )
    # Only when there are Checks: manual creation cannot link one, and the Card screen
    # already carries the counts on its Checks button.
    if state.get("check_names"):
        lines.append(f"Checks: {html.escape(', '.join(state['check_names']))}")
    return f"<b>{html.escape(heading)}</b>\n" + "\n".join(lines)


def _emoji_group(values: list[str], emojis: dict[str, str]) -> str:
    """Render a set of existing field icons in the product's established order."""
    present = set(values)
    return "".join(emoji for field, emoji in emojis.items() if field in present)


async def card_citation_label(session: AsyncSession, services: Any, card: Card) -> str:
    """A Card is named by its own metadata, so a citation never restates what Safwa knows."""
    marker = await title_marks(session, card)
    leading = f"{kind_emoji(card.kind)} {short_citation_title(card.title)}{marker}"
    if card.kind in {CardKind.GOAL.value, CardKind.IDEA.value}:
        progress = await card_progress(session, card.id)
        return with_citation_fields(
            leading, [f"⚡{progress['completed_effort']}/{card.effort_points or 0}"]
        )
    if card.kind != CardKind.ACTION.value:
        return leading

    categories = list(
        await session.scalars(
            select(CardCategory.category).where(CardCategory.card_id == card.id)
        )
    )
    energy_types = list(
        await session.scalars(
            select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
        )
    )
    fields = [
        group
        for group in (
            _emoji_group(energy_types, ENERGY_EMOJIS),
            _emoji_group(categories, CATEGORY_EMOJIS),
            f"⚡{card.effort_points}" if card.effort_points is not None else "",
        )
        if group
    ]
    return with_citation_fields(leading, fields)
