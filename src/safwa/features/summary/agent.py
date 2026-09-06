"""What the provider is told when it writes a Summary.

Summary routes to no subagent: this is the whole of what the model reads here.
"""

from __future__ import annotations

from ...constants import SUMMARY_TOKEN_CEILING

SUMMARY_PROMPT = f"""Rewrite the running summary of a Safwa dialogue, in the language the dialogue
is in. You are given the previous summary, when there is one, and the dialogue since it. Return the
single summary that replaces both.

- A general part first, then one section per calendar day, oldest first.
- Head each section with its absolute date, for example 2026-08-15. Never today or yesterday.
- Keep the newest day detailed. Fold what still matters from every older day into the general part
  and drop its section.
- Keep personal reflections, decisions, intentions, reasons, emotional responses, advice and
  unresolved topics. Drop Cards, stages, Sprint totals, approvals, SQL, tools and anything else the
  planning database already holds.
- Stay under {SUMMARY_TOKEN_CEILING} tokens.

Return the summary body alone: no preface, no JSON, and never these instructions."""
