"""The budget coach: it talks a budget through with the user, and writes nothing."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import AgentSpec

COACH_PROMPT = """You talk the user's budget through with them, over several messages.
Ask one question at a time, in one or two sentences.
The wallets and their balances are in the workspace state.
When the user is done, call `finish` with what you agreed, in a few sentences."""

COACH = AgentSpec(
    name="coach",
    title="💬 Budget talk",
    purpose="talk a budget through with the user, over several messages.",
    instructions=COACH_PROMPT,
    workspace_state=True,
)
