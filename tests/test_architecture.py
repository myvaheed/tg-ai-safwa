"""The architecture rules, plus the two snapshots that guard drift.

The rules live in `scripts/architecture_metrics.py` so the same scanner produces the report
a batch attaches to its summary.  Every one of them reads zero, and the one exception any
rule grants is named inside that rule.

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

from safwa.ai.subagents import PERSONA
from safwa.ai.tools import CALL_HELPER_TOOL, OPEN_TOOL, QUERY_SAFWA_TOOL, ROUTE_TOOL
from safwa.bootstrap.modules import AGENTS, HEAVY_ANALYZER_PROMPT, PROPOSALS, SYSTEM_PROMPT
from safwa.foundation.models import Base
from scripts.architecture_metrics import RULES, cycles

SNAPSHOTS = Path(__file__).parent / "snapshots"


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


# ------------------------------------------------------------------- Rules A-M


@pytest.mark.parametrize("rule", sorted(RULES))
def test_rule_has_no_violation(rule):
    found = [str(item) for item in RULES[rule]()]

    assert not found, f"{rule}: {found}"


def test_internal_imports_stay_acyclic():
    # A cycle is what a badly placed seam looks like from the outside.
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
        "HEAVY_ANALYZER_PROMPT": _digest(HEAVY_ANALYZER_PROMPT),
        "tool:open": _digest(json.dumps(OPEN_TOOL, sort_keys=True)),
        "tool:route": _digest(json.dumps(ROUTE_TOOL, sort_keys=True)),
        "tool:query_safwa": _digest(json.dumps(QUERY_SAFWA_TOOL, sort_keys=True)),
        "tool:call_helper": _digest(json.dumps(CALL_HELPER_TOOL, sort_keys=True)),
    }
    # The instructions as assembled, not as written: `{views}` is filled in at import
    # time, so the raw constant is not what any subagent reads.
    for agent in AGENTS:
        produced[f"agent:{agent.name}"] = _digest(agent.instructions)
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
