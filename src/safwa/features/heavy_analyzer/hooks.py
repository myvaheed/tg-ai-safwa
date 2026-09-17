"""A read past one flat scan is not run by the Advisor: the helper writes it instead."""

from __future__ import annotations

import json

from tg_agent_shell.ai.sql import is_complex_read
from tg_agent_shell.hooks.contracts import BeforeTool, HookSpec, OnBeforeTool, RefuseTool

from .agent import NAME, OFFER


async def complex_read(event: BeforeTool) -> tuple[str, ...]:
    """The offer, when the SQL the model sent goes past one flat scan.

    Arguments that are not a read at all are left to the runner, which refuses them with
    the hint that repairs them.
    """
    try:
        sql = json.loads(event.arguments_json)["sql"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return ()
    return (OFFER,) if isinstance(sql, str) and is_complex_read(sql) else ()


HEAVY_ANALYZER_HOOK = HookSpec(
    name="analyzer.offer",
    owner=NAME,
    on=(OnBeforeTool(tool="query_data"),),
    evaluate=complex_read,
    effect=RefuseTool(helper=NAME),
    title="Helper offer",
    description="Does not run a read past one flat scan; offers the helper that writes the query instead.",
)
