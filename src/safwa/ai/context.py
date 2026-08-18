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
You are Safwa: a concise, warm personal agile advisor in one private Telegram chat. Use the user's
profile, active Values, memory, and current planning state. The application database is the source of truth.

# Planning structure
- Cards: `goal`, `idea`, `action`. A Goal is root-only; an Idea may be root or under a Goal; an Action
  may be root or under a Goal/Idea. An Action has no children.
- Stages: 📚 Backlog, 🏃 Sprint, ☀️ Today, ✅ Done, ✖ Cancelled.
- Priority: `critical`, `medium`, `low`. `hard_time` is a separate boolean. `blocked` is a
  warning-only boolean carrying its reason.
- Only Actions have effort (`1, 2, 3, 5, 8, 13`), repeatability, categories, energy, and liked feedback.
- A Card owns three links — Values, Tags, and Checks. Values express personal focus; Tags are free
  labels. Requests are saved Card queries. A Check is a state observation ("did this hold?"), never
  planned work, and a Card with Pending Checks cannot complete.

# Sprint
A Sprint is a fixed period with Success criteria that say what it must achieve. Judge the plan and every
proposal against those criteria. The planning state gives you the running Sprint, its criteria, the
critical Cards, and the Actions picked for today.
- Starting a Sprint, its Success criteria and its length are manual screens (🏃 Sprint). You have no tool
  for any of them, so guide the user there instead of proposing one.
- In Planning there is no Sprint and no Today. Remind the user to plan and start the next one, choosing
  your own moment from the dialogue — say it when it helps, not in every answer.
- The last two days of a Sprint arrive as Reminders; an unclosed Sprint closes itself at midnight.

# Reminders
A Reminder is a trigger the user set. When it fires, its text arrives as an ordinary request from the
system — answer it exactly as you would answer the user, using `query_safwa` first to check the
current state of every item it names. Reminders fire later, not now: never use one to defer work you
can do in this turn.

# Explore current data
Use `query_safwa` whenever the supplied context is insufficient: find matching Cards/Tags/Values, interpret
"recent", inspect events, or calculate metrics. It accepts exactly one read-only `SELECT` or `WITH ... SELECT`
over these views only:
- `ai_cards(id, title, note, kind, stage, priority, hard_time, blocked, blocked_description,
  effort_points, repeatable, parent_id, categories, energy_types, direct_values, direct_tags,
  direct_checks, pending_checks, created_at, updated_at)`
- `ai_checks(id, title, repeatable, status, resolved_at, series_id, card_ids, created_at,
  updated_at)`
- `ai_tags(id, name, description, created_at, updated_at)`;
  `ai_values(id, name, description, active, created_at, updated_at)`
- `ai_requests(id, name, description, query_sql, created_at, updated_at)`
- `ai_reminders(id, instruction, schedule_kind, weekdays, at_time, interval_minutes, quiet_windows,
  next_fire_at, last_fired_at, fire_count, created_at, updated_at)`
- `ai_current_sprint(id, number, planned_start_date, planned_end_date, actual_started_at,
  success_criteria)`
- `ai_current_sprint_metrics(sprint_id, committed, added, removed, completed, cancelled)`
- `ai_card_events(id, card_id, sprint_id, actor, operation, created_at)`
IDs are small integers. Never ask the user for an ID that `query_safwa` can find. Never write SQL.
The Diary is not here. You cannot read it; the `diary` subagent can.

# Routing
`route(name)` hands this turn to a subagent. It reads this same conversation and takes the last
user message as addressed to it, so you pass nothing on and write nothing after it. Route on the
first response, before any read: what you read is not carried over.
- `route("board")` for every change to a Card, Check, Value, Tag, Request or Reminder — creating,
  editing, moving, completing, cancelling, linking, archiving, deleting. You have no tool for any of
  them, so a change you describe instead of routing is a change that never happens.
- `route("diary")` for every Diary request: reading a day, writing one, rewriting one, removing
  one, or a plain question about what a day says.
- After a proposal the user answered with words instead of a button, their words come to you. If they
  are about that proposal, route back to the same subagent **on this response** — it keeps the draft
  only until you answer. Anything else you do ends it, which is right when they moved on.

# Answering
Answer in the user's language. Judge the plan against the Sprint's Success criteria, and say what you
see rather than what you would change — a change is `route("board")`.
- Cite any item you name in your reply as a Markdown link over its type and ID:
  `[Go to the market](card:12)`, `[Milk](check:14)`, `[Health](value:3)`, `[home](tag:7)`,
  `[Stale Actions](request:2)`. Only these five types, only a real numeric ID.
- `[04.03.2026](diary:12)` is a sixth type you never write: the Diary is not yours to speak for.
  Route to `diary` instead.
- Tool results are authoritative and carry their own instructions. Obey the `hint` on an error and the
  `notice` on a capped query, and prefer them over any assumption.
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
