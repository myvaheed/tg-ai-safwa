"""The `.feature` files are a contract only if something checks they are still connected.

`tests/brd/README.md` says a test carries its scenario identifier in its docstring and the
`.feature` file preserves the approved wording. Nothing enforced either, so a renamed
scenario, a deleted test or a mistyped identifier all stayed green. These tests are what
makes the traceability real; they are also the reason no separate BDD runner is needed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
BRD = TESTS / "brd"
README = BRD / "README.md"

SCENARIO = re.compile(r"^\s*Scenario: (?P<id>[A-Z]{2,3}-[A-Z-]+-\d{3}) — ")
IDENTIFIER = re.compile(r"^(?P<id>[A-Z]{2,3}-[A-Z-]+-\d{3})\b")
CITATION = re.compile(r"^(?P<id>[A-Z]{2,3}-[A-Z-]+-\d{3}) — (?P<feature>tests/brd/[a-z_]+\.feature)$")


def approved_prefixes() -> set[str]:
    table = README.read_text(encoding="utf-8-sig")
    return set(re.findall(r"\|\s*`([A-Z]{2,3})`\s*\|", table))


def scenarios() -> dict[str, str]:
    """Every approved scenario identifier, and the feature file that carries it."""
    found: dict[str, str] = {}
    for path in sorted(BRD.glob("*.feature")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = SCENARIO.match(line)
            if match:
                found[match.group("id")] = f"tests/brd/{path.name}"
    return found


def citations() -> list[tuple[str, str, str]]:
    """Each test that cites a scenario, as (identifier, feature file, test name)."""
    out: list[tuple[str, str, str]] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            docstring = ast.get_docstring(node, clean=True)
            if not docstring:
                continue
            first = docstring.splitlines()[0].strip()
            if IDENTIFIER.match(first):
                out.append((first, path.name, node.name))
    return out


def test_every_approved_scenario_has_at_least_one_test():
    cited = {IDENTIFIER.match(first).group("id") for first, _, _ in citations()}

    uncovered = sorted(set(scenarios()) - cited)

    assert not uncovered, f"approved scenarios with no test: {uncovered}"


def test_every_cited_identifier_names_an_approved_scenario():
    known = scenarios()

    unknown = sorted(
        {
            (IDENTIFIER.match(first).group("id"), test)
            for first, _, test in citations()
            if IDENTIFIER.match(first).group("id") not in known
        }
    )

    assert not unknown, f"tests citing no approved scenario: {unknown}"


def test_a_citation_names_the_feature_file_that_carries_the_scenario():
    known = scenarios()
    wrong = []

    for first, module, test in citations():
        match = CITATION.fullmatch(first)
        if match is None:
            wrong.append(f"{module}::{test} — docstring is {first!r}, want 'ID — tests/brd/x.feature'")
        elif known.get(match.group("id")) != match.group("feature"):
            wrong.append(f"{module}::{test} — cites {match.group('feature')}, scenario is in "
                         f"{known.get(match.group('id'))}")

    assert not wrong, wrong


def test_every_scenario_prefix_is_declared_in_the_readme():
    approved = approved_prefixes()

    undeclared = sorted({identifier.split("-")[0] for identifier in scenarios()} - approved)

    assert not undeclared, f"prefixes missing from tests/brd/README.md: {undeclared}"


@pytest.mark.parametrize("path", sorted(BRD.glob("*.feature")), ids=lambda path: path.name)
def test_a_feature_file_numbers_each_scenario_once_per_identifier(path: Path):
    """Two Scenario blocks may share an identifier — one rule, two observable cases — but
    the wording after the identifier has to differ, or one of them is a copy."""
    titles = [
        line.split("Scenario: ", 1)[1].strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if SCENARIO.match(line)
    ]

    assert len(titles) == len(set(titles)), f"repeated scenario titles in {path.name}"


@pytest.mark.parametrize("path", sorted(BRD.glob("*.feature")), ids=lambda path: path.name)
def test_a_feature_file_carries_no_gherkin_tags(path: Path):
    """No BDD runner reads these files, so a `@tag` is a lowercase second copy of the
    identifier that nothing keeps in step. The Scenario line is where the identifier is."""
    tagged = [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip().startswith("@")
    ]

    assert not tagged, f"{path.name} carries tags nothing reads: {tagged}"
