"""How a proposal about the Sprint is checked and then saved.

There is no running Sprint to edit field by field: Save starts the next one, sets its
Success criteria, length or capacity, or finishes the running one, through the same
operations the Sprint screen's buttons call, with the same refusals. Preparing asks those
refusals first, so the model hears one before the owner is shown a screen.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
)
from tg_agent_shell.proposals.model import ChangeAction

from ...foundation.workspace import require_workspace
from .api import capacity_refusal, criteria_refusal, length_refusal, start_refusal
from .use_cases import (
    FINISHED_BY_HAND,
    finish_sprint,
    set_sprint_capacity,
    set_sprint_length,
    set_sprint_success_criteria,
    start_sprint,
)


async def _next_sprint_refusal(
    session: AsyncSession, values: dict[str, Any]
) -> ToolPreparationError | None:
    """The first value of the next Sprint that cannot be set now, as the model hears it."""
    checks = (
        ("success_criteria", criteria_refusal, "criteria_refused"),
        ("length_days", length_refusal, "length_refused"),
        ("capacity_effort_points", capacity_refusal, "capacity_refused"),
    )
    for name, refusal_of, code in checks:
        if name in values and (refusal := await refusal_of(session, values[name])) is not None:
            return ToolPreparationError(
                code, f"{refusal}.", "Tell the user this, in one line. Propose nothing."
            )
    return None


class SprintProposalHandler:
    entity = "sprint"
    version_model: type[Any] | None = None

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        session = context.session
        workspace = await require_workspace(session)
        values = dict(change.values)
        criteria = values.get("success_criteria")
        if change.action is ChangeAction.UPDATE:
            if (error := await _next_sprint_refusal(session, values)) is not None:
                raise error
        elif change.action is ChangeAction.CREATE:
            refusal = await start_refusal(
                session, criteria if criteria is not None else workspace.sprint_success_criteria
            )
            if refusal is not None:
                raise ToolPreparationError(
                    "start_refused",
                    f"{refusal}.",
                    "Tell the user why the Sprint cannot start and what to do first. "
                    "Propose nothing.",
                )
            if (error := await _next_sprint_refusal(session, values)) is not None:
                raise error
        elif change.action is ChangeAction.COMPLETE:
            if not workspace.active_sprint_id:
                raise ToolPreparationError(
                    "no_sprint",
                    "No Sprint is running.",
                    "Tell the user no Sprint is running. Propose nothing.",
                )
        else:
            raise ToolPreparationError(
                "unsupported_mode",
                f"The sprint tool has no mode {change.action.value}.",
                'Use mode "create", "update" or "complete".',
            )
        return PreparedChange(values=values, expected_version=None)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        criteria = change.values.get("success_criteria")
        if change.action is ChangeAction.COMPLETE:
            return [(await finish_sprint(session, reason=FINISHED_BY_HAND)).id]
        if criteria is not None:
            await set_sprint_success_criteria(session, criteria)
        if "length_days" in change.values:
            await set_sprint_length(session, change.values["length_days"])
        if "capacity_effort_points" in change.values:
            await set_sprint_capacity(session, change.values["capacity_effort_points"])
        if change.action is ChangeAction.UPDATE:
            return []
        workspace = await require_workspace(session)
        # As the Start button does: the Sprint takes what the workspace holds for it.
        sprint = await start_sprint(session, success_criteria=workspace.sprint_success_criteria)
        return [sprint.id]
