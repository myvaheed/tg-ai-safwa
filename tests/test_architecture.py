"""The architecture rules of the migration, plus the two snapshots that guard drift.

Rules A-K live in `scripts/architecture_metrics.py` so the same scanner produces the batch
report.  `tests/architecture_allowlist.json` records what the codebase violated when Phase 0
was recorded: counts may fall, never rise.

Rules I and J are snapshots of built artefacts rather than of the source tree, so they are
here.  Regenerate a snapshot only inside a batch that is declared as changing that artefact:

    uv run pytest tests/test_architecture.py --snapshot-update
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, inspect

from safwa.ai.service import OPEN_TOOL, QUERY_SAFWA_TOOL, ROUTE_TOOL
from safwa.ai.subagents import PERSONA
from safwa.bootstrap.modules import PROPOSALS, SYSTEM_PROMPT
from safwa.features.board.agent import BOARD_PROMPT
from safwa.features.diary.agent import DIARY_PROMPT
from safwa.models import Base
from scripts.architecture_metrics import RULES, allowlist, cycles, violations

ALLOWLIST = Path(__file__).parent / "architecture_allowlist.json"
SNAPSHOTS = Path(__file__).parent / "snapshots"


def _allowed() -> dict[str, dict[str, int]]:
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))["rules"]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _snapshot(name: str, produced: dict[str, str], update: bool) -> None:
    path = SNAPSHOTS / f"{name}.json"
    if update or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(produced, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip(f"{name} snapshot written")
    recorded = json.loads(path.read_text(encoding="utf-8"))
    changed = sorted(
        key for key in recorded.keys() | produced.keys() if recorded.get(key) != produced.get(key)
    )
    assert not changed, f"{name} changed: {changed}"


# ------------------------------------------------------------------- Rules A-K


@pytest.mark.parametrize("rule", sorted(RULES))
def test_rule_has_no_violation_outside_the_allowlist(rule):
    allowed = _allowed().get(rule, {})
    found: dict[str, int] = {}
    for item in RULES[rule]():
        found[item.key()] = found.get(item.key(), 0) + 1

    grown = {key: count for key, count in found.items() if count > allowed.get(key, 0)}

    assert not grown, f"{rule} gained violations: {grown}"


def test_the_allowlist_has_no_entry_that_is_already_fixed():
    # A stale entry hides a rule that has started passing, so the ratchet has to be reset
    # in the batch that fixed it: `uv run python scripts/architecture_metrics.py`.
    current = allowlist()
    stale = {
        f"{rule}|{key}"
        for rule, counts in _allowed().items()
        for key, count in counts.items()
        if current.get(rule, {}).get(key, 0) < count
    }

    assert not stale, f"allowlist is behind the code: {sorted(stale)}"


def test_the_allowlist_covers_every_violation_the_scanner_reports():
    allowed = _allowed()
    uncovered = [item for item in violations() if item.key() not in allowed.get(item.rule, {})]

    assert not uncovered, f"unrecorded violations: {[str(item) for item in uncovered]}"


def test_internal_imports_stay_acyclic():
    # Plan section 21: the migration has to preserve this, not only the phase that moves
    # the module.  A cycle is what a badly placed seam looks like from the outside.
    found = cycles()

    assert not found, [" -> ".join([*loop, loop[0]]) for loop in found]


# ------------------------------------------------- Rule I: the prompt prefix is stable


def test_rule_i_prompt_prefix_is_byte_stable(request):
    # Everything a provider sees before the dialogue: the prompts and the tool schemas.
    # `_context_messages` orders the volatile blocks after it, so this is the whole of
    # what remote prompt caching can hit.
    produced = {
        "SYSTEM_PROMPT": _digest(SYSTEM_PROMPT),
        "PERSONA": _digest(PERSONA),
        "BOARD_PROMPT": _digest(BOARD_PROMPT),
        "DIARY_PROMPT": _digest(DIARY_PROMPT),
        "tool:open": _digest(json.dumps(OPEN_TOOL, sort_keys=True)),
        "tool:route": _digest(json.dumps(ROUTE_TOOL, sort_keys=True)),
        "tool:query_safwa": _digest(json.dumps(QUERY_SAFWA_TOOL, sort_keys=True)),
    }
    for name, tool in PROPOSALS.tools.items():
        produced[f"tool:{name}"] = _digest(json.dumps(tool.schema(), sort_keys=True))

    _snapshot("prompt_prefix", produced, request.config.getoption("--snapshot-update"))


# ------------------------------------------------------ Rule J: the schema is stable


def test_rule_j_schema_is_unchanged_outside_a_schema_batch(request):
    produced = {}
    for name, table in Base.metadata.tables.items():
        columns = [
            f"{column.name}:{column.type!s}:"
            f"{'null' if column.nullable else 'notnull'}:"
            f"{'pk' if column.primary_key else '-'}"
            for column in table.columns
        ]
        indexes = sorted(f"{index.name}({','.join(c.name for c in index.columns)})" for index in table.indexes)
        keys = sorted(
            f"{','.join(c.name for c in fk.columns)}->{fk.elements[0].target_fullname}"
            for fk in table.foreign_key_constraints
        )
        # A uniqueness rule is what a table refuses, so it is schema: "a Check hangs on one
        # Card" is one constraint, and dropping it would otherwise move no hash.
        unique = sorted(
            f"unique({','.join(c.name for c in constraint.columns)})"
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        )
        produced[name] = _digest("|".join([*columns, *indexes, *keys, *unique]))

    _snapshot("schema", produced, request.config.getoption("--snapshot-update"))


def test_the_declared_schema_is_what_a_fresh_database_gets(tmp_path):
    # `create_all` never alters an existing table, so the only guarantee the project has is
    # that a rebuilt database matches `models.py`.  This is that guarantee, asserted.
    from sqlalchemy import create_engine

    from safwa.foundation.database import upgrade_database

    url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    upgrade_database(url)
    engine = create_engine(url)
    try:
        built = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert set(Base.metadata.tables) <= built
