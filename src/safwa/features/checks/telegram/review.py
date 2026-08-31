"""How a Check reads to the owner: its review screen, and the screen a citation opens."""

from __future__ import annotations

import html
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ....ai.contracts import AgentChange
from ....foundation.marks import title_marks
from ....foundation.references import resolve_references
from ....models import Check
from ....shell import short_citation_title
from ...proposals.api import (
    ChangeAction,
    ProposalChange,
    ProposalScreen,
    detail_label,
    detail_lines,
    detail_value,
    display_diff_value,
    field_diffs,
    named_summary,
    reference_groups,
    reference_names,
    result_value,
)
from ..model import CHECK_OUTCOME_LABELS
from ..proposal import CHECK_ANSWER_ACTIONS
from ..references import CHECK_VALUE_REFERENCE
from ..use_cases import check_value_ids


async def _value_names(session: AsyncSession, value_ids: list[int]) -> str:
    """The Values on a Check, as the owner reads them on a screen."""
    names = await reference_names(session, CHECK_VALUE_REFERENCE, sorted(value_ids))
    return ", ".join(result_value(name) for name in names)


class CheckProposalPresenter:
    entity = "check"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values))

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        values = dict(change.values)
        if change.action in {ChangeAction.LINK, ChangeAction.UNLINK}:
            verb = change.action.title()
            groups = await reference_groups(session, values, (CHECK_VALUE_REFERENCE,))
            return [f"{verb}: {group}" for group in groups]
        proposed = {name: values[name] for name in ("title", "repeatable") if name in values}
        if change.action in {ChangeAction.COMPLETE, ChangeAction.CANCEL}:
            proposed["outcome"] = CHECK_ANSWER_ACTIONS[change.action]
        check = (
            await session.get(Check, change.entity_id)
            if change.entity_id is not None
            else None
        )
        if change.action is ChangeAction.CREATE or check is None:
            return detail_lines(proposed)
        if change.action is ChangeAction.ARCHIVE:
            return [f"Check: #{check.id} “{result_value(check.title)}”"]
        before = {
            "title": check.title,
            "repeatable": check.repeatable,
            "outcome": check.outcome or "pending",
        }
        return [
            f"{detail_label(field)}: {detail_value(before.get(field))} → {detail_value(value)}"
            for field, value in proposed.items()
            if before.get(field) != value
        ]

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        if change.action in {ChangeAction.COMPLETE, ChangeAction.CANCEL}:
            check = (
                await session.get(Check, change.entity_id)
                if change.entity_id is not None
                else None
            )
            outcome = CHECK_ANSWER_ACTIONS[change.action]
            head = f"“{result_value(check.title)}”" if check else f"#{change.entity_id}"
            return f"Answer Check {head} ({CHECK_OUTCOME_LABELS[outcome]})"
        return await named_summary(session, change, details, model=Check)

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        current: dict[str, Any] = {}
        archived = False
        linked_ids: list[int] = []
        if change.entity_id:
            check = await session.get(Check, change.entity_id)
            if check is not None:
                archived = check.archived_at is not None
                linked_ids = await check_value_ids(session, check.id)
                current = {
                    "title": check.title,
                    "repeatable": check.repeatable,
                    "status": CHECK_OUTCOME_LABELS[check.outcome or "pending"],
                    "values": await _value_names(session, linked_ids),
                }
        payload = {k: v for k, v in change.values.items() if not k.startswith("value_")}
        proposed = {**current, **payload}
        if change.action in {ChangeAction.LINK, ChangeAction.UNLINK}:
            resolved = await resolve_references(session, CHECK_VALUE_REFERENCE, change.values)
            target = resolved.ids | set(resolved.unknown_ids)
            after = (
                set(linked_ids) | target
                if change.action is ChangeAction.LINK
                else set(linked_ids) - target
            )
            proposed["values"] = await _value_names(session, list(after))
        if change.action in CHECK_ANSWER_ACTIONS:
            proposed["status"] = CHECK_OUTCOME_LABELS[CHECK_ANSWER_ACTIONS[change.action]]
        if change.action in {ChangeAction.ARCHIVE, ChangeAction.DELETE}:
            current["status"] = "Archived" if archived else "Active"
            proposed["status"] = "Archived" if change.action is ChangeAction.ARCHIVE else "Deleted"
        if change.action is ChangeAction.CREATE:
            mode = "Create"
        elif change.action in CHECK_ANSWER_ACTIONS:
            mode = "Answer"
        else:
            mode = "Edit"
        return ProposalScreen(
            mode=mode,
            item="Check",
            blocks=(
                f"Title: {html.escape(display_diff_value(proposed.get('title')))}\n"
                f"Status: {html.escape(display_diff_value(proposed.get('status')))}\n"
                f"Repeatable: "
                f"{html.escape(display_diff_value(proposed.get('repeatable')))}\n"
                f"Values: {html.escape(display_diff_value(proposed.get('values')))}",
            ),
            diffs=field_diffs(current, proposed),
        )


async def check_citation_label(
    session: AsyncSession, services: Any, check: Check
) -> str | None:
    """A live Check keeps the model's own words.

    A closed repeat or an archived one has to carry its marks, or the link looks exactly
    like the open one it was superseded by.
    """
    marker = await title_marks(session, check)
    return f"{short_citation_title(check.title)}{marker}" if marker else None
