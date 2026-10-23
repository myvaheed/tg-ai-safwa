"""The workspace mutator: the one session that proposes every change to the workspace.

The workspace is what the owner keeps — Cards, Checks, Values, Tags and Requests. Reminders
and the Remind on an item have their own subagent, so this one never names a time. This
package is that subagent and nothing else: the roster lets it declare mutation tools the
features that own those entities publish.
"""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import AgentSpec

from ...constants import INBOX_TAG_NAME, PROPOSING_TURN, READING_THE_WORKSPACE

MUTATOR_TOOLS = ("goal", "action", "check", "value", "tag", "request", "remove")


MUTATOR_PROMPT = f"""You keep the user's workspace: their Cards, Checks, Values, Tags and Requests.

{{items}}

{PROPOSING_TURN}

# Proposing
- Propose only what was asked. When the choice is the user's, cite the item instead of guessing it.
- New Cards go to `backlog`. Use `sprint` or `today` only when the user says so.
- Effort Points on in the workspace state: give every new Action `effort_points`.
- Effort Points off: never send `effort_points`.
- Fill in what you are sure of; omit the rest. Never invent an id such as 0 or 1.
- All calls for one item go together, one call per mode: every field in one update.
- Starting or finishing a Sprint and its Success criteria are not yours.

# Inbox
An Action can hold a note, captured idea or draft. Use Tag "{INBOX_TAG_NAME}" to capture these without extra detail.

{READING_THE_WORKSPACE}"""


MUTATOR_AGENT = AgentSpec(
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
)
