"""How a Card reads: its labels, its overview text, its order in a list and its citation."""

from __future__ import annotations

import html
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.telegram import Page, paginate, short_citation_title, with_citation_fields

from ....foundation.marks import REPEAT_TODAY_MARKER, title_marks
from ....foundation.workspace import Workspace
from ..hierarchy import card_progress
from ..model import (
    Card,
    CardCategory,
    CardEnergyType,
    CardKind,
    Category,
    EnergyType,
    Priority,
    effort_label,
)

_KIND_EMOJIS = {
    CardKind.GOAL.value: "🎯",
    CardKind.SUBGOAL.value: "🧩",
    CardKind.ACTION.value: "⭐️",
    CardKind.IDEA.value: "💡",
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


def values_expression(kind: Any, names: list[str]) -> str:
    """Name the Values on a Card, and say plainly when a Goal is carrying none.

    A Goal is where a Value is what says why the work is there, so an empty list is
    something to see rather than a dash among the other dashes. It is not a refusal:
    the link stays optional on every kind.
    """
    if names:
        return ", ".join(names)
    return "⚠️ None" if str(getattr(kind, "value", kind)) == CardKind.GOAL.value else "—"


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


def card_overview_text(
    state: dict[str, Any], *, heading: str = "Card", compact: bool = False
) -> str:
    """What a Card reads as: everything it holds, or the little of it a day needs.

    The compact half is what the owner looks at while working — what this is, where
    it stands, what it costs — and the Values of a Goal, which are the whole reason
    that Goal is there. Everything else is one button away.
    """
    kind = str(state.get("kind") or "")
    lines = [
        f"Kind: {html.escape(kind_label(kind))}",
        f"Title: <b>{html.escape(str(state.get('title') or '—'))}</b>",
    ]
    if kind == CardKind.IDEA.value:
        lines.append(f"Note: {html.escape(str(state.get('note') or '—'))}")
        return f"<b>{html.escape(heading)}</b>\n" + "\n".join(lines)
    if state.get("parent_name"):
        lines.append(f"Parent: {html.escape(str(state['parent_name']))}")
    lines.append(f"Stage: {html.escape(str(state.get('stage') or 'backlog').title())}")
    if state.get("closed_at"):
        lines.append(f"Completed at: {html.escape(str(state['closed_at']))}")
    lines.append(f"Note: {html.escape(str(state.get('note') or '—'))}")
    if not compact:
        lines.extend(
            [
                f"Priority: {html.escape(str(state.get('priority') or 'medium').title())} · "
                f"Hard Time: {'Yes' if state.get('hard_time') else 'No'}",
                f"Blocked: {'Yes' if state.get('blocked') else 'No'}",
            ]
        )
    if state.get("blocked") and not compact:
        # A Goal and a Subgoal read as blocked for the Actions under them, and each of those
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
        lines.append(f"Effort: {effort_label(state.get('effort_points'))}")
        if not compact:
            lines.extend(
                [
                    f"Repeatable: {'Yes' if state.get('repeatable') else 'No'}",
                    f"Categories: "
                    f"{html.escape(category_expression(state.get('categories', [])))}",
                    f"Energy: "
                    f"{html.escape(energy_expression(state.get('energy_types', [])))}",
                ]
            )
    else:
        lines.extend(
            [
                f"Effort: {effort_label(state.get('completed_effort', 0))}"
                f"/{effort_label(state.get('effort_points') or 0)} EP",
                "Children: "
                f"{state.get('completed_children', 0)}/{state.get('total_children', 0)} completed",
            ]
        )
    if compact:
        # A Goal keeps its Values even here: an empty list is what the compact view
        # exists to put in front of the owner, not what it hides.
        if kind == CardKind.GOAL.value:
            lines.append(
                f"Values: {html.escape(values_expression(kind, state.get('value_names', [])))}"
            )
        return f"<b>{html.escape(heading)}</b>\n" + "\n".join(lines)
    lines.extend(
        [
            f"Values: {html.escape(values_expression(kind, state.get('value_names', [])))}",
            f"Tags: {html.escape(', '.join(state.get('tag_names', [])) or '—')}",
        ]
    )
    # Only when there are Checks: manual creation cannot link one, and the Card screen
    # already carries the counts on its Checks button.
    if state.get("check_names"):
        lines.append(f"Checks: {html.escape(', '.join(state['check_names']))}")
    return f"<b>{html.escape(heading)}</b>\n" + "\n".join(lines)


async def card_title_marks(session: AsyncSession, card: Card) -> str:
    """What a Card's title carries: its repeat marks, and today's work on the series.

    A repeating Action says when its series was already completed today, on the
    finished instance and on the open successor alike, so finishing one never leaves
    the next looking as though nothing was done. It acknowledges the work and stops
    there: the successor is still open, and finishing it again today is allowed.
    """
    marks = await title_marks(session, card)
    if card.repeat_series_id is None and not card.repeatable:
        return marks
    workspace = await session.get(Workspace, 1)
    tz = ZoneInfo(workspace.timezone if workspace else "UTC")
    day_start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    if await session.scalar(card.series_done_since_query(day_start)):
        marks += REPEAT_TODAY_MARKER
    return marks


def _emoji_group(values: list[str], emojis: dict[str, str]) -> str:
    """Render a set of existing field icons in the product's established order."""
    present = set(values)
    return "".join(emoji for field, emoji in emojis.items() if field in present)


async def card_citation_label(session: AsyncSession, services: Any, card: Card) -> str:
    """A Card is named by its own metadata, so a citation never restates what Safwa knows."""
    marker = await card_title_marks(session, card)
    leading = f"{kind_emoji(card.kind)} {short_citation_title(card.title)}{marker}"
    if card.kind in {CardKind.GOAL.value, CardKind.SUBGOAL.value}:
        progress = await card_progress(session, card.id)
        return with_citation_fields(
            leading,
            [
                f"⚡{effort_label(progress['completed_effort'])}"
                f"/{effort_label(card.effort_points or 0)}"
            ],
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
            f"⚡{effort_label(card.effort_points)}" if card.effort_points is not None else "",
        )
        if group
    ]
    return with_citation_fields(leading, fields)
