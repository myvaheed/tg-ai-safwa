"""Reads the Advisor does not run: one past one flat scan goes to the helper, and one that
matches words with LIKE goes to search or to the subagent that acts on the request."""

from __future__ import annotations

import json
from collections.abc import Sequence

from tg_agent_shell.ai.sql import SqlView, is_complex_read, like_on_words
from tg_agent_shell.hooks.contracts import BeforeTool, HookSpec, OnBeforeTool, RefuseTool

from .agent import NAME, OFFER

WORDS_NOTICE = (
    "LIKE misses other forms of a word.\n"
    'To find an item by words, call query_data with them in "search", not in "sql": '
    '{{"search": "<the user\'s words>", "sql": "SELECT id, {column} FROM {view} WHERE relevance '
    'IS NOT NULL ORDER BY relevance DESC LIMIT 5"}}\n'
    "To create, update, remove or remind, call route: the subagent reads the conversation and "
    "finds the items itself."
)


def _sql(event: BeforeTool) -> str | None:
    """The SQL the model sent. Arguments that are not a read at all are left to the runner,
    which refuses them with the hint that repairs them."""
    try:
        sql = json.loads(event.arguments_json)["sql"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return sql if isinstance(sql, str) else None


async def complex_read(event: BeforeTool) -> tuple[str, ...]:
    """The offer, when the SQL the model sent goes past one flat scan."""
    sql = _sql(event)
    return (OFFER,) if sql is not None and is_complex_read(sql) else ()


HEAVY_ANALYZER_HOOK = HookSpec(
    name="analyzer.offer",
    owner=NAME,
    on=(OnBeforeTool(tool="query_data"),),
    evaluate=complex_read,
    effect=RefuseTool(helper=NAME),
    title="Helper offer",
    description="Does not run a read past one flat scan; offers the helper that writes the query instead.",
)


def words_hook(views: Sequence[SqlView]) -> HookSpec:
    """The refusal of a read that matches words with LIKE, over the views whose word columns
    the search index reads (AD-READ-009)."""

    async def words_by_like(event: BeforeTool) -> tuple[str, ...]:
        sql = _sql(event)
        found = like_on_words(sql, views) if sql is not None else None
        if found is None:
            return ()
        view, column = found
        return (WORDS_NOTICE.format(view=view, column=column),)

    return HookSpec(
        name="analyzer.words",
        owner=NAME,
        on=(OnBeforeTool(tool="query_data"),),
        evaluate=words_by_like,
        effect=RefuseTool(),
        title="Words by search",
        description="Does not run a read that matches words with LIKE; says to search for them or to hand the request over.",
    )
