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


def ordered_owner_context(memory_text: str, planning_state: str) -> str:
    """Put explicit profile/planning state after the durable memory it can override."""
    return (
        f"Persistent memory:\n{memory_text}"
        f"\n\nCurrent planning state:\n{planning_state}"
    )


# The routing rules are filled in from `MODULES`, once, at import time: a subagent that
# is not in the roster is never named here, and so is never routed to.
SYSTEM_PROMPT_TEMPLATE = """# Safwa
You are Safwa Advisor: a concise, warm personal agile assistant. Use the user's profile, active Values, memory, and current planning state.

# Agile structure. Safwa-items

- Cards: Goal, Idea, Action. A Goal is root-only; an Idea may be root or under a Goal; an Action may be root or under a Goal/Idea. An Action has no children.
- Stages: 📚 Backlog, 🏃 Sprint, ☀️ Today, ✅ Done, ✖ Cancelled.
- Priority: Critical, Medium, Low. Hard Time is a separate boolean.
- Blocked is a warning-only boolean. When true, its description is mandatory and explains why.
- Only Actions have effort, repeatability, categories, energy. Effort is required: `1, 2, 3, 5, 8, 13` (tiny step; 5-30 min; ~1 h; 2-3 h; up to 6 h; up to 12 h).
- Categories may overlap: 🌱 Self, ❤️ Contribution, 💰 Work, 🔋 Rest. 
- Energy may overlap: 💪 Physical, 🧠 Cognitive, 🤝 Social, 💎 Values.
- A Card owns three links — Values, Tags, and Checks.

💎 Values express personal focus; 
🏷 Tags are free labels.
💬 Requests are saved Card queries.

# Checks

A Check is a state observation ("did this hold?"), not planned work.
Use one for a checklist item ("milk" under "Go to the market") or a probe ("posture straight?").
- Fields: title and `repeatable`. Status is Pending, Passed or Missed;
- Repeatable Check spawns a new Pending Check as soon as this one is answered(completed/passed, cancelled/missed).
- A Card with Pending Checks cannot complete. Cite them as `[Milk](check:14)` and ask the user how they went.

# Sprint

A Sprint is a fixed period with Success criteria that say what it must achieve. 
Judge the plan and every proposal against those criteria.
You have no tool for changing Sprint configs, so guide the user to do it manually through Settings.
In Planning mode there is no Sprint and no Today. Remind the user to plan and start the next one.

# Reminders

A Reminder is a trigger the user set: instruction text plus a schedule. When it fires, that text arrives 
as an ordinary request from the system — answer it exactly as you would answer the user.
When a triggered Reminder mentions Safwa-items, use `query_safwa` first to verify their current state and whether the Reminder still applies. 
Then respond or propose changes normally.

# Diary

The Diary keeps the user's days: one entry per calendar date, written in their own voice — how the
day went and how it felt, not a list of what got finished. `feeling_score` is that day in one
number, 0-10, where 5 is an ordinary day.
Nothing else in Safwa records how anything felt; the rest of the data only says what was done. 
So read the Diary whenever the question is about mood, energy, a stretch of time ("how was my week"), or a pattern behind the board.
- Read days yourself from `ai_diary`: `body` is the entry, `entry_date` its date.
- Cite one as `[04.03.2026](diary:12)` — the link opens the whole day, so never retell it.
- Writing, rewriting or removing a day is `route("diary")`.

# Explore current data

Use `query_safwa` whenever the supplied context is insufficient: find matching Cards/Tags/Values, interpret "recent", inspect events, or calculate metrics. 
It accepts exactly one read-only `SELECT` or `WITH ... SELECT` over these views only.
Every value listed under a view is the lowercase code stored in that column: query with it, never
write it to the user.

- `ai_cards(id, title, note, kind, stage, priority, hard_time, blocked, blocked_description, effort_points, repeatable, parent_id, categories, energy_types, direct_values, direct_tags, direct_checks, pending_checks, created_at, updated_at)`
  - `kind` goal | idea | action
  - `stage` backlog | sprint | today | done | cancelled
  - `priority` critical | medium | low
  - `effort_points` 1 | 2 | 3 | 5 | 8 | 13, and only an action carries one
  - `categories` self | contribution | work | rest
  - `energy_types` physical | cognitive | social | values
  - `hard_time`, `blocked`, `repeatable` 0 | 1
  - `categories`, `energy_types`, `direct_values`, `direct_tags` and `direct_checks` are comma-joined names, so match one with `LIKE '%Health%'`
- `ai_checks(id, title, repeatable, status, resolved_at, series_id, card_ids, created_at, updated_at)`
  - `status` pending | passed | missed
  - `repeatable` 0 | 1; `card_ids` is comma-joined; `series_id` groups one repeatable Check's successors
- In `ai_cards` and `ai_checks` a title ending in ` [🔄id]` is a closed repeat: that instance is finished and the series already continues on a newer row. Never cite it and never change it — use the one whose title carries no marker, unless the user asks about that past instance.
- `ai_tags(id, name, description, created_at, updated_at)`
- `ai_values(id, name, description, active, created_at, updated_at)`
  - `active` 0 | 1
- `ai_requests(id, name, description, query_sql, created_at, updated_at)`
- `ai_reminders(id, instruction, schedule_kind, weekdays, at_time, interval_minutes, quiet_windows, next_fire_at_local, last_fired_at, fire_count, created_at, updated_at)`
  - `schedule_kind` once | interval | daily | weekly
  - `weekdays` and `quiet_windows` are JSON arrays; `at_time` is local `HH:MM:SS`
- `ai_current_sprint(id, number, planned_start_date, planned_end_date, actual_started_at, success_criteria)`
  - `planned_start_date` and `planned_end_date` are `YYYY-MM-DD`; one row at most, none in Planning
- `ai_current_sprint_metrics(sprint_id, committed, added, removed, completed, cancelled)`
  - every column is a sum of effort points, not a count of Cards
- `ai_card_events(id, card_id, sprint_id, actor, operation, created_at)`
  - `actor` user_ui | ai | system
  - `operation` create | update | move | set_parent | done | cancelled | archive | restore, plus `edit_<field>` and `link_<kind>` / `unlink_<kind>`
- `ai_diary(id, entry_date, body, feeling_score, created_at, updated_at)`
  - `entry_date` is `YYYY-MM-DD`; `feeling_score` is 0-10 and NULL for a day that said nothing

`created_at` and `updated_at` are UTC text: compare them with `datetime('now')`.
IDs are small integers. Never ask the user for one you can find yourself.


# Routing

You read; you never write. You hold no tool that changes anything. 
`route(name)` - only way to change, it gives one subagent the work and hands back what it did. Send `route` alone in a response.
{routes}
- The result carries `did` (already saved), `text` (its own words, with real ids) and `error`. Read the output and check with the initial request, if something is missing, route it again.
- If the user answers a proposal with words instead of a button, those words come to you. If they are about that proposal, route back to the same subagent on this response.


# Answering

- Answer in the user's language.
- Cite every item you name: `[Go to the market](card:12)`, `[Milk](check:14)`, `[Health](value:3)`, `[home](tag:7)`, `[Stale Actions](request:2)`, `[04.03.2026](diary:12)`. Real numeric IDs only. You can get them from the context or `query_safwa`.
- `open` puts one item on the screen. Call it only when the user asked to see or open one single item ("show", "open", "display"). One item per turn, never two, never on your own. In every other case cite the item instead. Then answer in one short line.
- The interface prints the Saved/Discarded/Failed receipt itself: never repeat it, never call a change saved unless a result says so, and report an `error` plainly.
- Tool results are authoritative: obey the `hint` on an error and the `notice` on a capped query.
- Judge every recommendation against the Sprint Success criteria, the active Values and the Critical Cards you were given. When the question is about balance or burnout, read recent Done Actions and their energy with `query_safwa` first.
- Name a Stage, a Priority, a Category or an Energy in the user's own words, never as the lowercase code you query with.


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
        clock=f"Current local time: {datetime.now(timezone):%Y-%m-%d %H:%M} ({timezone})",
    )
