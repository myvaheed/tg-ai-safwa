"""Every document here is read as if it were true, so a link or a name that is gone is
worse than none. This checks each one that `CHECKED` names, `docs/` included.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

import pytest

from safwa.bootstrap.modules import SCREENS

ROOT = Path(__file__).parent.parent

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# `[16.08.2026](diary:12)` is a citation Safwa writes, quoted in docs as an example.
CITATION = re.compile(rf"^({'|'.join(SCREENS.types)}):")

CHECKED = [
    ROOT / "CLAUDE.md",
    ROOT / "README.md",
    *sorted((ROOT / "tests").rglob("README.md")),
    *sorted((ROOT / "docs").rglob("*.md")),
]

TICKED = re.compile(r"(?<!`)`(?!`)(.+?)(?<!`)`(?!`)", re.DOTALL)
# A name only code would carry: snake_case, CamelCase, a dotted attribute or call, or a
# module path. Ordinary backticked prose — `today`, `backlog` — is not one and is skipped.
CODE_NAME = re.compile(
    r"(?<![A-Za-z0-9_.@])(?P<name>[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?)(?![A-Za-z0-9_.])"
)
MODULE_PATH = re.compile(
    r"(?<![A-Za-z0-9_/*])(?P<path>(?:[a-z_][a-z0-9_]*/)*[a-z_][a-z0-9_]*\.py)"
    r"(?![A-Za-z0-9_/])"
)
SCENARIO_ID = re.compile(r"\b[A-Z]{2,3}-[A-Z-]+-\d{3}\b")
PLACEHOLDER = re.compile(r"<[A-Z]+>")
SOURCE = ("src", "tests", "scripts", "examples")
# `UseCaseBase` is named by the Definition of Done to say the codebase must not have one.
ABSENT_ON_PURPOSE = frozenset({"UseCaseBase"})


@cache
def written_names() -> str:
    """Everything the codebase spells: its sources, its settings and its example env."""
    files = [path for folder in SOURCE for path in (ROOT / folder).rglob("*.py")]
    files += [ROOT / "pyproject.toml", ROOT / ".env.example"]
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in files
        if "__pycache__" not in path.parts and path.exists()
    )


@cache
def source_paths() -> frozenset[str]:
    return frozenset(
        path.relative_to(ROOT).as_posix()
        for folder in SOURCE
        for path in (ROOT / folder).rglob("*.py")
    )


def missing_links(source: Path, text: str) -> list[str]:
    missing = []
    for href in LINK.findall(text):
        target, _anchor, _fragment = href.partition("#")
        if (
            href.startswith(("http://", "https://", "mailto:", "#"))
            or CITATION.match(href)
            or not target
            or (source.parent / target).exists()
        ):
            continue
        missing.append(href)
    return missing


def missing_module_paths(text: str, paths: frozenset[str]) -> list[str]:
    missing = []
    for match in MODULE_PATH.finditer(text):
        path = match.group("path")
        if path.split("/", 1)[0] in SOURCE:
            exists = path in paths
        else:
            exists = any(item == path or item.endswith(f"/{path}") for item in paths)
        if not exists:
            missing.append(path)
    return missing


def missing_code_references(text: str, corpus: str, paths: frozenset[str]) -> list[str]:
    missing = []
    for code in TICKED.findall(text):
        missing.extend(missing_module_paths(code, paths))

        without_paths = PLACEHOLDER.sub(" ", SCENARIO_ID.sub(" ", MODULE_PATH.sub(" ", code)))
        for match in CODE_NAME.finditer(without_paths):
            name = match.group("name")
            if "_" not in name and name.islower():
                continue
            last = name.removesuffix("()").rsplit(".", 1)[-1]
            if last not in ABSENT_ON_PURPOSE and not re.search(rf"\b{last}\b", corpus):
                missing.append(name)
    return missing


def source_docstrings(source: Path) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
    nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return [
        docstring
        for node in ast.walk(tree)
        if isinstance(node, nodes)
        if (docstring := ast.get_docstring(node, clean=False)) is not None
    ]


@pytest.mark.parametrize("doc", CHECKED, ids=lambda doc: str(doc.relative_to(ROOT)))
def test_every_link_names_something_that_exists(doc: Path) -> None:
    missing = missing_links(doc, doc.read_text(encoding="utf-8"))

    assert not missing, f"{doc.relative_to(ROOT)} links to nothing: {sorted(set(missing))}"


@pytest.mark.parametrize("doc", CHECKED, ids=lambda doc: str(doc.relative_to(ROOT)))
def test_every_code_name_a_document_spells_still_exists(doc: Path) -> None:
    """A renamed class or a moved module leaves the prose around it standing and wrong.

    A dotted name is checked by its last segment, which is the half a rename moves.
    """
    gone = missing_code_references(doc.read_text(encoding="utf-8"), written_names(), source_paths())

    assert not gone, f"{doc.relative_to(ROOT)} names what the code does not: {sorted(set(gone))}"


@pytest.mark.parametrize(
    "source",
    [
        path
        for folder in SOURCE
        for path in sorted((ROOT / folder).rglob("*.py"))
        if "__pycache__" not in path.parts
    ],
    ids=lambda source: str(source.relative_to(ROOT)),
)
def test_explicit_docstring_links_and_paths_exist(source: Path) -> None:
    missing_links_in_docstrings = []
    missing_paths_in_docstrings = []
    for docstring in source_docstrings(source):
        missing_links_in_docstrings.extend(missing_links(source, docstring))
        missing_paths_in_docstrings.extend(missing_module_paths(docstring, source_paths()))

    assert not missing_links_in_docstrings, (
        f"{source.relative_to(ROOT)} docstrings link to nothing: "
        f"{sorted(set(missing_links_in_docstrings))}"
    )
    assert not missing_paths_in_docstrings, (
        f"{source.relative_to(ROOT)} docstrings name paths that do not exist: "
        f"{sorted(set(missing_paths_in_docstrings))}"
    )


def test_links_with_anchors_still_check_their_target(tmp_path: Path) -> None:
    assert missing_links(tmp_path / "note.md", "[missing](gone.md#part)") == ["gone.md#part"]


def test_multiline_inline_code_checks_each_name_and_exact_path() -> None:
    missing_name = "No" + "SuchSymbol"
    missing = missing_code_references(
        f"`ContextBuilder,\n{missing_name}, src/safwa/history.py`",
        written_names(),
        source_paths(),
    )

    assert set(missing) == {missing_name, "src/safwa/history.py"}


def test_ordinary_prose_is_not_treated_as_a_code_reference() -> None:
    prose = "No" + "SuchSymbol is only prose."
    assert not missing_code_references(prose, written_names(), source_paths())
