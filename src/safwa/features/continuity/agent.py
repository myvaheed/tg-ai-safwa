"""What the provider is told when it writes a Summary or rewrites the memory list.

Continuity routes to no subagent: these are the whole of what the model reads here.
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

RETELL_PROMPT = """Retell this piece of a Safwa dialogue, compactly, as a source for durable memory
about the user.
- Keep stable preferences, routines, constraints, motivations, recurring difficulties,
  relationships, energy patterns and planning lessons.
- Drop Cards, Sprint state, deadlines, commands, UI, SQL and operations.
- Never invent a fact.
Return plain text alone."""

MEMORY_PROMPT = """You are given the existing memory list and a new retelling. Return the complete
list that replaces it.
- Keep only durable, useful facts about the user.
- Remove duplicates and facts that are no longer true.
- Never add Card stages, Sprint metrics, temporary priorities, obstacles, deadlines, SQL or tool
  traces.
- Keep the language the facts are written in. Never invent one.
Return JSON alone: {"facts": ["one complete non-empty fact per item"]}"""
