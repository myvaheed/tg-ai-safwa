"""The runtime is a package, and this is what makes that claim checkable.

Rule F already says no module under `src/agent_runtime/` imports Safwa. That is necessary
and not sufficient: a package can be free of an import and still be unusable without the
application it was cut out of. So the border is tested twice — by what the public names may
say, and by an example that runs the whole loop with no Safwa in the process at all.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "agent_runtime"
EXAMPLE = ROOT / "examples" / "plain_chat_bot" / "bot.py"

# What a public name in the runtime may never be about. Two are Safwa's vocabulary and two
# are the delivery it must not know: a package that names any of them is not reusable.
FOREIGN = ("proposal", "aiogram", "sqlalchemy", "safwa", "telegram", "pydantic")


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


@pytest.mark.parametrize("module", ["model.py", "ports.py"])
def test_the_vocabulary_of_the_package_belongs_to_no_application(module: str) -> None:
    foreign = [
        name
        for name in _public_names(PACKAGE / module)
        if any(word in name.lower() for word in FOREIGN)
    ]
    assert not foreign, f"agent_runtime/{module} names {foreign}"


def test_no_module_in_the_package_imports_the_application() -> None:
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}: {alias.name}"
                    for alias in node.names
                    if alias.name.split(".")[0] == "safwa"
                ]
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("safwa"):
                offenders.append(f"{path.name}: {node.module}")
    assert not offenders, offenders


async def test_a_bot_that_is_not_safwa_runs_the_whole_loop() -> None:
    """The example is the proof: one search inside the turn, one change that waits."""
    specification = importlib.util.spec_from_file_location("plain_chat_bot", EXAMPLE)
    assert specification is not None and specification.loader is not None
    bot = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(bot)

    notebook = bot.Notebook()
    notebook.notes.append("Bread, milk, coffee")
    provider = bot.ScriptedProvider(
        [
            bot._call("search_notes", word="coffee"),
            bot.CompletionTurn(content="You have one already."),
            bot._call("write_note", text="Ask about the roast"),
        ]
    )
    runtime = bot.AgentManager(
        bot.InMemorySessionStore(),
        provider,
        bot.NoteTools(notebook),
        bot.OneSystemPrompt("You keep notes."),
        bot.NoteReview(notebook),
        max_tool_calls=8,
        max_repair_rounds=2,
        child_deadline_seconds=30.0,
    )

    answered = await runtime.handle([{"role": "user", "content": "Any note on coffee?"}])
    assert answered.message == "You have one already."
    assert not answered.waiting

    proposed = await runtime.handle([{"role": "user", "content": "Note: ask about the roast"}])
    assert proposed.waiting
    assert notebook.waiting == "Ask about the roast"
    assert notebook.notes == ["Bread, milk, coffee"]

    notebook.approve()
    assert notebook.notes == ["Bread, milk, coffee", "Ask about the roast"]
