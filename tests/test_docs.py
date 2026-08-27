"""`CLAUDE.md` and `README.md` are read as if they were true, so a link to something that
is gone is worse than no link. Nothing checked them, and both had accumulated pointers to
files the migration deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from safwa.history import CITATION_TYPES

ROOT = Path(__file__).parent.parent

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# `[16.08.2026](diary:12)` is a citation Safwa writes, quoted in docs as an example.
CITATION = re.compile(rf"^({'|'.join(CITATION_TYPES)}):")

CHECKED = [
    ROOT / "CLAUDE.md",
    ROOT / "README.md",
    ROOT / "tests" / "brd" / "README.md",
    *sorted((ROOT / "docs").rglob("*.md")),
]


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
