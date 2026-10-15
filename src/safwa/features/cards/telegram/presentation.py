"""How a Card reads: its labels, its overview text, its order in a list and its citation."""

from __future__ import annotations

import html
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.telegram import citation_title, with_citation_fields

from ....foundation.marks import REPEAT_OPEN_MARKER, title_marks
from ...profile.api import effort_tracking_on
from ..api import forest_rows, parents_of, tree_level
from ..hierarchy import card_progress
from ..model import (
    Card,
    CardCategory,
    CardEnergyType,
    CardKind,
    CardStage,
    Category,
    EnergyType,
    card_kind_name,
    effort_label,
    minutes_label,
)

STAGE_EMOJIS = {
    CardStage.BACKLOG.value: "📚",
    CardStage.SPRINT.value: "🏃",
    CardStage.TODAY.value: "☀️",
    CardStage.DONE.value: "✅",
}

# Telegram cuts a button's text short of this anyway; cut here, it ends where the title does.
ITEM_BUTTON_LIMIT = 60


async def item_button_label(session: AsyncSession, card: Card) -> str:
    """A Card on a button in a list of several stages: its kind and its stage as emoji, which
    say both without a word (CD-BUTTON-048)."""
    stage = STAGE_EMOJIS[card.effective_stage]
    marker = await card_emoji(session, card)
    title = card.title[: ITEM_BUTTON_LIMIT - len(stage) - len(marker) - 4]
    return f"{marker} {title} · {stage}"


CATEGORY_EMOJIS = {
    Category.GROWTH.value: "🌱",
    Category.PEOPLE.value: "🫂",
    Category.WORK.value: "💰",
    Category.CHORES.value: "🧺",
    Category.REST.value: "🔋",
}


ENERGY_EMOJIS = {
    EnergyType.PHYSICAL.value: "💪",
    EnergyType.COGNITIVE.value: "🧠",
    EnergyType.EMOTIONAL.value: "🎭",
    EnergyType.SPIRITUAL.value: "🕊️",
}

# Each Category and Energy type keeps its colour on every chart, in the order they stack:
# neighbours stay apart for a colour-blind eye too.
CATEGORY_COLORS = {
    Category.GROWTH.value: "#1baf7a",
    Category.PEOPLE.value: "#e87ba4",
    Category.WORK.value: "#eda100",
    Category.CHORES.value: "#863748",
    Category.REST.value: "#2a78d6",
}
ENERGY_COLORS = {
    EnergyType.PHYSICAL.value: "#eb6834",
    EnergyType.COGNITIVE.value: "#4a3aa7",
    EnergyType.EMOTIONAL.value: "#008300",
    EnergyType.SPIRITUAL.value: "#e34948",
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


def kind_label(value: Any, has_parent: bool = False, *, level: int = 0) -> str:
    name = card_kind_name(str(getattr(value, "value", value)), has_parent)
    emoji = kind_emoji(value, level)
    return f"{emoji} {name}" if emoji else name


def values_expression(kind: Any, names: list[str], has_parent: bool = False) -> str:
    """Name the Values on a Card, and say plainly when a Goal is carrying none.

    A Goal is where a Value is what says why the work is there, so an empty list is
    something to see rather than a dash among the other dashes. It is not a refusal:
    the link stays optional on every kind.
    """
    if names:
        return ", ".join(names)
    return "⚠️ None" if kind == CardKind.GOAL.value and not has_parent else "—"


def kind_emoji(value: Any, level: int = 0) -> str:
    """Return the compact Card-kind marker without repeating its text label."""
    kind = str(getattr(value, "value", value))
    if kind == CardKind.ACTION.value:
        return "⭐️"
    if kind == CardKind.GOAL.value:
        suffix = (
            str(level).translate(str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))
            if level > 1 else ""
        )
        return "🎯" + suffix
    return ""


async def card_emoji(session: AsyncSession, card: Card) -> str:
    return kind_emoji(card.kind, await tree_level(session, card.kind, card.id))


