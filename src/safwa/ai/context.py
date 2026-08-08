from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..enums import CardStage
from ..models import Card, SavedRequest, Tag, UserProfile, Value, Workspace


@dataclass(frozen=True)
class DialogueMessage:
    role: str
    content: str


SYSTEM_PROMPT = """You are Safwa, a thoughtful personal agile advisor in a private Telegram chat.
Be concise, warm, practical, and faithful to the user's profile, active Values, and actual planning state.
The database is the source of truth for cards and Sprints. Never claim a write happened before approval.
Card creation is special: propose a card create change so the application can open mandatory draft review.
Never provide or request write SQL. You may request one safe read-only SELECT over the documented ai_* views.
Return exactly one JSON object matching the response contract. Do not wrap it in Markdown.

For every Action creation, actively infer and provide effort_points, categories, and energy_types from
the requested work—even when the user did not state them verbatim. Estimate effort only as 1, 2, 3, 5,
8, or 13. Use empty fields only when no reasonable inference is possible; the review UI remains the user’s
final authority. Include kind, title, note, stage, priority, hard_time, effort_points, repeatable,
categories, energy_types, value_ids or value_query, tag_ids or tag_query, and parent_id or parent_query
when known. For a new parent and child in the same response, assign each create a draft_ref and set the
child's parent_draft_ref to the parent's draft_ref. If a requested parent cannot be uniquely identified,
preserve parent_query so the review UI requires a choice.

Effort rubric: 1 is 0–5 minutes (one tiny step, about 300 steps, one page, or a short conversation);
2 is 5–30 minutes (about 1,000 steps, three pages, or a 30-minute meeting); 3 is about one intensive
hour; 5 is two to three intensive hours; 8 is up to six intensive hours; 13 is up to twelve intensive
hours. Estimate the work itself, not the user's motivation or importance.

Categories may overlap: Self is self-development, learning, health, or personal projects; Contribution
is primarily benefit for others, gifts, or service; Work creates economic/professional value; Rest is
recovery, play, or leisure. Select every category that genuinely describes the Action, not a forced one.

Energy types may overlap: Physical is bodily exertion or physical capacity; Cognitive is concentration,
reasoning, learning, or mental intensity; Social is interaction, communication, or coordination; Values
is energy drawn from or spent on acting in alignment with a personally important Value. Select every
genuinely relevant energy type. These are estimates to help planning, not facts about the user.

Saved Requests are named, reusable filters over committed Cards. They can only be created or changed
through an approved AI proposal. To create one, return entity="request", action="create", with name,
optional description, and filter. A filter is a nested object using all and/or any arrays. Each predicate
is {"field": ..., "op": ..., "value": ...}. Fields: kind, stage, priority (eq/in); hard_time,
repeatable, liked, has_blockers (eq true/false); effort_points (eq/in/gte/lte); parent_id (eq/is_null);
title or note (contains); tag_id or value_id (any_of/all_of/none_of). Use IDs shown in context for Tag
and Value predicates. Example Family actions: {"all":[{"field":"kind","op":"eq","value":"action"},
{"field":"tag_id","op":"any_of","value":["TAG-ID"]}]}. Never use SQL or unknown fields.
"""


async def planning_context(session: AsyncSession) -> str:
    workspace = await session.get(Workspace, 1)
    profile = await session.get(UserProfile, 1)
    active_values = list(
        await session.scalars(
            select(Value)
            .where(Value.active.is_(True), Value.archived_at.is_(None))
            .order_by(Value.name)
        )
    )
    tags = list(
        await session.scalars(
            select(Tag).where(Tag.archived_at.is_(None)).order_by(Tag.name)
        )
    )
    requests = list(
        await session.scalars(
            select(SavedRequest)
            .where(SavedRequest.archived_at.is_(None))
            .order_by(SavedRequest.name)
            .limit(20)
        )
    )
    today = list(
        await session.scalars(
            select(Card)
            .where(Card.effective_stage == CardStage.TODAY.value, Card.archived_at.is_(None))
            .order_by(Card.hard_time.desc(), Card.priority, Card.created_at)
        )
    )
    sprint = list(
        await session.scalars(
            select(Card)
            .where(Card.effective_stage == CardStage.SPRINT.value, Card.archived_at.is_(None))
            .order_by(Card.hard_time.desc(), Card.priority, Card.created_at)
            .limit(40)
        )
    )
    lines = [
        f"Current local time: {datetime.now(ZoneInfo(workspace.timezone if workspace else 'Europe/Istanbul')).isoformat()}",
        f"Workspace mode: {workspace.mode if workspace else 'planning'}",
        f"Workspace revision: {workspace.revision if workspace else 0}",
        f"About me: {(profile.about_me if profile else '').strip()}",
        f"Advisor instructions: {(profile.advisor_instructions if profile else '').strip()}",
        "Active Values: " + ", ".join(f"{v.name} [{v.id}]" for v in active_values),
        "Available Tags: " + ", ".join(f"{tag.name} [{tag.id}]" for tag in tags),
        "Saved Requests: " + ", ".join(f"{request.name} [{request.id}]" for request in requests),
        "Today cards:",
        *[f"- {c.title} [{c.id}] kind={c.kind} effort={c.effort_points}" for c in today],
        "Sprint cards:",
        *[f"- {c.title} [{c.id}] kind={c.kind} effort={c.effort_points}" for c in sprint],
        "Read views: ai_cards, ai_boards, ai_values, ai_current_sprint, ai_current_sprint_metrics, ai_card_events.",
    ]
    return "\n".join(lines)


async def lexical_candidates(session: AsyncSession, user_text: str, limit: int = 12) -> str:
    words = [
        word.casefold()
        for word in re.findall(r"[\w-]{3,}", user_text, flags=re.UNICODE)
        if word.casefold()
        not in {"please", "card", "action", "goal", "idea", "today", "sprint", "move", "create"}
    ][:8]
    if not words:
        return ""
    match = " OR ".join(f'"{word.replace(chr(34), "")}"' for word in words)
    try:
        rows = (
            await session.execute(
                text(
                    "SELECT c.id, c.title, c.kind, c.effective_stage "
                    "FROM card_search s JOIN cards c ON c.id=s.card_id "
                    "WHERE card_search MATCH :match AND c.archived_at IS NULL "
                    "ORDER BY bm25(card_search) LIMIT :limit"
                ),
                {"match": match, "limit": limit},
            )
        ).all()
    except Exception:
        return ""
    return "\n".join(
        f"- {row.title} [{row.id}] kind={row.kind} stage={row.effective_stage}" for row in rows
    )
