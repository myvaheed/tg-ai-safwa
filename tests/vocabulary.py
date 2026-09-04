"""What a package's public names are, and what words one of those names is made of.

A package that claims to be reusable is one whose vocabulary names no application, and the
test that says so is the same test in each of them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+")


def public_names(path: Path) -> list[str]:
    """Every class, function, argument and annotated attribute a module offers by name."""
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


def words(name: str) -> set[str]:
    """A name's own words. `discard` is not about a Card, and a substring match says it is."""
    return {part.lower().rstrip("s") for part in _WORD.findall(name)}