def category_expression(values: Any) -> str:
    return _typed_expression(values, CATEGORY_EMOJIS)


def energy_expression(values: Any) -> str:
    return _typed_expression(values, ENERGY_EMOJIS)


# A result's three groups, in the order they are listed.
_LIVE, _DONE, _ARCHIVED = range(3)


async def result_order(session: AsyncSession, cards: list[Card]) -> list[Card]:
    """A list of Cards as one flat tree (CD-ORDER-054).

    Live Cards come first, then Done ones, then archived ones. A Done Action leaves its open
    parents for the Done group, and a Card under a Done or archived parent stays with it.
    In each group the Goals with the most levels lead, each Card right under the last of its
    parents from the same list and group, and Actions follow the Goals. The order the list
    came in breaks every tie.
    """
    by_id = {card.id: card for card in cards}
    position = {card.id: index for index, card in enumerate(cards)}
    levels = {card.id: await tree_level(session, card.kind, card.id) for card in cards}
    parents = {
        card_id: [by_id[parent] for parent in held if parent in by_id]
        for card_id, held in (await parents_of(session, list(by_id))).items()
    }
    groups: dict[int, int] = {}

    def group(card: Card) -> int:
        if card.id not in groups:
            own = (
                _ARCHIVED
                if card.archived_at is not None
                else _DONE
                if card.effective_stage == CardStage.DONE.value
                else _LIVE
            )
            groups[card.id] = max([own, *(group(parent) for parent in parents.get(card.id, ()))])
        return groups[card.id]

    ranked = sorted(cards, key=lambda card: (group(card), -levels[card.id], position[card.id]))
    anchors = {
        card.id: [parent.id for parent in parents.get(card.id, ()) if group(parent) == group(card)]
        for card in cards
    }
    return [row.card for row in forest_rows(ranked, anchors)]


def progress_summary(progress: dict[str, Any], *, effort_tracking: bool) -> str:
    """Cards counted as a Goal's children are, on one line: 🎯2 · ⭐️4 · ⚡3/10 EP.

    A kind with none is left out, and no Cards at all read as nothing.
    """
    parts = [
        f"{emoji}{progress[key]}"
        for emoji, key in (("🎯", "goals"), ("⭐️", "actions"))
        if progress[key]
    ]
    if parts and effort_tracking:
        parts.append(
            f"⚡{effort_label(progress['completed_effort'])}"
            f"/{effort_label(progress['effort_points'])} EP"
        )
    return " · ".join(parts)


