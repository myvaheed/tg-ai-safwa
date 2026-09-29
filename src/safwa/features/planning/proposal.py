"""How a proposal about the Sprint is checked and then saved.

There is no Sprint to edit field by field: Save starts the next one, writes its Success
criteria, or finishes the running one, through the same operations the Sprint screen's
buttons call, with the same refusals. Preparing asks those refusals first, so the model
hears one before the owner is shown a screen.
"""

from __future__ import annotations

from typing import Any

from tg_agent_shell.proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
)
from tg_agent_shell.proposals.model import ChangeAction

from ...foundation.workspace import require_workspace
from .api import criteria_refusal, start_refusal
from .use_cases import (
    FINISHED_BY_HAND,
    finish_sprint,
    set_sprint_success_criteria,
    start_sprint,
)


class SprintProposalHandler:
    entity = "sprint"
    version_model: type[Any] | None = None

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        session = context.session
        workspace = await require_workspace(session)
        values = dict(change.values)
        criteria = values.get("success_criteria")
        if change.action is ChangeAction.UPDATE:
            refusal = await criteria_refusal(session, criteria or "")
            if refusal is not None:
                raise ToolPreparationError(
                    "criteria_refused",
                    f"{refusal}.",
                    "Tell the user this, in one line. Propose nothing.",
                )
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
        if change.action is ChangeAction.UPDATE:
            return []
        workspace = await require_workspace(session)
        # As the Start button does: the Sprint takes the criteria the workspace holds.
        sprint = await start_sprint(session, success_criteria=workspace.sprint_success_criteria)
        return [sprint.id]
