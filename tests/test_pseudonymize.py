from __future__ import annotations

import json

import pytest

from llm_gateway import (
    CompletionRequest,
    CompletionTurn,
    PseudonymizingProvider,
    ScriptedProvider,
    Span,
    ToolCall,
)


class Finder:
    """Finds every occurrence of the spellings it is given, except in the texts it is blind to."""

    def __init__(self, kinds: dict[str, str], blind: tuple[str, ...] = ()) -> None:
        self.kinds = kinds
        self.blind = blind
        self.calls: list[str] = []

    def find(self, text: str) -> list[Span]:
        self.calls.append(text)
        if text in self.blind:
            return []
        spans = []
        for surface, kind in self.kinds.items():
            start = text.find(surface)
            while start != -1:
                spans.append(Span(start, start + len(surface), kind))
                start = text.find(surface, start + len(surface))
        return spans


def _wrapped(finder, *turns: CompletionTurn) -> tuple[PseudonymizingProvider, ScriptedProvider]:
    inner = ScriptedProvider(turns or [CompletionTurn("Хорошо.")])
    return PseudonymizingProvider(inner, finder), inner


def _request(*messages: dict) -> CompletionRequest:
    return CompletionRequest(messages=tuple(messages))


async def test_ag_privacy_063_the_provider_reads_placeholders_numbered_by_kind():
    """AG-PRIVACY-063 — tests/brd/tg_agent_shell/agents.feature"""
    finder = Finder({"Анне": "PERSON", "Анны": "PERSON", "+7 916 123-45-67": "PHONE"})
    provider, inner = _wrapped(finder)

    await provider.complete(
        _request(
            {"role": "user", "content": "Позвони Анне на +7 916 123-45-67."},
            {"role": "assistant", "content": "Позвоню Анне."},
            {"role": "user", "content": "И спроси у Анны про отчёт."},
        )
    )

    sent = [message["content"] for message in inner.requests[0].messages]
    assert sent == [
        "Позвони [PERSON_1] на [PHONE_1].",
        "Позвоню [PERSON_1].",
        "И спроси у [PERSON_2] про отчёт.",
    ]


async def test_placeholders_keep_their_numbers_as_the_transcript_grows():
    finder = Finder({"Анне": "PERSON", "Лене": "PERSON"})
    provider, inner = _wrapped(finder, CompletionTurn("1"), CompletionTurn("2"))
    first = {"role": "user", "content": "Позвони Анне."}

    await provider.complete(_request(first))
    await provider.complete(
        _request(first, {"role": "assistant", "content": "1"}, {"role": "user", "content": "И Лене."})
    )

    assert inner.requests[0].messages[0] == inner.requests[1].messages[0]
    assert inner.requests[1].messages[2]["content"] == "И [PERSON_2]."


async def test_a_text_is_looked_through_once():
    finder = Finder({"Анне": "PERSON"})
    provider, _ = _wrapped(finder, CompletionTurn("1"), CompletionTurn("2"))
    first = {"role": "user", "content": "Позвони Анне."}

    await provider.complete(_request(first))
    await provider.complete(_request(first, {"role": "user", "content": "Ещё раз."}))

    assert finder.calls == ["Позвони Анне.", "Ещё раз."]


async def test_ag_privacy_064_the_answer_and_the_tool_arguments_get_the_real_words():
    """AG-PRIVACY-064 — tests/brd/tg_agent_shell/agents.feature"""
    finder = Finder({"Анне": "PERSON", 'ООО "Ромашка"': "ORGANIZATION"})
    arguments = '{"name": "[PERSON_1]", "company": "[ORGANIZATION_1]", "note": "[PERSON_9]"}'
    provider, _ = _wrapped(
        finder,
        CompletionTurn(
            "Напомню позвонить [PERSON_1], не [PERSON_9].",
            (ToolCall("call_1", "save", arguments),),
        ),
    )

    turn = await provider.complete(
        _request({"role": "user", "content": 'Позвони Анне из ООО "Ромашка".'})
    )

    assert turn.content == "Напомню позвонить Анне, не [PERSON_9]."
    assert json.loads(turn.tool_calls[0].arguments_json) == {
        "name": "Анне",
        "company": 'ООО "Ромашка"',
        "note": "[PERSON_9]",
    }


async def test_the_arguments_of_a_call_in_the_transcript_go_as_placeholders_in_valid_json():
    finder = Finder({"Анне": "PERSON"})
    provider, inner = _wrapped(finder)
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "save", "arguments": '{"text": "Позвонить Анне", "count": 2}'},
    }

    await provider.complete(
        _request(
            {"role": "user", "content": "Позвони Анне."},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "call_1", "content": "Сохранено."},
        )
    )

    sent = inner.requests[0].messages[1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(sent) == {"text": "Позвонить [PERSON_1]", "count": 2}


async def test_ag_privacy_065_a_name_found_once_is_hidden_where_it_recurs():
    """AG-PRIVACY-065 — tests/brd/tg_agent_shell/agents.feature"""
    found_in = "Анне 37 лет, позвони ей."
    missed_in = "Напомню Анне в 37 минут, Аннета подождёт."
    finder = Finder({"Анне": "PERSON", "37": "AGE"}, blind=(missed_in,))
    provider, inner = _wrapped(finder)

    await provider.complete(
        _request({"role": "user", "content": found_in}, {"role": "assistant", "content": missed_in})
    )

    sent = [message["content"] for message in inner.requests[0].messages]
    assert sent == [
        "[PERSON_1] [AGE_1] лет, позвони ей.",
        "Напомню [PERSON_1] в 37 минут, Аннета подождёт.",
    ]


async def test_ag_privacy_067_safwas_instructions_and_photos_go_as_they_are():
    """AG-PRIVACY-067 — tests/brd/tg_agent_shell/agents.feature"""
    finder = Finder({"Анна": "PERSON"})
    provider, inner = _wrapped(finder)
    instructions = {"role": "system", "content": "Ты Safwa. Пример: Анна позвонила."}
    photo = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QW5uYQ=="}}

    await provider.complete(
        _request(
            instructions,
            {"role": "user", "content": [{"type": "text", "text": "Это Анна."}, photo]},
        )
    )

    system, user = inner.requests[0].messages
    assert system == instructions
    assert user["content"] == [{"type": "text", "text": "Это [PERSON_1]."}, photo]


async def test_ag_privacy_068_a_failed_search_sends_nothing():
    """AG-PRIVACY-068 — tests/brd/tg_agent_shell/agents.feature"""

    class Broken:
        def find(self, text: str) -> list[Span]:
            raise RuntimeError("the finder is gone")

    provider, inner = _wrapped(Broken())

    with pytest.raises(RuntimeError, match="the finder is gone"):
        await provider.complete(_request({"role": "user", "content": "Позвони Анне."}))
    assert inner.requests == []
