"""The sprint subagent: it starts and finishes the Sprint, and sets the next one's Success
criteria, length and capacity. What is not its own it hands back with `nothing_to_do`.

Every change it makes is a proposal that Save applies through the same operations the
Sprint screen's buttons call. What the Sprint is right now is not in its prompt: it is the
block after the conversation, read again at every step, so the prompt stays byte-stable.
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from tg_agent_shell.ai.contracts import AgentChange, ChangeAction, ToolInput
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.proposals.api import MutationToolSpec
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...constants import WEEKDAY_NAMES
from ...foundation.workspace import require_workspace
from ..cards.api import CardStage, actions_on_stages, effort_label
from ..profile.api import effort_tracking_on
from .api import (
    capacity_effort_points,
    plan_load,
    sprint_counts,
    sprint_day,
    sprint_length_days,
    sprint_metrics,
    start_refusal,
)
from .model import Sprint

SPRINT_PROMPT = """You run the user's Sprint: you start it, finish it, and set the next Sprint's Success criteria, length and capacity.
The last message lists the Sprint as it stands now.

# The `sprint` tool
- `mode="update"`: set the next Sprint's `success_criteria`, `length_days` or `capacity_effort_points`, one or more. Only in Planning.
- `length_days` is a whole number of days. `capacity_effort_points` is a number of effort points, only while Effort Points are on; null turns it off.
- `mode="create"`: start the next Sprint today. Add any of those fields to set them in the same Save.
- `mode="complete"`: finish the running Sprint. Its open Actions keep their stage.
- Write the Success criteria in the user's own words.
- Write one short line naming what you propose, in the same response.

# Not yours
Call nothing_to_do with one sentence why when the request is:
- a change to the running Sprint: its Success criteria, length, capacity or dates; pausing or extending it;
- bringing a finished Sprint back;
- moving Actions."""


async def sprint_now(context: AgentContext) -> str:
    """The Sprint as it stands, for this step: the running one, or what starting one today
    would be."""
    async with context.sessions() as session:
        workspace = await require_workspace(session)
        tz = ZoneInfo(workspace.timezone)
        now = utcnow().astimezone(tz)
        today = now.date()
        length = await sprint_length_days(session)
        effort_tracking = await effort_tracking_on(session)
        lines = [
            f"Today is {today.isoformat()} ({WEEKDAY_NAMES[today.weekday()]}), local time "
            f"now {now:%H:%M}, timezone {workspace.timezone}."
        ]
        sprint = (
            await session.get(Sprint, workspace.active_sprint_id)
            if workspace.active_sprint_id
            else None
        )
        if sprint is None:
            criteria = workspace.sprint_success_criteria.strip()
            planned = await actions_on_stages(session, CardStage.SPRINT, CardStage.TODAY)
            load = await plan_load(session, planned, start_date=today, end_date=today + timedelta(days=length - 1))
            effort = load.effort
            capacity = await capacity_effort_points(session)
            refusal = await start_refusal(session, criteria)
            lines += [
                "Mode: Planning. No Sprint is running.",
                f"Next Sprint's Success criteria: {criteria or 'not written yet'}",
                f"Planned: {load.actions} Actions."
                + (f" Estimated load: {effort_label(effort)} EP. Capacity: {_capacity(capacity)}. "
                   f"Unestimated Actions: {load.unestimated}."
                   if effort_tracking else ""),
                f"Next Sprint's length: {length} days. Started today, it "
                f"runs {today.isoformat()} – {(today + timedelta(days=length - 1)).isoformat()}.",
                "It can start now." if refusal is None else f"It cannot start now: {refusal}.",
                *(["Schedule quantities are unknown for some Actions; these totals are lower bounds."]
                  if load.unknown_schedules else []),
            ]
            return "\n".join(lines)
        counts = await sprint_counts(session, sprint.id)
        metrics = await sprint_metrics(session, sprint.id) if effort_tracking else counts
        unit = "EP" if effort_tracking else "Actions"
        day, days = sprint_day(sprint, today)
        left = days - day
        lines += [
            f"Mode: Sprint. Sprint {sprint.number} runs {sprint.planned_start_date.isoformat()} "
            f"– {sprint.planned_end_date.isoformat()}, {days} days.",
            f"Today is day {day} of {days}. Days left after today: {max(left, 0)}."
            + (" Today is its last day." if left == 0 else ""),
            f"Success criteria: {sprint.success_criteria}",
            ("Effort: " if effort_tracking else "Actions: ")
            + ", ".join(
                f"{name} {effort_label(metrics[key])} {unit}"
                for name, key in (
                    ("committed", "committed"),
                    ("added", "added"),
                    ("removed", "removed"),
                    ("done", "completed"),
                )
            )
            + ".",
            *([f"Capacity it started with: {_capacity(sprint.capacity_effort_points)}.",
               f"Unestimated Actions: {counts['unestimated']}."] if effort_tracking else []),
            *(["Schedule quantities are unknown for some Actions; these totals are lower bounds."]
              if counts["unknown_schedules"] else []),
        ]
        return "\n".join(lines)


def _capacity(points: float | None) -> str:
    return f"{effort_label(points)} EP" if points is not None else "off"


SPRINT_AGENT = AgentSpec(
    name="sprint",
    purpose=(
        "start or finish the Sprint, or set the next Sprint's Success criteria, length or capacity."
    ),
    instructions=SPRINT_PROMPT,
    mutation_tools=("sprint",),
    current=sprint_now,
)


class SprintToolInput(ToolInput):
    """One change to the Sprint: start the next one, set its Success criteria, length or
    capacity, or finish the running one."""

    content_fields: ClassVar[frozenset[str]] = frozenset({"success_criteria"})
    semantic_null_fields: ClassVar[frozenset[str]] = frozenset({"capacity_effort_points"})

    mode: Literal["create", "update", "complete"] = Field(
        description=(
            "create starts the next Sprint today; update sets the next Sprint's Success "
            "criteria, length or capacity; complete finishes the running Sprint."
        )
    )
    success_criteria: str | None = Field(
        default=None,
        description="What the next Sprint must achieve, in the user's words.",
    )
    length_days: int | None = Field(default=None, description="Days the next Sprint runs.")
    capacity_effort_points: float | None = Field(
        default=None, description="Effort points the next Sprint holds; null turns it off."
    )

    @model_validator(mode="after")
    def update_names_a_field(self) -> SprintToolInput:
        if self.mode == "update" and not self.model_fields_set - {"mode"}:
            raise ValueError("update needs success_criteria, length_days or capacity_effort_points")
        return self


def _sprint_change(call: SprintToolInput) -> AgentChange:
    values = call.model_dump(exclude_unset=True)
    values.pop("mode", None)
    return AgentChange(entity="sprint", action=ChangeAction(call.mode), values=values)


SPRINT_TOOL = MutationToolSpec(
    name="sprint",
    input_model=SprintToolInput,
    description="Propose starting the next Sprint, setting its Success criteria, length or capacity, or finishing the running one.",
    to_change=_sprint_change,
)
