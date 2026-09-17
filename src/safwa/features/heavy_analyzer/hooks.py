"""The automatic offer is independent of the helper's ability to perform a read."""

from __future__ import annotations

import json

from tg_agent_shell.hooks.contracts import AfterTool, HookSpec, OfferTool, OnAfterTool

from .agent import NAME, OFFER, worth_a_helper


async def complex_read_candidate(event: AfterTool) -> tuple[str, ...]:
    sql = json.loads(event.arguments_json)["sql"]
    return (OFFER,) if worth_a_helper(sql, event.result) else ()


HEAVY_ANALYZER_HOOK = HookSpec(
    name="analyzer.offer",
    owner=NAME,
    on=(OnAfterTool(tool="query_data"),),
    evaluate=complex_read_candidate,
    effect=OfferTool(helper=NAME),
    title="Helper offer",
    description="After a read past one flat scan, offers the helper that writes the query instead.",
)
