"""The architecture rules, plus the two snapshots that guard drift.

The rules live in `scripts/architecture_metrics.py` so the same scanner produces the report
a batch attaches to its summary.  Every one of them reads zero, and the one exception any
rule grants is named inside that rule.

Rules I, J and O are snapshots of built artefacts rather than of the source tree, so they
are here.  Regenerate a snapshot only inside a batch that is declared as changing that
artefact:

    uv run pytest tests/test_architecture.py --snapshot-update
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, inspect

from safwa.ai.messages import ContextBuilder, StateBlocks
from safwa.ai.subagents import PERSONA, RoutedSubagent
from safwa.ai.tools import CALL_HELPER_TOOL, OPEN_TOOL, QUERY_SAFWA_TOOL, ROUTE_TOOL
from safwa.bootstrap.modules import AGENTS, HEAVY_ANALYZER_PROMPT, PROPOSALS, SYSTEM_PROMPT
from safwa.dialogue_marks import MARKS, RETIRED_MARK_CODES
from safwa.foundation.models import Base
from scripts.architecture_metrics import RULES, cycles
from telegram_llm import DialogueMessage

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


# The snapshot above is what the prompts *say*, which catches an edit to one.  What follows
# is where each block may go, which is the half a provider's cache actually reads: two turns
# share a prefix only while nothing volatile has been folded into it.


class _Facts:
    text = "The owner rests on Fridays."


class _Memory:
    async def sync(self) -> _Facts:
        return _Facts()


@asynccontextmanager
async def _no_session():
    yield None


def _builder(clock: str) -> ContextBuilder:
    async def workspace_state(session) -> StateBlocks:
        return StateBlocks(state="2 Cards open.", clock=clock)

    return ContextBuilder(
        _no_session,
        _Memory(),
        workspace_state,
        system_prompt="You keep the owner's Cards.",
        subagents={},
        cache_breakpoints=True,
    )


async def test_rule_i_the_clock_never_reaches_the_advisor_prefix():
    # Three turns, because `append_user_message` folds the state block into the first user
    # message: one turn collapses the whole list into two entries and asserts nothing.
    dialogue = [
        DialogueMessage(role="user", content="What is open?"),
        DialogueMessage(role="assistant", content="Two Cards."),
        DialogueMessage(role="user", content="Close the shopping one."),
    ]

    early = await _builder("Now: Tuesday 09:00.").advisor(dialogue)
    later = await _builder("Now: Friday 23:41.").advisor(dialogue)

    # The state block is what a folded clock would ride in on, so the prefix under test has
    # to still contain it or this passes by holding nothing.
    assert any("2 Cards open." in json.dumps(item) for item in early[:-1])
    assert early[:-1] == later[:-1]
    assert "23:41" not in json.dumps(later[:-1])
    assert early[-1] != later[-1]
    # A chat template that enforces this raises on any system block but the first.
    assert sum(1 for item in early if item["role"] == "system") == 1


async def test_rule_i_neither_the_clock_nor_a_receipt_reaches_a_routed_prefix():
    # A routed subagent breaks the cache after the prompt, so its prefix is `messages[0]`
    # alone: the conversation, what this turn already saved, and the clock all follow it.
    def subagent(clock: str) -> RoutedSubagent:
        return RoutedSubagent(
            name="cards",
            purpose="Change Cards.",
            instructions="Save what the owner asked for.",
            clock=lambda: clock,
        )

    builder = _builder("unread: this subagent asks for no workspace state")
    dialogue = [{"role": "user", "content": "Close the shopping Card."}]

    early = await builder.routed(subagent("Now: Tuesday 09:00."), dialogue, [])
    later = await builder.routed(subagent("Now: Friday 23:41."), dialogue, ["Saved Card #4."])

    assert early[0] == later[0]
    assert "23:41" not in json.dumps(later[0])
    assert "Saved Card #4." not in json.dumps(later[0])
    assert early[-1] != later[-1]


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


# ------------------------------------------- Rule O: the marker codes are append-only


def test_rule_o_marker_codes_are_unchanged(request):
    # The code is written into the message text and Telegram is the store, so a code that
    # changes hands re-labels every message already sent under it and nothing can migrate
    # them back.  Renumbering is therefore a schema change to a table that has no rows.
    produced = {kind: str(code) for kind, code in MARKS.codes.items()}

    _snapshot("marker_codes", produced, request.config.getoption("--snapshot-update"))


def test_rule_o_no_code_is_shared_or_revived():
    codes = list(MARKS.codes.values())

    assert len(codes) == len(set(codes))
    assert RETIRED_MARK_CODES.isdisjoint(codes)
