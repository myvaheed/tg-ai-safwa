"""The registry itself: what `bootstrap/modules.py` promises about a feature.

Phase 2's claim is that adding an entity the model may change costs one package plus one
line in `MODULES`.  Rule H proves no other module spells a feature's name; these tests
prove the registry actually carries what those modules used to enumerate by hand.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect

from safwa.ai.sql import create_ai_views
from safwa.bootstrap.modules import (
    AGENTS,
    AI_VIEWS,
    ALLOWED_VIEWS,
    BACKGROUND_TASKS,
    MODULES,
    PROPOSALS,
    RECOVERY_HOOKS,
    SYSTEM_PROMPT,
)
from safwa.cues.module import CUE_QUEUE
from safwa.models import Base


def test_every_entity_registers_all_three_proposal_responsibilities():
    contributions = [
        contribution for module in MODULES for contribution in module.proposals
    ]

    for contribution in contributions:
        entity = contribution.handler.entity
        assert PROPOSALS.handler(entity) is contribution.handler
        assert PROPOSALS.presenter(entity) is contribution.presenter
        assert PROPOSALS.tools[contribution.tool.name] is contribution.tool

    assert len(PROPOSALS.handlers) == len(contributions)
    assert set(PROPOSALS.presenters) == set(PROPOSALS.handlers)


def test_a_mutation_tool_name_belongs_to_exactly_one_feature():
    declared = [contribution.tool.name for module in MODULES for contribution in module.proposals]
    declared += [tool.name for module in MODULES for tool in module.mutation_tools]

    assert sorted(declared) == sorted(set(declared))
    assert set(declared) == set(PROPOSALS.tools)


def test_an_unowned_entity_is_refused_rather_than_silently_dropped():
    from safwa.domain import DomainError

    with pytest.raises(DomainError):
        PROPOSALS.handler("habit")


def test_the_view_allowlist_is_the_catalogue_the_database_gets(tmp_path):
    # One source: what `query_safwa` may name is exactly what startup created.
    assert ALLOWED_VIEWS == {view.name for view in AI_VIEWS}
    assert len(AI_VIEWS) == len({view.name for view in AI_VIEWS})

    engine = create_engine(f"sqlite:///{(tmp_path / 'views.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        create_ai_views(connection, AI_VIEWS)
    try:
        built = set(inspect(engine).get_view_names())
    finally:
        engine.dispose()

    assert ALLOWED_VIEWS <= built


def test_the_routing_rules_are_generated_from_the_roster():
    # CLAUDE.md warns that a subagent missing from the prompt is never routed to; the
    # roster is now the only place that can omit one.
    rules = SYSTEM_PROMPT.split("# Routing", 1)[1].split("\n# ", 1)[0]

    for agent in AGENTS:
        assert f'- `route("{agent.name}")` — {agent.purpose}' in rules
    assert rules.count('- `route("') == len(AGENTS)


def test_a_subagent_only_declares_tools_a_feature_publishes():
    for agent in AGENTS:
        assert set(agent.mutation_tools) <= set(PROPOSALS.tools), agent.name


def test_lifecycle_work_is_collected_from_the_modules_that_own_it():
    assert list(RECOVERY_HOOKS) == [m.recover for m in MODULES if m.recover is not None]
    # The Cue poll belongs to no feature: it delivers whatever any of them wrote.
    assert [task.name for task in BACKGROUND_TASKS] == [
        CUE_QUEUE.name,
        *(task.name for module in MODULES for task in module.background),
    ]
    assert len({task.name for task in BACKGROUND_TASKS}) == len(BACKGROUND_TASKS)
