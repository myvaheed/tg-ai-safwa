"""The architecture rules, plus the two snapshots that guard drift.

The rules live in `scripts/architecture_metrics.py` so the same scanner produces the report
a batch attaches to its summary.  Every one of them reads zero, and the one exception any
rule grants is named inside that rule.

Rules I, J and O are snapshots of built artefacts rather than of the source tree, so they
are here.  Regenerate one by naming its own test, never the whole file, so a batch cannot
re-baseline the two it did not change:

    uv run pytest tests/test_architecture.py::test_rule_i_prompt_prefix_is_byte_stable --snapshot-update
"""

from __future__ import annotations

import ast
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from brd_ids import cited_tests
from sqlalchemy import UniqueConstraint, inspect
from vocabulary import public_names, words

from safwa.bootstrap.modules import (
    AGENTS,
    ALLOWED_VIEWS,
    HELPERS,
    PROPOSALS,
    SCREENS,
    SYSTEM_PROMPT,
)
from safwa.features.advisor.agent import PERSONA
from safwa.foundation.models import Base
from scripts.architecture_metrics import (
    BUSINESS_FILES,
    RULES,
    SRC,
    Module,
    agent_domain_calls,
    business_imports,
    cycles,
    feature_map,
    process_modules,
    readers,
    unregistered_packages,
)
from telegram_llm import DialogueMessage, KindMarks, code_for
from tg_agent_shell.ai.contracts import (
    CALL_HELPER_TOOL,
    QUERY_TOOL,
    ROUTE_TOOL,
    open_tool,
)
from tg_agent_shell.ai.messages import ContextBuilder, StateBlocks
from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.foundation.models import Base as ShellBase

