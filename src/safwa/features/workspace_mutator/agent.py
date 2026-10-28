"""The workspace mutator: the one session that proposes every change to the workspace.

The workspace is what the owner keeps — Cards, Checks, Values, Tags and Requests. Reminders
and the Remind on an item already made have their own subagent; a new item is proposed with
its Remind. This package is that subagent and nothing else: the roster lets it declare
mutation tools the features that own those entities publish.
"""

from __future__ import annotations

from dataclasses import replace

from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...constants import PROPOSING_TURN, READING_THE_WORKSPACE
from ..profile.api import thoughts_by_default_on
from .state import close_items

MUTATOR_TOOLS = ("goal", "action", "thought", "check", "value", "tag", "request", "remove")

_CREATE_REQUESTED = "- Otherwise, create the requested type; use a Thought to keep a note, idea or draft."
_CREATE_THOUGHT = "- Otherwise, create a Thought unless the user explicitly asks for an Action, Goal, Value or Check."

MUTATOR_PROMPT = f"""You keep the user's workspace: their Cards, Checks, Values, Tags and Requests.

{{items}}

{PROPOSING_TURN}

# Proposing
- Propose only what was asked. When the choice is the user's, cite the item instead of guessing it.
- "I did …" about an open Action: complete it; about a Check: passed.
- A repeating Action or Check has one open copy; Save opens the next one. "I did it 3 times": one call with times=3.
- New Actions go to `backlog`. Use `sprint` or `today` only when the user says so.
- Effort Points on in the workspace state: give every new Action `effort_points`.
- Effort Points off: never send `effort_points`.
- Fill in what you are sure of; omit the rest. Never invent an id such as 0 or 1.
- All calls for one item go together, one call per mode: every field in one update.
- Starting or finishing a Sprint and its Success criteria are not yours.

# Thoughts
- Update existing items with their current type.
- Create Tags and Requests as requested.
- Direct Create or an accepted Blueprint plan: create every requested item with the type its content calls for.
{_CREATE_REQUESTED}

{READING_THE_WORKSPACE}"""


def thoughts_default_prompt(prompt: str) -> str:
    return prompt.replace(_CREATE_REQUESTED, _CREATE_THOUGHT)


class _MutatorAgentSpec(AgentSpec):
    __slots__ = ()

    def bind(self, context: AgentContext, *, prompt: str) -> RoutedSubagent:
        agent = super().bind(context, prompt=prompt)

        async def selected_prompt() -> str:
            async with context.sessions() as session:
                on = await thoughts_by_default_on(session)
            return thoughts_default_prompt(prompt) if on else prompt

        return replace(agent, prompt=selected_prompt)


MUTATOR_AGENT = _MutatorAgentSpec(
    name="workspace_mutator",
    purpose="create, update or delete a Card, Check, Value, Tag or Request; archive a Card or Check only.",
    instructions=MUTATOR_PROMPT,
    # What it changes, and what it judges a change against. The Diary is not the workspace's,
    # and neither is the log of what has already happened.
    views=(
        "ai_cards",
        "ai_checks",
        "ai_tags",
        "ai_values",
        "ai_requests",
        "ai_current_sprint",
    ),
    mutation_tools=MUTATOR_TOOLS,
    workspace_state=True,
    # The open items the owner is talking about, so the one meant needs no read.
    current=close_items,
)
