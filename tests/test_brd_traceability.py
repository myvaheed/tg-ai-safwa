"""The `.feature` files are a contract only if something checks they are still connected.

`tests/brd/README.md` says a test carries its scenario identifier in its docstring and the
`.feature` file preserves the approved wording. Nothing enforced either, so a renamed
scenario, a deleted test or a mistyped identifier all stayed green. These tests are what
makes the traceability real; they are also the reason no separate BDD runner is needed.

The files themselves are read by `brd_ids.py`, which the feature map reads through too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from brd_ids import (
    SCENARIO_ID,
    SCENARIO_ID_TEXT,
    citations,
    feature_files,
    read,
    scenarios,
    titled_scenarios,
)

TESTS = Path(__file__).parent
BRD = TESTS / "brd"
README = BRD / "README.md"
SAFWA_FEATURES = TESTS.parent / "src" / "safwa" / "features"

CITATION = re.compile(
    rf"^(?P<id>{SCENARIO_ID_TEXT}) — "
    r"(?P<feature>tests/brd/(?:[a-z_]+/)?[a-z_]+\.feature)$"
)
SCENARIO_REFERENCE = re.compile(rf"\b{SCENARIO_ID_TEXT}\b")


def approved_prefixes() -> set[str]:
    table = README.read_text(encoding="utf-8-sig")
    return set(re.findall(r"\|\s*`([A-Z]{2,3})`\s*\|", table))


def product_feature_packages() -> set[str]:
    return {
        path.name
        for path in SAFWA_FEATURES.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    }


def product_feature_files() -> set[str]:
    return {path.stem for path in BRD.glob("*.feature")}


def test_every_approved_scenario_has_at_least_one_test():
    cited = {SCENARIO_ID.match(first).group("id") for first, _ in citations()}

    uncovered = sorted(set(scenarios()) - cited)

    assert not uncovered, f"approved scenarios with no test: {uncovered}"


def test_every_scenario_reference_names_an_approved_scenario():
    known = scenarios()
    unknown = sorted(
        {
            (match.group(), path.relative_to(TESTS.parent).as_posix())
            for path in feature_files()
            for match in SCENARIO_REFERENCE.finditer(read(path))
            if match.group() not in known
        }
    )

    assert not unknown, f"scenario references with no approved scenario: {unknown}"


def test_every_cited_identifier_names_an_approved_scenario():
    known = scenarios()

    unknown = sorted(
        {
            (SCENARIO_ID.match(first).group("id"), test)
            for first, test in citations()
            if SCENARIO_ID.match(first).group("id") not in known
        }
    )

    assert not unknown, f"tests citing no approved scenario: {unknown}"


def test_a_citation_names_the_feature_file_that_carries_the_scenario():
    known = scenarios()
    wrong = []

    for first, test in citations():
        match = CITATION.fullmatch(first)
        if match is None:
            wrong.append(f"{test} — docstring is {first!r}, want 'ID — tests/brd/x.feature'")
        elif known.get(match.group("id")) != match.group("feature"):
            wrong.append(f"{test} — cites {match.group('feature')}, scenario is in "
                         f"{known.get(match.group('id'))}")

    assert not wrong, wrong


def test_every_scenario_prefix_is_declared_in_the_readme():
    approved = approved_prefixes()

    undeclared = sorted({identifier.split("-")[0] for identifier in scenarios()} - approved)

    assert not undeclared, f"prefixes missing from tests/brd/README.md: {undeclared}"


def test_every_safwa_feature_package_has_one_scenario_file():
    packages, scenario_files = product_feature_packages(), product_feature_files()
    missing = sorted(packages - scenario_files)
    orphaned = sorted(scenario_files - packages)

    assert not missing, f"Safwa packages with no scenario file: {missing}"
    assert not orphaned, f"scenario files with no Safwa package: {orphaned}"


@pytest.mark.parametrize("path", feature_files(), ids=lambda path: path.name)
def test_a_feature_file_numbers_each_scenario_once_per_identifier(path: Path):
    """Two Scenario blocks may share an identifier — one rule, two observable cases — but
    the wording after the identifier has to differ, or one of them is a copy."""
    titles = titled_scenarios(path)

    assert len(titles) == len(set(titles)), f"repeated scenario titles in {path.name}"


@pytest.mark.parametrize("path", feature_files(), ids=lambda path: path.name)
def test_a_feature_file_carries_no_gherkin_tags(path: Path):
    """No BDD runner reads these files, so a `@tag` is a lowercase second copy of the
    identifier that nothing keeps in step. The Scenario line is where the identifier is."""
    tagged = [line.strip() for line in read(path).splitlines() if line.strip().startswith("@")]

    assert not tagged, f"{path.name} carries tags nothing reads: {tagged}"
