"""The chat package is a package, and this is what makes that claim checkable.

Rule F already says no module under `src/telegram_llm/` imports Safwa. That is necessary and
not sufficient: a package can be free of an import and still be unusable without the
application it was cut out of. So the border is tested twice — by what the public names may
say, and by an example that runs a whole turn with no Safwa in the process at all.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from llm_gateway import CompletionTurn, ScriptedProvider
from telegram_llm import TELEGRAM_TEXT_LIMIT

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "telegram_llm"
EXAMPLE = ROOT / "examples" / "plain_chat_bot" / "bot.py"

# Safwa's vocabulary, plus the two deliveries the package must not know: which database the
# notes are in, and which model answers. A package that names any of them is not reusable.
FOREIGN = frozenset(
    {
        "advisor",
        "card",
        "cue",
        "diary",
        "proposal",
        "reminder",
        "safwa",
        "sprint",
        "sqlalchemy",
    }
)
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+")

# The package is the chat of an aiogram bot. Where the notes are kept and how the chat is
# read back are the two things it asks an application for, so neither the database Safwa
# keeps them in nor the user session Safwa reads through may be named here.
FOREIGN_IMPORTS = frozenset({"safwa", "sqlalchemy", "telethon"})

BOT_ID = 4242
PERSON_ID = 77


def _public_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(node.name)
            names.extend(argument.arg for argument in node.args.args)
            names.extend(argument.arg for argument in node.args.kwonlyargs)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [name for name in names if not name.startswith("_")]


def _words(name: str) -> set[str]:
    """A name's own words. `discard` is not about a Card, and a substring match says it is."""
    return {part.lower().rstrip("s") for part in _WORD.findall(name)}


@pytest.mark.parametrize("module", sorted(path.name for path in PACKAGE.glob("*.py")))
def test_the_vocabulary_of_the_package_belongs_to_no_application(module: str) -> None:
    foreign = [
        name
        for name in _public_names(PACKAGE / module)
        if _words(name) & FOREIGN
    ]
    assert not foreign, f"telegram_llm/{module} names {foreign}"


def test_no_module_in_the_package_imports_the_application() -> None:
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}: {alias.name}"
                    for alias in node.names
                    if alias.name.split(".")[0] in FOREIGN_IMPORTS
                ]
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in FOREIGN_IMPORTS:
                    offenders.append(f"{path.name}: {node.module}")
    assert not offenders, offenders


def test_the_example_imports_nothing_of_the_application() -> None:
    tree = ast.parse(EXAMPLE.read_text(encoding="utf-8"))
    imported = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "safwa" not in imported
    assert {"telegram_llm", "llm_gateway"} <= imported


def _load_example():
    specification = importlib.util.spec_from_file_location("plain_chat_bot", EXAMPLE)
    assert specification is not None and specification.loader is not None
    bot = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(bot)
    return bot


@dataclass
class FakeUser:
    id: int
    is_bot: bool = False


@dataclass
class FakeChat:
    id: int = 900


@dataclass
class FakeMessage:
    """As much of an aiogram message as the package touches, and nothing else."""

    message_id: int
    text: str
    from_user: FakeUser
    date: datetime
    telegram: FakeTelegram
    chat: FakeChat = field(default_factory=FakeChat)

    async def answer(self, text: str, reply_markup=None, parse_mode=None) -> FakeMessage:
        return self.telegram.mint(text, FakeUser(BOT_ID, is_bot=True))


@dataclass
class FakeTelegram:
    """One private chat, numbering its messages the way Telegram does."""

    next_id: int = 1
    minute: int = 0
    sent: list[FakeMessage] = field(default_factory=list)

    def mint(self, text: str, user: FakeUser) -> FakeMessage:
        self.next_id += 1
        self.minute += 1
        message = FakeMessage(
            message_id=self.next_id,
            text=text,
            from_user=user,
            date=datetime(2026, 8, 31, 10, tzinfo=UTC) + timedelta(minutes=self.minute),
            telegram=self,
        )
        self.sent.append(message)
        return message

    def says(self, text: str) -> FakeMessage:
        return self.mint(text, FakeUser(PERSON_ID))


async def test_the_example_answers_and_reads_its_own_answer_back() -> None:
    bot = _load_example()
    provider = ScriptedProvider(
        [CompletionTurn(content="Hello to you."), CompletionTurn(content="You said hello.")]
    )
    talker = bot.Talker(provider, BOT_ID)
    telegram = FakeTelegram()

    await talker.answer(telegram.says("hello"))
    await talker.answer(telegram.says("what did I say?"))

    said = [message.text for message in telegram.sent if message.from_user.is_bot]
    assert len(said) == 2
    # Nothing was remembered between the turns: the second request is the chat, read back.
    second = list(provider.requests[1].messages)
    assert second[0]["role"] == "system"
    spoken = [(item["role"], item["content"]) for item in second[1:]]
    assert any(role == "user" and "hello" in content for role, content in spoken)
    assert ("assistant", "Hello to you.") in spoken
    assert any(role == "user" and "what did I say?" in content for role, content in spoken)


async def test_the_example_splits_an_answer_too_long_for_one_message() -> None:
    bot = _load_example()
    long_answer = "word " * (TELEGRAM_TEXT_LIMIT // 2)
    talker = bot.Talker(ScriptedProvider([CompletionTurn(content=long_answer)]), BOT_ID)
    telegram = FakeTelegram()

    await talker.answer(telegram.says("say a lot"))

    parts = [message for message in telegram.sent if message.from_user.is_bot]
    assert len(parts) > 1
    assert all(len(part.text) <= TELEGRAM_TEXT_LIMIT + 200 for part in parts)
