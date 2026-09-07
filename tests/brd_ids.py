"""The BRD identifiers, and the two files that carry them.

`tests/brd/README.md` says a scenario carries its identifier on its `Scenario:` line and a
test carries the same identifier on the first line of its docstring. Both are read here,
once, so the traceability tests and the feature map of `scripts/architecture_metrics.py`
see the same links rather than two parsers drifting apart.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SCENARIO_ID_TEXT = r"[A-Z]{2,3}-[A-Z-]+-\d{3}"
SCENARIO_ID = re.compile(rf"^(?P<id>{SCENARIO_ID_TEXT})\b")
SCENARIO_LINE = re.compile(rf"^\s*Scenario: (?P<id>{SCENARIO_ID_TEXT}) — (?P<wording>.+)$")

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
BRD = TESTS / "brd"


def feature_files() -> list[Path]:
    return sorted(BRD.rglob("*.feature"))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def titled_scenarios(path: Path) -> list[tuple[str, str]]:
    """The `Scenario:` lines of one file, as (identifier, wording), in file order."""
    found = []
    for line in read(path).splitlines():
        match = SCENARIO_LINE.match(line)
        if match:
            found.append((match.group("id"), match.group("wording").strip()))
    return found


def scenarios() -> dict[str, str]:
    """Every approved scenario identifier, and the feature file that carries it."""
    return {
        identifier: path.relative_to(REPO).as_posix()
        for path in feature_files()
        for identifier, _ in titled_scenarios(path)
    }


def citations() -> list[tuple[str, str]]:
    """Each test citing a scenario, as (docstring first line, module path and test name)."""
    out: list[tuple[str, str]] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(read(path), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            docstring = ast.get_docstring(node, clean=True)
            if not docstring:
                continue
            first = docstring.splitlines()[0].strip()
            if SCENARIO_ID.match(first):
                out.append((first, f"{path.relative_to(REPO).as_posix()}::{node.name}"))
    return out


def cited_tests() -> dict[str, list[str]]:
    """Which tests cite each identifier. A citation is a claim, not proof of coverage."""
    found: dict[str, list[str]] = {}
    for first, test in citations():
        found.setdefault(SCENARIO_ID.match(first).group("id"), []).append(test)
    return found
