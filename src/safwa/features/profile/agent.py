"""The profile subagent: it sets the Profile's fields in words and answers what they hold.

Its one tool writes any of the fields the Profile screen edits, and Save stores each through
the same check the screen's prompts use. The switches of the automatic reactions are not
among them: it reads them, to say whether one is on, and says they are switched on the
Profile → Hooks. What the Profile holds is the block after the conversation, read again at
every step.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from tg_agent_shell.ai.contracts import AgentChange, ChangeAction, ToolInput
from tg_agent_shell.proposals.api import MutationToolSpec
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...foundation.workspace import require_workspace
from .api import hook_switched_on
from .model import ProfileField, UserProfile

PROFILE_PROMPT = """You keep the user's Profile: what they tell Safwa outright. You change its fields and answer what they hold.

# What you know
The last message lists every Profile field as it is now, by the name the `profile` tool gives it, then the timezone and each automatic reaction with its switch.
Answer a question about the Profile from that message.

# The `profile` tool
- One call, with every field the user asked to change, and `mode` "update".
- A time is HH:MM, like 08:00.
- `sprint_length_days` and `home_after_minutes` are whole numbers.
- `capacity_effort_points` is a number of effort points; null turns the capacity off.
- `time_tracking` is true or false.
- `effort_tracking` is true or false; it switches Effort Points and their capacity warnings.
- A text field is replaced whole. To add to it, write the text it holds now, then the new words.
- Write one short line naming what you propose, in the same response. The review screen shows the rest.

# What you cannot do
- Switch an automatic reaction on or off: say whether it is on now, and that its switch is on its own screen through ⚙️ Profile → 🔔 Hooks. Propose nothing.
- Change the timezone.

# Answering
Your answer goes to the user as you wrote it. Keep it short."""


def _shown(profile: UserProfile, field: ProfileField) -> str:
    value = getattr(profile, field.value)
    if field is ProfileField.CAPACITY_EFFORT_POINTS:
        return "off" if value is None else f"{value:g}"
    if field in {ProfileField.TIME_TRACKING, ProfileField.EFFORT_TRACKING}:
        return "on" if value else "off"
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value) if value not in (None, "") else "empty"


async def profile_now(context: AgentContext) -> str:
    """Every Profile field as it is, the timezone, and each reaction's switch."""
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        workspace = await require_workspace(session)
        lines = ["The Profile now:"]
        if profile is not None:
            lines += [f"- {field.value}: {_shown(profile, field)}" for field in ProfileField]
        lines.append(f"Timezone: {workspace.timezone}. It is not changed here.")
        if context.switches:
            lines.append("Automatic reactions, switched only on the Profile screen:")
            lines += [
                f"- {hook.title}: "
                + ("on" if await hook_switched_on(session, hook.name) else "off")
                for hook in context.switches
            ]
        return "\n".join(lines)


PROFILE_AGENT = AgentSpec(
    name="profile",
    purpose=(
        "change a Profile field or answer what it holds: About me, Advisor instructions, "
        "Sprint length and capacity, the Morning, Diary and daily summary times, the Diary "
        "instruction, Home after, Time tracking, Effort Points; or asks to switch an automatic reaction."
    ),
    instructions=PROFILE_PROMPT,
    mutation_tools=("profile",),
    current=profile_now,
    shown_as_is=True,
)


class ProfileToolInput(ToolInput):
    """The Profile fields to change, each with its new value."""

    content_fields: ClassVar[frozenset[str]] = frozenset(
        {"about_me", "advisor_instructions", "diary_instructions"}
    )
    semantic_null_fields: ClassVar[frozenset[str]] = frozenset({"capacity_effort_points"})

    mode: Literal["update"]
    about_me: str | None = Field(default=None, description="What Safwa should know about the user.")
    advisor_instructions: str | None = Field(
        default=None, description="Standing instructions for Safwa."
    )
    sprint_length_days: int | None = Field(
        default=None, description="Days the next Sprint runs."
    )
    capacity_effort_points: float | None = Field(
        default=None, description="Effort points one Sprint holds; null turns it off."
    )
    morning_time: str | None = Field(
        default=None, description="HH:MM when the morning checks run."
    )
    diary_time: str | None = Field(
        default=None, description="HH:MM when Safwa offers to write the day up."
    )
    diary_instructions: str | None = Field(
        default=None, description="A standing instruction for the Diary."
    )
    summary_time: str | None = Field(
        default=None, description="HH:MM when Safwa sums up the day."
    )
    home_after_minutes: int | None = Field(
        default=None,
        description="Minutes of quiet before the chat is cleared down to the Home dashboard.",
    )
    time_tracking: bool | None = Field(
        default=None, description="Whether the user records the time an Action took."
    )
    effort_tracking: bool | None = Field(
        default=None, description="Whether the user uses Effort Points to estimate load."
    )

    @model_validator(mode="after")
    def names_a_field(self) -> ProfileToolInput:
        if not self.model_fields_set - {"mode"}:
            raise ValueError("a Profile change needs at least one field")
        return self


def _profile_change(call: ProfileToolInput) -> AgentChange:
    values = call.model_dump(exclude_unset=True)
    values.pop("mode", None)
    return AgentChange(entity="profile", action=ChangeAction.UPDATE, values=values)


PROFILE_TOOL = MutationToolSpec(
    name="profile",
    input_model=ProfileToolInput,
    description="Propose new values for one or more Profile fields.",
    to_change=_profile_change,
)