def card_overview_text(
    state: dict[str, Any],
    *,
    heading: str = "Card",
    compact: bool = False,
    effort_tracking: bool = False,
) -> str:
    """What a Card reads as: everything it holds, or the little of it a day needs.

    The compact half is what the owner looks at while working — what this is, where
    it stands, what it costs — and the Values of a Goal, which are the whole reason
    that Goal is there. Everything else is one button away.

    The keys ending in `_html` are already HTML: the Card's screen puts links in them and
    a review screen plain words.
    """
    kind = str(state.get("kind") or "")
    has_parent = bool(state.get("parent_ids"))
    lines = [
        f"Kind: {html.escape(kind_label(kind, has_parent, level=state.get('tree_level', 0)))}",
        f"Title: <b>{html.escape(str(state.get('title') or '—'))}</b>",
    ]
    if parents := state.get("parents_html"):
        lines.append(f"{'Parent' if len(parents) == 1 else 'Parents'}: {', '.join(parents)}")
    lines.append(f"Stage: {html.escape(str(state.get('stage') or 'backlog').title())}")
    if state.get("closed_at"):
        lines.append(f"Completed at: {html.escape(str(state['closed_at']))}")
    lines.append(f"Note: {html.escape(str(state.get('note') or '—'))}")
    if state.get("schedule") or not compact:
        label = "Schedule" if kind == CardKind.ACTION.value else "Deadline"
        lines.append(f"{label}: {html.escape(str(state.get('schedule') or '—'))}")
        if state.get("schedule_summary"):
            lines.append(html.escape(state["schedule_summary"]))
    if not compact:
        lines.append(f"Priority: {html.escape(str(state.get('priority') or 'medium').title())}")
    # Only an Action is ever blocked, and its reason is what blocks it.
    if kind == CardKind.ACTION.value and not compact:
        reason = str(state.get("blocked_description") or "")
        lines.append(f"Blocked: {'Yes' if reason else 'No'}")
        if reason:
            lines.append(f"Blocked description: {html.escape(reason)}")
    if kind == CardKind.ACTION.value:
        if effort_tracking:
            lines.append(f"Effort: {effort_label(state.get('effort_points'))}")
        if state.get("tracked_mins"):
            lines.append(f"Time spent: {minutes_label(state['tracked_mins'])}")
        if not compact:
            lines.extend(
                [
                    f"Categories: "
                    f"{html.escape(category_expression(state.get('categories', [])))}",
                    f"Energy: "
                    f"{html.escape(energy_expression(state.get('energy_types', [])))}",
                ]
            )
    else:
        lines.append(f"Children: {state.get('children_html') or '—'}")
        if effort_tracking and state.get("unestimated_actions"):
            lines.append(f"{state['unestimated_actions']} Actions have no estimate.")
        if state.get("tracked_mins"):
            lines.append(f"Time spent: {minutes_label(state['tracked_mins'])}")
    if compact:
        # A Goal keeps its Values even here: an empty list is what the compact view
        # exists to put in front of the owner, not what it hides.
        if kind == CardKind.GOAL.value and not has_parent:
            lines.append(f"Values: {values_expression(kind, state.get('value_html', []))}")
        return f"<b>{html.escape(heading)}</b>\n" + "\n".join(lines)
    lines.extend(
        [
            f"Values: {values_expression(kind, state.get('value_html', []), has_parent)}",
            f"Tags: {', '.join(state.get('tag_html', [])) or '—'}",
        ]
    )
    # Only when there are Checks: manual creation cannot link one, and the Card screen
    # already carries the counts on its Checks button.
    if state.get("check_html"):
        lines.append(f"Checks: {', '.join(state['check_html'])}")
    return f"<b>{html.escape(heading)}</b>\n" + "\n".join(lines)


async def card_title_marks(session: AsyncSession, card: Card) -> str:
    """What a Card's title carries on a screen: its repeat marks, and on the open instance
    of a repeating Action that it repeats, so the one to work with is told apart from the
    finished ones at a glance (CD-REPEAT-032)."""
    marks = await title_marks(session, card)
    if card.kind == CardKind.ACTION.value and card.completed_at is None and card.repeats():
        marks = REPEAT_OPEN_MARKER + marks
    return marks


def _emoji_group(values: list[str], emojis: dict[str, str]) -> str:
    """Render a set of existing field icons in the product's established order."""
    present = set(values)
    return "".join(emoji for field, emoji in emojis.items() if field in present)


async def card_citation_label(
    session: AsyncSession, services: Any, card: Card, *, short: bool
) -> str:
    """A Card is named by its own metadata, so a citation never restates what Safwa knows."""
    marker = await card_title_marks(session, card)
    effort_tracking = await effort_tracking_on(session)
    leading = f"{await card_emoji(session, card)} {citation_title(card.title, short=short)}{marker}"
    if card.kind == CardKind.GOAL.value:
        progress = await card_progress(session, card.id)
        if not effort_tracking:
            return with_citation_fields(
                leading, [f"{progress['completed_children']}/{progress['total_children']} completed"]
            )
        return with_citation_fields(
            leading,
            [
                f"⚡{effort_label(progress['completed_effort'])}"
                f"/{effort_label(card.effort_points)}",
                *([f"{progress['unestimated_actions']} unestimated"] if progress['unestimated_actions'] else []),
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
            f"⚡{effort_label(card.effort_points)}"
            if effort_tracking and card.effort_points is not None else "",
        )
        if group
    ]
    return with_citation_fields(leading, fields)
