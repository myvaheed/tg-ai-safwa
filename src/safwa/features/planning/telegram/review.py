"""How a proposal about the Sprint reads to the owner.

A Sprint about to start is shown the way the Planning screen would start it: its Success
criteria, its first and last day and its length, and the plan against the capacity. One
about to finish is shown with the day it is on and what stays open.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.proposals.api import ProposalChange, ProposalScreen
from tg_agent_shell.proposals.model import ChangeAction

from ....foundation.workspace import Workspace, require_workspace
from ...cards.api import CardStage, actions_on_stages
from ...profile.api import capacity_effort_points
from ..api import sprint_day, sprint_metrics
from ..model import Sprint
from ..use_cases import sprint_length_days
from .sprint import plan_cost

_SUMMARIES = {
    ChangeAction.UPDATE: "Write the next Sprint's Success criteria",
    ChangeAction.CREATE: "Start the next Sprint",
    ChangeAction.COMPLETE: "Finish the running Sprint",
}


def _criteria_lines(values: dict) -> list[str]:
    criteria = values.get("success_criteria")
    return [f"Success criteria: {criteria}"] if criteria else []


def _today(workspace: Workspace) -> date:
    return utcnow().astimezone(ZoneInfo(workspace.timezone)).date()


class SprintProposalPresenter:
    entity = "sprint"

    def raw_details(self, change: AgentChange) -> list[str]:
        return _criteria_lines(change.values)

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        return _criteria_lines(change.values)

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        return _SUMMARIES.get(change.action, "Change the Sprint")

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        change = changes[0]
        workspace = await require_workspace(session)
        today = _today(workspace)
        criteria = change.values.get("success_criteria")
        if change.action is ChangeAction.UPDATE:
            was = workspace.sprint_success_criteria.strip() or "not written yet"
            return ProposalScreen(
                mode="Edit",
                item="Success criteria",
                blocks=(f"Now: {html.escape(was)}\nBecomes: {html.escape(criteria or '')}",),
            )
        if change.action is ChangeAction.CREATE:
            length = await sprint_length_days(session)
            planned = await actions_on_stages(session, CardStage.SPRINT, CardStage.TODAY)
            cost, warning = plan_cost(planned, await capacity_effort_points(session))
            last = today + timedelta(days=length - 1)
            return ProposalScreen(
                mode="Start",
                item=f"{length}-day Sprint",
                blocks=(
                    "Success criteria: "
                    + html.escape(criteria or workspace.sprint_success_criteria.strip()),
                    f"{today.isoformat()} – {last.isoformat()}, {length} days",
                    f"Planned: {cost}" + (f"\n{warning}" if warning else ""),
                ),
            )
        sprint = (
            await session.get(Sprint, workspace.active_sprint_id)
            if workspace.active_sprint_id
            else None
        )
        if sprint is None:
            return None
        day, days = sprint_day(sprint, today)
        metrics = await sprint_metrics(session, sprint.id)
        still_open = await actions_on_stages(session, CardStage.SPRINT, CardStage.TODAY)
        return ProposalScreen(
            mode="Finish",
            item=f"Sprint {sprint.number}",
            blocks=(
                f"{sprint.planned_start_date} – {sprint.planned_end_date}, day {day} of {days}",
                f"Committed {metrics['committed']} · Added {metrics['added']} · "
                f"Done {metrics['completed']}",
                f"{len(still_open)} Actions are still open. They keep their stage.",
            ),
        )
