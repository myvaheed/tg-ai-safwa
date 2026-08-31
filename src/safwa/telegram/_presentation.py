"""Pure rendering: text, labels, markup and paging that touch neither a session nor the bot."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..constants import PAGE_SIZE, PROPOSAL_OUTCOME_DETAIL_LIMIT
from ..enums import CardKind, Category, EnergyType, Priority
from ..features.cards.model import CardStage
from ..features.proposals.model import BatchDecision, ProposalChange
from ..models import Card

CITATION_TITLE_LIMIT = 25


def short_citation_title(value: str) -> str:
    """Keep a citation recognisable without letting it consume an advisor reply."""
    title = value.strip()
    return f"{title[: CITATION_TITLE_LIMIT - 1]}…" if len(title) > CITATION_TITLE_LIMIT else title


def with_citation_fields(leading: str, fields: list[str]) -> str:
    return f"{leading} · {'·'.join(fields)}" if fields else leading


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


def with_notice(body: str, notice: str | None) -> str:
    """Carry a warning into the destination screen.

    A callback response replaces the current message, so a warning sent as its own
    message is overwritten by the next render.  It has to be part of that render.
    """
    return f"{html.escape(notice)}\n\n{body}" if notice else body


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


@dataclass(frozen=True)
class Page:
    items: list[Any]
    index: int
    count: int

    @property
    def label(self) -> str:
        return f"page {self.index + 1}/{self.count}"


def paginate(items: list[Any], page: int, size: int = PAGE_SIZE) -> Page:
    last = max(0, (len(items) - 1) // size)
    index = min(max(page, 0), last)
    return Page(items[index * size : (index + 1) * size], index, last + 1)


def paginate_cards(cards: list[Card], page: int) -> Page:
    return paginate(sorted(cards, key=_live_card_order), page)


def proposal_change_summary(change: ProposalChange) -> str:
    target = f" #{change.entity_id}" if change.entity_id is not None else ""
    values = ", ".join(f"{key}={value!r}" for key, value in change.values.items())
    suffix = f": {values}" if values else ""
    return f"{change.action.title()} {change.entity.title()}{target}{suffix}"


PROPOSAL_OUTCOME_HEADINGS = {
    BatchDecision.APPROVED: "✅ Saved",
    BatchDecision.DISCARDED: "🗑 Discarded",
    BatchDecision.FAILED: "⚠️ Failed",
}


def _carries_a_value(field: str) -> bool:
    """Whether a `Label: value` detail line says anything. `—` is the empty rendering."""
    _, separator, value = field.partition(": ")
    return not separator or value.strip() not in {"", "—", "— → —"}


def proposal_outcome_text(
    decision: BatchDecision,
    summary: str,
    fields: list[str] | None = None,
    *,
    notice: str | None = None,
) -> str:
    """The one text a resolved proposal leaves in the conversation.

    Save, Discard, and the screen a navigation freezes all read the same way, so the model
    rereading the dialogue learns what happened from one shape rather than three.
    """
    parts = [f"<b>{PROPOSAL_OUTCOME_HEADINGS.get(decision, 'Resolved')}</b>"]
    if notice:
        parts.append(html.escape(notice))
    if summary:
        parts.append(html.escape(summary))
    details = [field for field in (fields or []) if _carries_a_value(field)]
    if details:
        capped = details[:PROPOSAL_OUTCOME_DETAIL_LIMIT]
        if len(details) > PROPOSAL_OUTCOME_DETAIL_LIMIT:
            capped.append(f"… and {len(details) - PROPOSAL_OUTCOME_DETAIL_LIMIT} more")
        parts.append("\n".join(f"• {html.escape(field)}" for field in capped))
    return "\n".join(parts)


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


def menu_markup(*, sprint_active: bool) -> InlineKeyboardMarkup:
    """The menu. Today belongs to a running Sprint, so Planning does not offer it."""
    sprint_row = [InlineKeyboardButton(text="🏃 Sprint", callback_data="nav:sprint")]
    if sprint_active:
        sprint_row.insert(0, InlineKeyboardButton(text="☀️ Today", callback_data="nav:today"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            sprint_row,
            [
                InlineKeyboardButton(text="📚 Backlog", callback_data="nav:backlog"),
                InlineKeyboardButton(text="➕ Add", callback_data="nav:add"),
            ],
            [
                InlineKeyboardButton(text="💎 Values", callback_data="nav:values"),
                InlineKeyboardButton(text="🏷 Tags", callback_data="nav:tags"),
            ],
            [
                InlineKeyboardButton(text="🔎 Requests", callback_data="nav:requests"),
                InlineKeyboardButton(text="⏰ Reminders", callback_data="nav:reminders"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="nav:settings"),
            ],
        ]
    )


def menu_row() -> list[InlineKeyboardButton]:
    """A consistent escape hatch for a screen reached through quick actions."""
    return [InlineKeyboardButton(text="↩️ Menu", callback_data="nav:home")]


def start_payload(text: str | None) -> str | None:
    """The deep-link payload of a `/start <payload>` message, if this is one.

    `command_start` also serves the menu's Home button, where it is handed the bot's own
    screen — only a real command line may be read as a payload.
    """
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].split("@", 1)[0].casefold() != "/start":
        return None
    return parts[1].strip() or None