SNAPSHOTS = Path(__file__).parent / "snapshots"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _snapshot(name: str, produced: dict[str, str], update: bool) -> None:
    path = SNAPSHOTS / f"{name}.json"
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(produced, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip(f"{name} snapshot written")
    assert path.exists(), (
        f"{name} snapshot is missing; create it explicitly by naming this test with "
        "--snapshot-update"
    )
    recorded = json.loads(path.read_text(encoding="utf-8"))
    changed = sorted(
        key for key in recorded.keys() | produced.keys() if recorded.get(key) != produced.get(key)
    )
    assert not changed, f"{name} changed: {changed}"


def test_a_missing_snapshot_fails_without_an_explicit_update(monkeypatch, tmp_path):
    monkeypatch.setattr("test_architecture.SNAPSHOTS", tmp_path)

    with pytest.raises(AssertionError, match="snapshot is missing"):
        _snapshot("missing", {}, update=False)

    assert not (tmp_path / "missing.json").exists()


def test_snapshot_update_is_the_only_way_to_create_a_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr("test_architecture.SNAPSHOTS", tmp_path)

    with pytest.raises(pytest.skip.Exception):
        _snapshot("created", {"value": "digest"}, update=True)

    assert json.loads((tmp_path / "created.json").read_text(encoding="utf-8")) == {
        "value": "digest"
    }


# ------------------------------------------------------------------- Rules A-M


@pytest.mark.parametrize("rule", sorted(RULES))
def test_rule_has_no_violation(rule):
    found = [str(item) for item in RULES[rule].check()]

    assert not found, f"{rule} ({RULES[rule].name}): {found}"


def _example(rel: str, source: str) -> Module:
    """One module as the scanner sees it, parsed from a snippet rather than read."""
    return Module(SRC / rel, ast.parse(source))


# A rule that reads zero proves nothing on its own: it reads zero when it works and when
# it looks in the wrong place. These are the smallest examples of each.


@pytest.mark.parametrize(
    "source, caught",
    [
        ("from ...config import Settings", True),
        ("import aiogram", True),
        ("from ..cards.telegram.screens import card_screen", True),
        ("from tg_agent_shell.hooks.registry import HookRegistry", True),
        ("from .model import DiaryEntry", False),
        ("from ...foundation.database import Database", False),
    ],
)
def test_rule_a_reads_the_settings_module_where_it_lives(source: str, caught: bool):
    found = business_imports(_example("safwa/features/diary/use_cases.py", source))

    assert bool(found) is caught, found


@pytest.mark.parametrize(
    "source, caught",
    [
        ("from .use_cases import create_diary_entry", True),
        ("from . import use_cases", True),
        ("import safwa.features.diary.use_cases as writes", True),
        # A feature whose operations are two modules is still the operations layer: Cards
        # keeps its derived-value walk in `hierarchy.py`, and an agent may not reach it.
        ("from ..cards.hierarchy import propagate_ancestors", True),
        ("from .model import DiaryEntry", False),
        ("from .api import day_read_tool", False),
    ],
)
def test_rule_k_catches_a_domain_call_however_it_is_imported(source: str, caught: bool):
    found = agent_domain_calls(_example("safwa/features/diary/agent.py", source))

    assert bool(found) is caught, found


def test_a_second_operations_module_is_under_the_business_rules_too():
    # Cards keeps its derived-value walk in `hierarchy.py`. Rule A reaches a file by name,
    # so a feature that splits its operations out of `use_cases.py` leaves that rule
    # unless the name is in the list.
    scanned = {module.rel for module in process_modules(*BUSINESS_FILES)}

    assert "safwa/features/cards/hierarchy.py" in scanned


def test_rule_r_asks_only_about_safwa_feature_packages():
    # `proposals` is registered from `tg_agent_shell`, so it is no Safwa package and is
    # never one of these names; `advisor` is a Safwa package that registers nothing.
    assert unregistered_packages(["diary", "advisor"]) == []
    assert unregistered_packages(["diary", "unplugged"]) == ["unplugged"]


def test_internal_imports_stay_acyclic():
    # A cycle is what a badly placed seam looks like from the outside.
    found = cycles()

    assert not found, [" -> ".join([*loop, loop[0]]) for loop in found]


# ------------------------------------------------------------------- the feature map


def test_publishing_a_view_reaches_no_reader_whose_own_list_leaves_it_out():
    who = readers()

    assert set(who) == set(ALLOWED_VIEWS)
    # A Card's history is deliberately the Diary's and the heavy analyzer's, not the
    # Advisor's, and publishing it beside `ai_cards` is what does not change that.
    assert who["ai_card_events"] == ("diary", "heavy_analyzer")
    assert "advisor" in who["ai_cards"]


def test_the_map_names_the_scenarios_of_a_feature_and_the_tests_citing_them():
    printed = feature_map("diary")

    assert "## Scenarios — tests/brd/diary.feature" in printed
    assert "DI-DAY-001" in printed
    for test in cited_tests()["DI-DAY-001"]:
        assert test in printed
    # The views half is both directions: what it publishes, and what its subagent reads.
    assert "ai_diary" in printed and "ai_card_events" in printed


def test_the_map_names_who_opens_the_feature_from_outside():
    # What the removal path needs first, and the reason the map says it proves nothing:
    # this is who imports Cards today, not everything that would break without it.
    assert "safwa.features.planning.use_cases -> safwa.features.cards.api" in feature_map("cards")


def test_the_map_answers_for_a_shell_package_and_refuses_a_name_nothing_registers():
    assert "tests/brd/tg_agent_shell/proposals.feature" in feature_map("proposals")

    with pytest.raises(SystemExit, match="No such feature"):
        feature_map("unplugged")


# ------------------------------------------------- Rule I: the prompt prefix is stable


def test_rule_i_prompt_prefix_is_byte_stable(request):
    # Everything a provider sees before the dialogue: the prompts and the tool schemas.
    # `_context_messages` orders the volatile blocks after it, so this is the whole of
    # what remote prompt caching can hit.
    produced = {
        "SYSTEM_PROMPT": _digest(SYSTEM_PROMPT),
        "PERSONA": _digest(PERSONA),
        "HEAVY_ANALYZER_PROMPT": _digest(HELPERS["heavy_analyzer"].instructions),
        "tool:open": _digest(json.dumps(open_tool(SCREENS), sort_keys=True)),
        "tool:route": _digest(json.dumps(ROUTE_TOOL, sort_keys=True)),
        "tool:query_data": _digest(json.dumps(QUERY_TOOL, sort_keys=True)),
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

    early = await _builder("Now: Tuesday 09:00.").root(dialogue)
    later = await _builder("Now: Friday 23:41.").root(dialogue)

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
            prompt="Save what the owner asked for.",
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
    # A Safwa database holds the shell's tables and Safwa's, so the snapshot covers both.
    produced = {}
    for name, table in {**ShellBase.metadata.tables, **Base.metadata.tables}.items():
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

    from tg_agent_shell.foundation.database import upgrade_database

    url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    upgrade_database(url, Base.metadata)
    engine = create_engine(url)
    try:
        built = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert set(Base.metadata.tables) <= built


# ------------------------------------------- Rule O: the marker codes are append-only


def test_rule_o_marker_codes_are_unchanged(request):
    # The code is written into the message text and Telegram is the store, so a code that
    # moves re-labels every message already sent under it and nothing can migrate them back.
    # It follows from the kind's name, so the two ways to move one are renaming a kind and
    # changing the derivation, and this is what notices either.
    produced = {kind: str(code) for kind, code in MARKS.codes.items()}

    _snapshot("marker_codes", produced, request.config.getoption("--snapshot-update"))


def test_rule_o_two_kinds_on_one_code_are_refused():
    # A derived code can only go wrong one way, and it goes wrong silently, so the search
    # for a colliding pair is worth what it costs.
    seen: dict[int, str] = {}
    for index in range(1 << 18):
        name = f"kind_{index}"
        code = code_for(name)
        if code in seen:
            with pytest.raises(ValueError):
                KindMarks([seen[code], name])
            return
        seen[code] = name
    pytest.fail("no colliding pair in the searched range")


# ------------------------------------------------- the package names no application


SHELL = Path(__file__).resolve().parents[1] / "src" / "tg_agent_shell"
SHELL_MODULES = sorted(path.relative_to(SHELL).as_posix() for path in SHELL.rglob("*.py"))

# Safwa's vocabulary. `action`, `request`, `summary`, `value` and `workspace` are not on it:
# generic code needs those words for its own things, and a list that cries wolf is not read.
FOREIGN = frozenset(
    {
        "advisor",
        "backlog",
        "card",
        "check",
        "diary",
        "planning",
        "reminder",
        "retro",
        "safwa",
        "sprint",
        "tag",
    }
)


@pytest.mark.parametrize("module", SHELL_MODULES)
def test_the_vocabulary_of_the_package_belongs_to_no_application(module: str) -> None:
    """Rule F says the package imports no Safwa. This says it does not name one either."""
    foreign = [name for name in public_names(SHELL / module) if words(name) & FOREIGN]

    assert not foreign, f"tg_agent_shell/{module} names {foreign}"
