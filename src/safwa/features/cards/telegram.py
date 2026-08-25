"""How a proposed Card reads to the owner: its receipt lines and its review screen."""

from __future__ import annotations

import html
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.contracts import AgentChange
from ...domain import (
    CARD_REFERENCE_SPECS,
    card_progress,
    resolve_references,
)
from ...enums import (
    CardKind,
    Priority,
)
from ...models import (
    Card,
    CardCategory,
    CardEnergyType,
    CardTag,
    CardValue,
    ProposalChange,
)
from ...telegram import card_overview_text, category_expression, energy_expression
from ..proposals.api import (
    ACTION_VERBS,
    ProposalScreen,
    detail_label,
    detail_lines,
    detail_value,
    display_diff_value,
    reference_details,
    reference_groups,
    reference_names,
    result_value,
)
from .model import CardStage

CARD_DETAIL_FIELDS = (
    "kind",
    "title",
    "note",
    "stage",
    "priority",
    "hard_time",
    "blocked",
    "blocked_description",
    "effort_points",
    "repeatable",
    "categories",
    "energy_types",
    "parent_id",
)


# Every Card relationship diffs and renders through its spec, so a new one shows up here
# without a second table to update.
_REFERENCE_BY_PLURAL = {spec.plural_key: spec for spec in CARD_REFERENCE_SPECS}


def normalized_card_details(values: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    fields = {name: values[name] for name in CARD_DETAIL_FIELDS if name in values}
    if creating:
        fields.setdefault("stage", CardStage.BACKLOG.value)
        fields.setdefault("note", "")
        fields.setdefault("priority", "medium")
        fields.setdefault("hard_time", False)
        fields.setdefault("blocked", False)
        if fields.get("kind") == CardKind.ACTION.value:
            fields.setdefault("repeatable", False)
            fields.setdefault("categories", [])
            fields.setdefault("energy_types", [])
    if referenced_values := reference_details(values, "value"):
        fields["values"] = referenced_values
    if referenced_tags := reference_details(values, "tag"):
        fields["tags"] = referenced_tags
    if referenced_checks := reference_details(values, "check"):
        fields["checks"] = referenced_checks
    return fields


async def _card_detail_snapshot(session: AsyncSession, card: Card) -> dict[str, Any]:
    return {
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
        "parent_id": card.parent_id,
        "values": [
            f"#{item}"
            for item in sorted(
                await session.scalars(
                    select(CardValue.value_id).where(CardValue.card_id == card.id)
                )
            )
        ],
        "tags": [
            f"#{item}"
            for item in sorted(
                await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card.id))
            )
        ],
    }


async def _card_state(
    session: AsyncSession, change: ProposalChange
) -> tuple[dict[str, Any], dict[str, Any]]:
    current: dict[str, Any] = {}
    archived = False
    if change.entity_id:
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
                        select(CardEnergyType.energy_type).where(
                            CardEnergyType.card_id == card.id
                        )
                    )
                ),
            }
            for spec in CARD_REFERENCE_SPECS:
                current[spec.plural_key] = sorted(
                    await session.scalars(
                        select(spec.link_column).where(spec.link_model.card_id == card.id)
                    )
                )
    proposed = {**current, **dict(change.values)}
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


async def _card_display_state(
    session: AsyncSession, state: dict[str, Any]
) -> dict[str, Any]:
    display = dict(state)
    parent = await session.get(Card, state.get("parent_id")) if state.get("parent_id") else None
    display["parent_name"] = parent.title if parent else None
    for spec in CARD_REFERENCE_SPECS:
        display[spec.plural_key.replace("_ids", "_names")] = await reference_names(
            session, spec, state.get(spec.plural_key)
        )
    if display.get("id") and display.get("kind") in {
        CardKind.GOAL.value,
        CardKind.IDEA.value,
    }:
        display.update(await card_progress(session, int(display["id"])))
    return display


async def _card_diff_value(session: AsyncSession, field: str, value: Any) -> str:
    if field == "parent_id":
        if value is None:
            return "Root"
        parent = await session.get(Card, value)
        return parent.title if parent else f"Card #{value}"
    spec = _REFERENCE_BY_PLURAL.get(field)
    if spec is not None:
        return ", ".join(await reference_names(session, spec, value)) or "—"
    if field == "categories":
        return category_expression(value)
    if field == "energy_types":
        return energy_expression(value)
    if field in {"stage", "priority"} and value:
        return str(value).title()
    return display_diff_value(value)


