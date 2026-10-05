"""The profile subagent: it sets the Profile's fields in words and answers what they hold.

Its one tool writes any of the fields the Profile screen edits, and Save stores each through
the same check the screen's prompts use. The switches of the automatic reactions are not
among them: it reads them, to say whether one is on, and says they are switched on the
Profile → Hooks. What the Profile holds is the block after the conversation, read again at
every step.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, model_validator

from tg_agent_shell.ai.contracts import AgentChange, ChangeAction, ToolInput
from tg_agent_shell.proposals.api import MutationToolSpec
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...foundation.workspace import require_workspace
from .api import hook_switched_on
from .model import ProfileField, UserProfile

PROFILE_PROMPT = """You keep the user's Profile. You change its fields and answer what they hold.

# What you know
The last message lists every Profile field as it is now, by the name the `profile` tool gives it, then the timezone and each automatic reaction with its switch.
Answer a question about the Profile from that message.

# The `profile` tool
- One call with every field the user asked to change.
- A text field is replaced whole. To add to it, write the text it holds now, then the new words.
- Write one short line naming what you propose, in the same response. The review screen shows the rest.

# What you cannot do
- Switch an automatic reaction on or off: say whether it is on now, and that its switch is on its own screen through ⚙️ Profile → 🔔 Hooks. Propose nothing.
- Change the timezone.

# Answering
Your answer goes to the user as you wrote it. Keep it short."""


def _shown(profile: UserProfile, field: ProfileField) -> str:
    value = getattr(profile, field.value)
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
        "the Morning, Diary and daily summary times, the Diary instruction, Home after, "
        "Time tracking, Effort Points; or asks to switch an automatic reaction."
    ),
    instructions=PROFILE_PROMPT,
    mutation_tools=("profile",),
    current=profile_now,
    answers_questions=True,
)


class ProfileToolInput(ToolInput):
    """The Profile fields to change, each with its new value."""

    content_fields: ClassVar[frozenset[str]] = frozenset(
        {"about_me", "advisor_instructions", "diary_instructions"}
    )

    about_me: str | None = Field(default=None, description="What Safwa should know about the user.")
    advisor_instructions: str | None = Field(
        default=None, description="Standing instructions for Safwa."
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
        if not self.model_fields_set:
            raise ValueError("a Profile change needs at least one field")
        return self


def _profile_change(call: ProfileToolInput) -> AgentChange:
    return AgentChange(
        entity="profile", action=ChangeAction.UPDATE, values=call.model_dump(exclude_unset=True)
    )


PROFILE_TOOL = MutationToolSpec(
    name="profile",
    input_model=ProfileToolInput,
    description="Propose new values for one or more Profile fields.",
    to_change=_profile_change,
)
