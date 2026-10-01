"""The sprint subagent: it starts and finishes the Sprint, writes the next one's Success
criteria, and answers what the Sprint is as it stands.

Every change it makes is a proposal that Save applies through the same operations the
Sprint screen's buttons call. What the Sprint is right now is not in its prompt: it is the
block after the conversation, read again at every step, so the prompt stays byte-stable.
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from tg_agent_shell.ai.contracts import AgentChange, ChangeAction, ToolInput
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.proposals.api import MutationToolSpec
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...constants import WEEKDAY_NAMES
from ...foundation.workspace import require_workspace
from ..cards.api import CardStage, actions_on_stages, effort_label
from ..profile.api import capacity_effort_points, effort_tracking_on, sprint_length_days
from .api import sprint_counts, sprint_day, sprint_metrics, start_refusal
from .model import Sprint

SPRINT_PROMPT = """You run the user's Sprint: you start it, finish it, and write the next Sprint's Success criteria. You also answer questions about the Sprint.

# What you know
The last message lists the Sprint as it stands now: the mode, its days, its Actions, its effort and capacity while Effort Points are on, and the Profile's Sprint length.
Answer a question about the Sprint from that message: its dates, its length, which day it is, how many days are left.
Read the Actions with `query_data` only when the question is about them.

# The `sprint` tool
- `mode="update"` with `success_criteria`: write the next Sprint's Success criteria. Only in Planning.
- `mode="create"`: start the next Sprint today. It runs for the Profile's Sprint length. Add `success_criteria` to write them in the same Save.
- `mode="complete"`: finish the running Sprint. Its open Actions keep their stage.
- Write the Success criteria in the user's own words.
- Write one short line naming what you propose, in the same response. The review screen shows the rest.

# What you cannot do
- Change the running Sprint's Success criteria: they were fixed when it started. Say so, and propose nothing.
- Change a Sprint's dates, pause it, extend it, or bring a finished one back. Say there is no way to.
- Change the Sprint length or the capacity: they are in the Profile. Say so.
- Move Actions into the Sprint or Today: say the Advisor does that with the workspace.

# Answering
Your answer goes to the user as you wrote it. Keep it short.

# Read the data
`query_data` runs one read-only `SELECT` over these views only.
Every value listed under a view is the lowercase code stored in that column.

{views}"""


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
            effort = sum(card.effort_points or 0 for card in planned)
            capacity = await capacity_effort_points(session)
            refusal = await start_refusal(session, criteria)
            lines += [
                "Mode: Planning. No Sprint is running.",
                f"Next Sprint's Success criteria: {criteria or 'not written yet'}",
                f"Planned: {len(planned)} Actions."
                + (f" Estimated load: {effort_label(effort)} EP. Capacity: {_capacity(capacity)}. "
                   f"Unestimated Actions: {sum(card.effort_points is None for card in planned)}."
                   if effort_tracking else ""),
                f"Sprint length in the Profile: {length} days. Started today, the Sprint "
                f"runs {today.isoformat()} – {(today + timedelta(days=length - 1)).isoformat()}.",
                "It can start now." if refusal is None else f"It cannot start now: {refusal}.",
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
            f"Sprint length in the Profile, for the next Sprint: {length} days.",
        ]
        return "\n".join(lines)


def _capacity(points: float | None) -> str:
    return f"{effort_label(points)} EP" if points is not None else "off"


SPRINT_AGENT = AgentSpec(
    name="sprint",
    purpose=(
        "start or finish the Sprint, write the next Sprint's Success criteria, or answer "
        "about the running Sprint: its dates, its length, its days left, its effort."
    ),
    instructions=SPRINT_PROMPT,
    mutation_tools=("sprint",),
    views=("ai_current_sprint", "ai_current_sprint_metrics", "ai_cards"),
    current=sprint_now,
    shown_as_is=True,
)


class SprintToolInput(ToolInput):
    """One change to the Sprint: start the next one, write its Success criteria, or finish
    the running one."""

    content_fields: ClassVar[frozenset[str]] = frozenset({"success_criteria"})

    mode: Literal["create", "update", "complete"] = Field(
        description=(
            "create starts the next Sprint today; update writes the next Sprint's Success "
            "criteria; complete finishes the running Sprint."
        )
    )
    success_criteria: str | None = Field(
        default=None,
        description=(
            "What the next Sprint must achieve, in the user's words. Required with update, "
            "optional with create."
        ),
    )


def _sprint_change(call: SprintToolInput) -> AgentChange:
    values = (
        {"success_criteria": call.success_criteria} if call.success_criteria is not None else {}
    )
    return AgentChange(entity="sprint", action=ChangeAction(call.mode), values=values)


SPRINT_TOOL = MutationToolSpec(
    name="sprint",
    input_model=SprintToolInput,
    description="Propose starting the next Sprint, writing its Success criteria, or finishing the running one.",
    to_change=_sprint_change,
)
