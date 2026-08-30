"""A whole application on the agent runtime, in one file and with no Safwa in it.

A note keeper. The model may search the notes, which happens inside the turn, and it may
write one, which does not: a written note waits for a person to say yes. The turn stops
there and hands back an `InteractionRef`; when the person says yes, `resume` answers the
call the session was waiting on and it carries on to its own last word. That is the same
suspension Safwa uses for its review screens, and it is the reason the runtime is a package
rather than part of Safwa — nothing here is a database, a chat client, or a proposal.

Run it: `uv run python examples/plain_chat_bot/bot.py`
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent_runtime import (
    AgentDefinition,
    AgentLoopResult,
    AgentManager,
    AgentSession,
    InMemorySessionStore,
    Resumption,
    ToolOutcome,
    TurnOutcome,
)
from llm_gateway import CompletionTurn, ScriptedProvider, ToolCall

SEARCH_NOTES = {
    "type": "function",
    "function": {
        "name": "search_notes",
        "description": "Find notes whose text contains a word.",
        "parameters": {
            "type": "object",
            "properties": {"word": {"type": "string"}},
            "required": ["word"],
        },
    },
}

WRITE_NOTE = {
    "type": "function",
    "function": {
        "name": "write_note",
        "description": "Write one note. The person has to say yes before it is kept.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}


class Notebook:
    """The application's own state. Everything a note is lives here and nowhere else."""

    def __init__(self) -> None:
        self.notes: list[str] = []
        self.waiting: str | None = None
        # The call the person is being asked about. The runtime knows it only as an id.
        self.waiting_call: str = ""

    def approve(self) -> tuple[str, str]:
        """Keep the note that was shown, and say which call that answers."""
        call, note = self.waiting_call, self.waiting or ""
        self.notes.append(note)
        self.waiting, self.waiting_call = None, ""
        return call, note


class NoteTools:
    """The runtime's `ToolRunner`: what this application does when a session calls a tool."""

    def __init__(self, notebook: Notebook) -> None:
        self.notebook = notebook

    def definition(self, kind: str) -> AgentDefinition:
        return AgentDefinition(kind=kind, tools=(SEARCH_NOTES, WRITE_NOTE))

    def is_immediate(self, agent: AgentSession, name: str) -> bool:
        return name == "search_notes"

    async def run(self, agent: AgentSession, call: ToolCall) -> ToolOutcome:
        arguments = json.loads(call.arguments_json or "{}")
        if call.name == "search_notes":
            word = str(arguments.get("word", "")).lower()
            found = [note for note in self.notebook.notes if word in note.lower()]
            return ToolOutcome(result={"found": found})
        text = str(arguments.get("text", "")).strip()
        if not text:
            return ToolOutcome(result={"error": "A note needs text."})
        return ToolOutcome(result={"status": "waiting"}, change=text)

    def route_target(self, call: ToolCall) -> tuple[str | None, dict[str, Any] | None]:
        return None, {"error": "This bot has no subagents to route to."}

    def refuse_mixed(self) -> dict[str, Any]:
        return {"error": "Write a note on its own, after you have read what you need."}

    def prepared_message(self) -> str:
        return "I wrote a note for you to look at."

    def repair_exhausted_message(self) -> str:
        return "I could not write that note."


class OneSystemPrompt:
    """The runtime's `ContextSource`: what a session reads before its own steps."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt

    async def messages_for(
        self,
        kind: str,
        dialogue: list[dict[str, Any]],
        prior_receipts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.prompt}, *dialogue]


class NoteReview:
    """The runtime's `Materializer`: a written note is something a person decides."""

    def __init__(self, notebook: Notebook) -> None:
        self.notebook = notebook

    async def materialize(
        self, agent: AgentSession, result: AgentLoopResult
    ) -> TurnOutcome | None:
        written = [tool for tool in result.pending_tools if tool.change is not None]
        if not written:
            return TurnOutcome(message=result.message)
        self.notebook.waiting = written[0].change
        self.notebook.waiting_call = written[0].call.id
        return TurnOutcome(message=result.message, waiting=True, payload=written[0].change)


def _call(name: str, **arguments: Any) -> CompletionTurn:
    return CompletionTurn(
        content="",
        tool_calls=(
            ToolCall(id=f"call-{name}", name=name, arguments_json=json.dumps(arguments)),
        ),
    )


async def main() -> None:
    notebook = Notebook()
    notebook.notes.append("Bread, milk, coffee")
    provider = ScriptedProvider(
        [
            _call("search_notes", word="coffee"),
            CompletionTurn(content="You already have one about coffee: Bread, milk, coffee."),
            _call("write_note", text="Ask about the roast"),
            CompletionTurn(content="Kept it, next to the coffee one."),
        ]
    )
    runtime = AgentManager(
        InMemorySessionStore(),
        provider,
        NoteTools(notebook),
        OneSystemPrompt("You keep notes for one person. Search before you write."),
        NoteReview(notebook),
        max_tool_calls=8,
        max_repair_rounds=2,
        child_deadline_seconds=30.0,
    )

    answered = await runtime.handle([{"role": "user", "content": "Do I have a note on coffee?"}])
    print("bot:", answered.message)

    proposed = await runtime.handle([{"role": "user", "content": "Note: ask about the roast"}])
    print("bot:", proposed.message, "->", proposed.payload)
    print("waiting for a yes:", notebook.waiting)

    # The person says yes. The runtime is handed back the reference it stopped on and the
    # result the note-writing call was waiting for, and the same session carries on.
    call, note = notebook.approve()
    assert proposed.ref is not None
    answered = await runtime.resume(
        proposed.ref, Resumption(results={call: {"status": "kept", "note": note}})
    )
    print("bot:", answered.message if answered else "(nothing to resume)")
    print("notes:", notebook.notes)


if __name__ == "__main__":
    asyncio.run(main())
