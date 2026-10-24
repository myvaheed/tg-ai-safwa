"""A short session that prepares a call reads the conversation as its caller did, in the example
application (AG-SESSION-063).

The ledger's own handler is wrapped to keep what each preparation was handed; the session is
the shell's, with a terminal of the test's own. Nothing here imports Safwa.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent_turns import mutation_turn
from wallet import app
from wallet.ledger.module import MODULE as LEDGER
from wallet.world import ledger_world
from wallet_harness import entry_script, seed_lists, take_a_turn

from llm_gateway import ScriptedProvider
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.conversation import conversation_note
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session
from tg_agent_shell.registry import Registry

REQUEST = "I spent 12.50 on lunch out of Cash"
CONVERSATION = f"<Conversation>\n<User>{REQUEST}</User>\n</Conversation>"


class Recording:
    """The ledger's handler, keeping the conversation each preparation was handed."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.conversations: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.handler, name)

    async def prepare(self, context: Any, change: Any) -> Any:
        self.conversations.append(context.conversation)
        return await self.handler.prepare(context, change)


class Read(ToolInput):
    """The words were read."""


READ = TerminalTool("read", "The words were read.", Read)


async def test_ag_session_063_a_call_is_prepared_with_the_conversation_its_session_read(
    wallet_bot, monkeypatch
):
    """AG-SESSION-063 — tests/brd/tg_agent_shell/agents.feature"""
    recording = Recording(LEDGER.proposals[0].handler)
    ledger = replace(LEDGER, proposals=(replace(LEDGER.proposals[0], handler=recording),))
    modules = tuple(ledger if module is LEDGER else module for module in app.MODULES)
    monkeypatch.setattr(app, "REGISTRY", Registry.of(modules, world=ledger_world))
    seeding = await wallet_bot.start()
    ids = await seed_lists(seeding.sessions)
    running = await wallet_bot.start(*entry_script(ids, answer="Done."))

    await take_a_turn(running, REQUEST)

    [conversation] = recording.conversations
    assert f">{REQUEST}</User>" in conversation
    # The bookkeeper's step that made the call: the block it read is the one preparation gets.
    read = [message["content"] for message in running.provider.requests[2].messages]
    assert any(conversation_note(conversation) in str(content) for content in read)


async def test_ag_session_063_a_short_session_reads_the_conversation_before_its_context():
    """AG-SESSION-063 — tests/brd/tg_agent_shell/agents.feature"""
    provider = ScriptedProvider([mutation_turn(("read", {})), mutation_turn(("read", {}))])

    await run_mini_session(
        provider,
        system_prompt="Read the words.",
        context="Words: lunch",
        terminals=(READ,),
        max_tool_calls=1,
        conversation=CONVERSATION,
    )
    await run_mini_session(
        provider,
        system_prompt="Read the words.",
        context="Words: lunch",
        terminals=(READ,),
        max_tool_calls=1,
    )

    with_conversation, without = (request.messages for request in provider.requests)
    assert with_conversation == (
        {"role": "system", "content": "Read the words."},
        {"role": "user", "content": f"{conversation_note(CONVERSATION)}\nWords: lunch"},
    )
    assert without == (
        {"role": "system", "content": "Read the words."},
        {"role": "user", "content": "Words: lunch"},
    )
