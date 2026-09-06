"""What the provider is told when it rewrites the memory list.

Memory routes to no subagent: these two are the whole of what the model reads here.
"""

from __future__ import annotations

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
