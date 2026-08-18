from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import CONTEXT_CRITICAL_CARD_LIMIT
from ..enums import CardKind, CardStage, Priority
from ..models import Card, CardValue, Sprint, Tag, UserProfile, Value, Workspace


@dataclass(frozen=True)
class DialogueMessage:
    role: str
    content: str


@dataclass(frozen=True)
class PlanningContext:
    """Split so the volatile clock can be sent after the cacheable prefix."""

    state: str
    clock: str


SYSTEM_PROMPT = """# Safwa
You are Safwa: a concise, warm personal agile advisor in one private Telegram chat. Answer in the
owner's language. The database is the source of truth; your context carries their profile, their
active Values, their memory and the current plan.

The plan is Cards — `goal`, `idea`, `action` — at a stage: 📚 Backlog, 🏃 Sprint, ☀️ Today, ✅ Done,
✖ Cancelled. A Card owns Values, Tags and Checks; a Check is a state observation ("did this hold?"),
never work. A Sprint is a fixed period with Success criteria: judge the plan against them. In
Planning there is no Sprint — guide the owner to the 🏃 Sprint screen, which no tool can replace.
A Reminder the owner set arrives later as an ordinary request; answer it as you answer them.

# Read the data
`query_safwa` runs one read-only `SELECT` or `WITH ... SELECT` over these views only:
- `ai_cards(id, title, note, kind, stage, priority, hard_time, blocked, blocked_description,
  effort_points, repeatable, parent_id, categories, energy_types, direct_values, direct_tags,
  direct_checks, pending_checks, created_at, updated_at)`
- `ai_checks(id, title, repeatable, status, resolved_at, series_id, card_ids, created_at, updated_at)`
- `ai_tags(id, name, description, created_at, updated_at)`
- `ai_values(id, name, description, active, created_at, updated_at)`
- `ai_requests(id, name, description, query_sql, created_at, updated_at)`
- `ai_reminders(id, instruction, schedule_kind, weekdays, at_time, interval_minutes, quiet_windows,
  next_fire_at, last_fired_at, fire_count, created_at, updated_at)`
- `ai_current_sprint(id, number, planned_start_date, planned_end_date, actual_started_at,
  success_criteria)`
- `ai_current_sprint_metrics(sprint_id, committed, added, removed, completed, cancelled)`
- `ai_card_events(id, card_id, sprint_id, actor, operation, created_at)`
- `ai_diary(id, entry_date, body, feeling_score, created_at, updated_at)`
IDs are small integers. Never ask the owner for one you can find yourself.

# Routing
You read; you never write. You hold no tool that changes anything, so a change you describe instead
of routing is a change that never happens. `route(name)` gives one subagent the work and hands back
what it did. Send `route` alone in a response.
- `route("board")` — any change to a Card, Check, Value, Tag, Request or Reminder.
- `route("diary")` — write, rewrite or delete a day. Reading a day is `query_safwa` over `ai_diary`.
- The result carries `did` (already saved), `text` (its own words, with real ids) and `error`. Read
  it, route again for a part another subagent owns, then answer once.
- If the owner answers a proposal with words instead of a button, those words come to you. If they
  are about that proposal, route back to the same subagent on this response — anything else you do
  ends that draft.

# Answering
- Cite every item you name: `[Go to the market](card:12)`, `[Milk](check:14)`, `[Health](value:3)`,
  `[home](tag:7)`, `[Stale Actions](request:2)`, `[04.03.2026](diary:12)`. Real numeric IDs only.
- The interface prints the Saved/Discarded/Failed receipt itself. Never repeat it, and never call a
  change saved unless a result says so. Report an `error` plainly.
- Tool results are authoritative: obey the `hint` on an error and the `notice` on a capped query.
"""


def citation(name: str, kind: str, item_id: int) -> str:
    """The one shape an item takes in context, ready for the model to reuse in a reply."""
    return f"[{name}]({kind}:{item_id})"


async def _critical_cards(session: AsyncSession) -> list[Card]:
    """The critical Cards, those carrying an active Value first."""
    linked_active_value = (
        select(CardValue.card_id)
        .join(Value, Value.id == CardValue.value_id)
        .where(
            CardValue.card_id == Card.id,
            Value.active.is_(True),
            Value.archived_at.is_(None),
        )
        .exists()
    )
    return list(
        await session.scalars(
            select(Card)
            .where(
                Card.priority == Priority.CRITICAL.value,
                Card.archived_at.is_(None),
                Card.effective_stage.notin_(
                    [CardStage.DONE.value, CardStage.CANCELLED.value]
                ),
            )
            .order_by(linked_active_value.desc(), Card.hard_time.desc(), Card.created_at)
            .limit(CONTEXT_CRITICAL_CARD_LIMIT)
        )
    )


async def planning_context(session: AsyncSession) -> PlanningContext:
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
        await session.scalars(select(Tag).where(Tag.archived_at.is_(None)).order_by(Tag.name))
    )
    sprint = (
        await session.get(Sprint, workspace.active_sprint_id)
        if workspace and workspace.active_sprint_id
        else None
    )
    timezone = ZoneInfo(workspace.timezone if workspace else "Europe/Istanbul")
    lines = [
        f"Workspace mode: {workspace.mode if workspace else 'planning'}",
        f"About me: {(profile.about_me if profile else '').strip()}",
        f"Advisor instructions: {(profile.advisor_instructions if profile else '').strip()}",
        "Active Values: "
        + ", ".join(citation(value.name, "value", value.id) for value in active_values),
        "Available Tags: " + ", ".join(citation(tag.name, "tag", tag.id) for tag in tags),
    ]
    if sprint is not None:
        lines.extend(
            [
                f"Sprint {sprint.number}: {sprint.planned_start_date} – {sprint.planned_end_date}",
                f"Success criteria: {sprint.success_criteria.strip()}",
            ]
        )
    else:
        lines.append(
            "No Sprint is running; the workspace is in Planning. "
            + (
                f"Draft Success criteria for the next one: "
                f"{workspace.sprint_success_criteria.strip()}"
                if workspace and workspace.sprint_success_criteria.strip()
                else "No Success criteria have been written yet."
            )
        )
    critical = await _critical_cards(session)
    lines.append("Critical Cards:")
    lines.extend(
        f"- {citation(card.title, 'card', card.id)} kind={card.kind} stage={card.effective_stage}"
        for card in critical
    )
    if sprint is not None:
        today = list(
            await session.scalars(
                select(Card)
                .where(
                    Card.effective_stage == CardStage.TODAY.value,
                    Card.kind == CardKind.ACTION.value,
                    Card.archived_at.is_(None),
                )
                .order_by(Card.hard_time.desc(), Card.priority, Card.created_at)
            )
        )
        lines.append("Today Actions:")
        lines.extend(
            f"- {citation(card.title, 'card', card.id)} effort={card.effort_points}"
            for card in today
        )
    return PlanningContext(
        state="\n".join(lines),
        clock=f"Current local time: {datetime.now(timezone).isoformat()}",
    )
