"""Every document here is read as if it were true, so a link or a name that is gone is
worse than none. This checks each one that `CHECKED` names, `docs/` included.
"""

from __future__ import annotations

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
    ROOT / "tests" / "brd" / "README.md",
    *sorted((ROOT / "docs").rglob("*.md")),
]

TICKED = re.compile(r"`([^`\n]+)`")
# A name only code would carry: snake_case, CamelCase, a dotted attribute or call, or a
# module path. Ordinary backticked prose — `today`, `backlog` — is not one and is skipped.
CODE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*(\(\))?$")
MODULE_PATH = re.compile(r"^[a-z_][a-z0-9_]*(/[a-z_][a-z0-9_]*)*\.py$")
SOURCE = ("src", "tests", "scripts")
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


@pytest.mark.parametrize("doc", CHECKED, ids=lambda doc: str(doc.relative_to(ROOT)))
def test_every_link_names_something_that_exists(doc: Path) -> None:
    missing = [
        href
        for href in LINK.findall(doc.read_text(encoding="utf-8"))
        if not href.startswith(("http://", "https://", "mailto:", "#"))
        and not CITATION.match(href)
        and not (doc.parent / href.split("#")[0]).exists()
    ]

    assert not missing, f"{doc.relative_to(ROOT)} links to nothing: {sorted(set(missing))}"


@pytest.mark.parametrize("doc", CHECKED, ids=lambda doc: str(doc.relative_to(ROOT)))
def test_every_code_name_a_document_spells_still_exists(doc: Path) -> None:
    """A renamed class or a moved module leaves the prose around it standing and wrong.

    A dotted name is checked by its last segment, which is the half a rename moves.
    """
    corpus, paths = written_names(), source_paths()
    gone = []
    for ticked in dict.fromkeys(TICKED.findall(doc.read_text(encoding="utf-8"))):
        name = ticked.strip()
        if MODULE_PATH.match(name):
            # On a segment boundary: `history.py` must not be answered by `telegram_history.py`.
            if not any(path == name or path.endswith(f"/{name}") for path in paths):
                gone.append(name)
        elif CODE_NAME.match(name) and ("_" in name or not name.islower()):
            last = name.removesuffix("()").rsplit(".", 1)[-1]
            if last not in ABSENT_ON_PURPOSE and not re.search(rf"\b{last}\b", corpus):
                gone.append(name)

    assert not gone, f"{doc.relative_to(ROOT)} names what the code does not: {sorted(set(gone))}"