async def _card_diffs(
    session: AsyncSession, current: dict[str, Any], proposed: dict[str, Any]
) -> tuple[str, ...]:
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
        old = await _card_diff_value(session, field, current.get(field))
        new = await _card_diff_value(session, field, proposed.get(field))
        diffs.append(f"• {label}: {html.escape(old)} → {html.escape(new)}")
    for label, name in proposed.get("_unresolved_references", []):
        diffs.append(f"• {label}: — → {html.escape(name)} (not found)")
    return tuple(diffs)


class CardProposalPresenter:
    entity = "card"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(
            normalized_card_details(dict(change.values), creating=change.action == "create")
        )

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        values = dict(change.values)
        if change.action == "create":
            return detail_lines(normalized_card_details(values, creating=True))
        card = (
            await session.get(Card, change.entity_id)
            if change.entity_id is not None
            else None
        )
        if card is None:
            fallback_lines = self.raw_details(fallback) if fallback is not None else []
            return fallback_lines or detail_lines(
                normalized_card_details(values, creating=False)
            )
        before = await _card_detail_snapshot(session, card)
        if change.action in {"link", "unlink"}:
            relationship = normalized_card_details(values, creating=False)
            verb = "Link" if change.action == "link" else "Unlink"
            return [
                f"{verb} {detail_label(field)}: {detail_value(value)}"
                for field, value in relationship.items()
            ]
        proposed = normalized_card_details(values, creating=False)
        if change.action == "move":
            proposed = {"stage": values.get("stage")}
        elif change.action == "complete":
            proposed = {"stage": CardStage.DONE.value}
        elif change.action == "cancel":
            proposed = {"stage": CardStage.CANCELLED.value}
        elif change.action == "reopen":
            proposed = {"stage": values.get("stage", CardStage.BACKLOG.value)}
        elif change.action in {"archive", "delete"}:
            return [f"Card: {card.kind.title()} #{card.id} “{card.title}”"]
        return [
            f"{detail_label(field)}: {detail_value(before.get(field))} → {detail_value(value)}"
            for field, value in proposed.items()
            if before.get(field) != value
        ]

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        values = dict(change.values)
        action = change.action
        verb = ACTION_VERBS.get(action, action.title())
        card = (
            await session.get(Card, change.entity_id)
            if change.entity_id is not None
            else None
        )
        title = result_value(values.get("title") or (card.title if card else ""))
        kind = str(values.get("kind") or (card.kind if card else "") or "card")
        head = f"{kind.title()} “{title}”" if title else f"Card #{change.entity_id}"
        parent = (
            await session.get(Card, int(values["parent_id"]))
            if values.get("parent_id")
            else None
        )
        if action in {"link", "unlink"}:
            joined = " · ".join(await reference_groups(session, values))
            preposition = "to" if action == "link" else "from"
            return f"{verb} {joined} {preposition} {head}" if joined else f"{verb} {head}"
        parts: list[str] = []
        if action == "create":
            # Only what was actually chosen: the defaults a new Card lands on say
            # nothing, and a receipt naming them buries the fields that do.
            stage = str(values.get("stage") or CardStage.BACKLOG.value)
            if stage != CardStage.BACKLOG.value:
                parts.append(stage.title())
            priority = str(values.get("priority") or Priority.MEDIUM.value)
            if priority != Priority.MEDIUM.value:
                parts.append(priority.title())
            if values.get("effort_points"):
                parts.append(f"{values['effort_points']} EP")
            for field_name in ("categories", "energy_types"):
                if chosen := values.get(field_name):
                    parts.append(detail_value(chosen))
            if values.get("hard_time"):
                parts.append("Hard time")
            if values.get("repeatable"):
                parts.append("Repeatable")
            if values.get("blocked"):
                parts.append("Blocked")
            parts.extend(await reference_groups(session, values))
        elif action in {"move", "reopen"} and values.get("stage"):
            parts.append(str(values["stage"]).title())
        elif action == "update":
            parts.extend(detail for detail in details if not detail.startswith("Parent ID:"))
        if parent is not None:
            head += f" under {parent.kind.title()} “{result_value(parent.title)}”"
        return f"{verb} {head}" + (f" ({' · '.join(parts)})" if parts else "")

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        current, proposed = await _card_state(session, change)
        display = await _card_display_state(session, proposed)
        # The Card screen is the overview itself; a field diff repeats it only when the
        # Card already exists.
        diffs = (
            await _card_diffs(session, current, proposed) if change.action != "create" else ()
        )
        return ProposalScreen(
            mode="Create" if change.action == "create" else "Edit",
            item="Card",
            blocks=(card_overview_text(display, heading="Card overview"),),
            diffs=diffs,
        )
