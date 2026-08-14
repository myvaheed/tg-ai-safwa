# Safwa — Reminders

**Implemented.** This file stays as the contract: the rules below are what the code is written
against, and a change to the behaviour is a change to this file first.
Read [ARCHITECTURE.md](ARCHITECTURE.md) for how it fits the rest of the codebase.

Where the doc named a placeholder, the code settled on: `next_occurrence` → `next_fire`
([reminders.py](../src/safwa/reminders.py)); the mini-session runner is
[ai/mini.py](../src/safwa/ai/mini.py) with the two Reminder sessions in
[ai/reminder_sessions.py](../src/safwa/ai/reminder_sessions.py); the escalation runtime is
[telegram/escalation.py](../src/safwa/telegram/escalation.py) and the screens are
[telegram/reminders.py](../src/safwa/telegram/reminders.py).

## Rules

These are the contract. Everything below follows from them.

1. Safwa sends a proactive message **only** because a Reminder the owner set fired. The 13 nudge kinds
   are deleted.
2. A Reminder is **instruction text + schedule**. Nothing else is stored.
3. When a Reminder fires, the system does exactly one thing: it **hands the instruction text to the
   main advisor as a request**. It never composes a message, never renders an item, never decides
   anything. The advisor answers with the tools it already has.
4. The advisor never structures a schedule. It passes the timing through as **free text**; a setup
   mini-session turns that into parameters, and the schedule is then computed **in code**. If the text
   is not resolvable, the session calls `not_clear_enough(reason)` and the advisor asks the owner.
   **Never guess a time.**
5. **No part of this system writes a Reminder without a proposal.** The setup mini-session only
   *resolves* free text into parameters; the result becomes an ordinary Save/Discard screen, exactly
   like a Card or Check proposal. The only unapproved write is a one-shot deleting itself after it
   fires.
6. **Relevance is checked by the system, never stored on the row.** Before each escalation a mini-session
   looks up the Safwa items the instruction names by `#id`, reports their current state, and returns one
   of two verdicts: `trigger` or `irrelevant`. It answers "does this Reminder still make sense?", never
   "is now a good time" — time suppression is a quiet window. That state text rides along in the
   escalation so the main advisor does not have to look it up itself.
7. **Escalations are batched** — everything one poll found goes over in one advisor turn.
8. **Nothing escalates while the advisor is busy**: generating, or waiting for an answer to any
   pending proposal. That proposal must resolve completely first.
9. **The owner always wins.** An owner message arriving while a background escalation holds the guard
   cancels that escalation.
10. **Editing instruction text never changes the timing.**

## How it works, end to end

### The loop

The scheduler poll in [scheduler.py](../src/safwa/scheduler.py) ticks every
`SCHEDULER_POLL_SECONDS = 30`. That poll *is* the alarm clock:

```sql
SELECT * FROM reminders WHERE next_fire_at <= :now
ORDER BY next_fire_at LIMIT 3            -- REMINDER_FIRE_BATCH
```

`next_fire_at` (UTC, indexed) is the only column the loop reads, and it is **written only after a
successful escalation**. That single ordering rule gives three properties for free:

- **A cancelled or crashed turn loses nothing.** The row is still overdue, so the next tick — 30
  seconds later — finds it again and retries. It keeps retrying every tick until the advisor is free.
- **Restarts need no setup.** The rows *are* the schedule; boot only reconciles.
- **Downtime is visible.** A Reminder due while the bot was down is simply overdue at the next tick.

Do not confuse the two intervals: the **poll** is 30 seconds and is the system's clock; a Reminder's
own `interval_minutes` is a property of its row. A deferred Reminder waits seconds for a quiet moment,
never one of its own cycles.

No scheduling library. `cron` cannot see SQLite or reach the live bot; APScheduler and
`asyncio.call_later` keep state in memory that must be rebuilt at boot and can disagree with the rows.
The only cron-like logic needed is `next_fire`, specified below.

### The full pipeline

```
poll tick
 ├─ reminders_enabled false, or snoozed?          → stop
 ├─ SELECT due (above)                            → nothing? stop
 ├─ gate closed? (advisor busy / proposal open)   → stop, advance NOTHING
 ├─ for each due Reminder:
 │    no "#<id>" in the instruction?              → verdict = trigger, no state text
 │    workspace.revision == evaluated_revision?   → reuse last_verdict + last_state
 │    else                                        → RELEVANCE mini-session
 ├─ format ONE escalation text covering the batch
 ├─ ONE main-advisor turn (guard held as background)
 └─ on success, for each Reminder:
      one-shot?   → DELETE the row
      repeating?  → next_fire_at = roll_forward(from the SCHEDULED moment)
```

### What the advisor receives

```
3 Reminders triggered.

1. Reminder #7
   Text: Ask me what to start with today.
   Schedule: every weekday at 08:30 (fired 14 times, last yesterday 08:30)

2. Reminder #12
   Text: Check my posture — Check #5 "Is my posture straight?"
   Schedule: every 2 hours between 09:00 and 22:00 (fired 3 times today)
   State: Check #5 is Pending, on Card #42 (In progress). Still relevant.

3. Reminder #19
   Text: Remind me to review the launch plan — Card #88.
   Schedule: once, was due 2026-08-12 09:00 (4 hours late)
   State: NO LONGER RELEVANT — Card #88 was completed on 2026-08-11.
```

Reminder #7 names no item, so no session ran and there is no `State` line.

The advisor treats this as an ordinary request: a `check` proposal for #12 (which already carries the
answer buttons), one sentence for #7, `remove(type="reminder", id=19)` for #19. **The reminder system
never needed to know what a Check is.** Its only job is to format this text well.

## Data model

```
reminders
  id
  instruction          text      — handed to the advisor at fire time; must be self-contained
                                   and name every item it concerns by #id

  schedule_kind        str       — once | interval | daily | weekly   (derived, never model-supplied)
  weekdays             JSON      — ["Mon",…]; all seven ⇒ daily; empty otherwise
  at_time              nullable  — local wall clock "HH:MM"
  anchor_at            nullable  — UTC; the one-shot moment, or the moment a recurrence STARTS
  interval_minutes     nullable
  quiet_windows        JSON      — ["22:00-09:00"]; interval schedules only

  next_fire_at         UTC, indexed, NOT NULL — the only column the loop reads
  last_fired_at        nullable
  fire_count           int
  evaluated_revision   nullable  — workspace.revision when the verdict below was produced
  last_verdict         nullable  — "trigger" | "irrelevant"
  last_state           nullable  — the state sentence that went into the escalation
  version, created_at, updated_at
```

The three `evaluated_*`/`last_*` columns are a **cache of the relevance check**, not state the owner or
the model can set. Clear all three whenever `instruction` is edited.

Four deliberate absences:

- **No `archived_at`, no `active`.** A trigger that never fires is the same as one that does not
  exist. `_create_proposal` probes with `getattr(entity, "archived_at", None)`
  ([ai/service.py:1244](../src/safwa/ai/service.py:1244)), which is `None` for a model without it, so
  nothing breaks.
- **No `condition` column.** A stored predicate would be a second description of the same thing the
  instruction already describes, free to drift out of sync with it. The relevance session reads the
  instruction directly.
- **No FK to a subject Card or Check.** The id lives inside `instruction` as text. One mechanism, not
  two that can disagree. The cost: no cascade when a Card is deleted — the relevance session notices
  the item is gone and returns `irrelevant`, which is the intended path anyway.
- **No `escalation_proposal_id`.** Rule 8 already blocks every escalation while any proposal is open,
  so a per-row pause would be dead weight.

Derived, never stored twice:

```python
repeating = schedule_kind in {"interval", "daily", "weekly"}
          # equivalently: interval_minutes is not None or bool(weekdays)
```

`anchor_at` is a **floor: no fire is ever scheduled before it.** On a `once` schedule it is the moment
itself. On a recurrence it constrains only the first fire — after that `next_fire_at` is already past
it and the floor costs nothing to keep.

**Advancing `next_fire_at` must not bump `workspace.revision`.** It is bookkeeping, not a user-visible
mutation, and bumping it would break the revision skip below — that is why the scheduler writes the
column directly instead of going through a `domain.py` mutation.

### Constants (all in [constants.py](../src/safwa/constants.py))

| Name | Value | Meaning |
|---|---|---|
| `SCHEDULER_POLL_SECONDS` | 30 | poll interval (already exists) |
| `REMINDER_FIRE_BATCH` | 3 | max Reminders per escalation |
| `REMINDER_CATCHUP_GRACE_MINUTES` | 120 | how late a missed repeat may still fire |
| `REMINDER_MIN_INTERVAL_MINUTES` | 5 | floor for `interval_minutes` |
| `MINI_SESSION_REPAIR_ROUNDS` | 3 | retries on an invalid terminal tool call |
| `RELEVANCE_MAX_TOOL_CALLS` | 6 | cap for the relevance session |

## Schedule resolution

### The setup mini-session

Runs inside `_create_proposal`, **before** the `ChangeProposal` row is written, so the review screen
shows a resolved schedule instead of free text. It **writes nothing** — its only output is the
parameter set that goes into the proposal payload.

Context: current local datetime, timezone, the free-text `when`, the instruction. Tools: exactly two,
of which exactly one must be called. It has **no** `query_safwa` — it resolves timing, nothing else.

```
set_reminder_config(
  days:             ["Mon","Tue",…] | null,      # all seven ⇒ every day
  time:             "HH:MM" | null,              # local wall clock
  date:             "dd.mm.yyyy" | null,         # local calendar date
  interval_minutes: int | null,
  quiet_windows:    ["HH:MM-HH:MM", …] | null,   # interval schedules only
)

not_clear_enough(reason: str)
```

**What `date` and `time` mean depends on what they sit next to.** `interval_minutes` or `days` sets the
rhythm; `date`/`time` then say when that rhythm **starts** and it runs from there on. With neither of
them present, `date`/`time` are the moment itself. Two rules cover every combination:

- **`date` is always a start date**, never a fire clock.
- **`time` is the fire clock when `days` is set** ("at 08:30"), and a start clock otherwise.

| Parameters given | `schedule_kind` | Meaning |
|---|---|---|
| `interval_minutes` alone | `interval` | starts now, then every N minutes |
| `interval_minutes` + `time` | `interval` | starts at that clock's next occurrence, then every N |
| `interval_minutes` + `date` + `time` | `interval` | starts at that exact moment, then every N |
| `days` + `time` | `weekly`, or `daily` when all seven | first fire is the next matching day |
| `days` + `time` + `date` | same | first fire is the first matching day **on or after** `date` |
| `date` + `time` | `once` | that moment, then the row deletes itself |
| `time` alone | `once` | its next occurrence |
| `date` alone | → `not_clear_enough` | which hour? — true even with an interval |
| nothing | → `not_clear_enough` | — |

`quiet_windows` stay interval-only and are unaffected by a start: they suppress hours of the day, the
anchor suppresses everything before one instant.

`not_clear_enough` is expected often: "every morning", "soon", "twice a week" (which days?), a one-shot
date already gone, contradictory timings. Its `reason` comes back as a retryable `ToolPreparationError`
([ai/service.py:245](../src/safwa/ai/service.py:245)), so the advisor asks the owner that exact
question. A guessed time is discovered only when the Reminder fires at 03:00.

Reject: `interval_minutes < REMINDER_MIN_INTERVAL_MINUTES`; a past moment **on a `once` schedule**;
quiet windows on a non-interval schedule; quiet windows leaving no gap in the day.

A past start on a *recurring* schedule is not an error — it means "already started", so the first fire
is just the next occurrence from now. Nothing is backfilled. Only a one-shot in the past is
unsatisfiable.

### Then: an ordinary proposal

The resolved parameters become an `AgentChange(entity="reminder", action="create"|"update")` and then
a `ChangeProposal` + `ProposalChange` pair — the same path a Card takes. The owner sees:

```
<b>Proposal</b>
Create Reminder

Text: Ask me what to start with today.
Schedule: every weekday at 08:30
First fire: tomorrow 08:30

[ ✅ Save ]  [ ❌ Discard ]
```

Only **Save** calls `domain.create_reminder`. Discard leaves no row. The `Schedule` and `First fire`
lines both come from `describe()`, so what the owner approves is exactly what the loop will do — the
whole reason the mini-session runs before the proposal rather than after it.

### `next_fire` — the whole algorithm

One pure function in [reminders.py](../src/safwa/reminders.py), alongside `describe()` and
validation. Four
steps, run in full **every** time:

```
example: last fire 21:00, interval 120, quiet window 22:00-09:00

1. candidate = 21:00 + 2 h                    → 23:00 UTC
2. convert candidate to LOCAL time            → 23:00 local
3. is it inside a quiet window?               → yes
4. move it to the window's END                → 09:00 local next day → store as UTC
```

**Step 2 is redone on every call and never cached.** After a daylight-saving shift "09:00 local" is a
different UTC instant than yesterday, so arithmetic done purely in UTC would land on the wrong side of
the window. Re-converting each time makes DST a non-issue.

**The anchor comes before all four steps.** The first-ever fire of a recurrence is not computed from
"last fire" — there isn't one. It is `max(anchor_at, now)` for an interval, and for `daily`/`weekly`
the first matching (weekday, `at_time`) at or after that same floor. Steps 1–4 then produce every fire
after it, and `anchor_at` is never consulted again.

A window whose start is later than its end wraps midnight: `"22:00-09:00"` and
`["22:00-00:00","00:00-09:00"]` mean the same thing. A window's end is **exclusive**, which
is why the split form ends at `00:00` — `"22:00-23:59"` leaves the last minute of the day
open and a candidate would stop inside it.

For `daily` and `weekly`, the same rule applies without windows: build the next matching local wall
clock, then convert to UTC — so 08:30 stays 08:30 across a DST shift.

### Catch-up after downtime

- **One-shot overdue** — always fires, however late. The escalation states how late, then the row is
  deleted. Invariant: a one-shot produces exactly one escalation, ever.
- **Repeating overdue** — at most **one** catch-up, and only if the most recent missed occurrence is
  within `REMINDER_CATCHUP_GRACE_MINUTES`; otherwise roll `next_fire_at` forward silently. A weekend
  offline must not produce 32 posture escalations.

## The relevance check

Runs at fire time. Its job is to save the main advisor a lookup: find every `#id` the instruction
names, read its current state, say in one sentence what that state is, and judge whether the Reminder
still makes sense.

Context: planning state, memory, the instruction, current local datetime. Tools: `query_safwa` and one
terminal tool, capped at `RELEVANCE_MAX_TOOL_CALLS`.

```
complete_relevance_check(verdict: "trigger" | "irrelevant", state: str)
```

`state` is one or two sentences naming each item and its current stage/outcome. It is pasted verbatim
into the escalation under `State:`.

| Verdict | Meaning | Effect |
|---|---|---|
| `trigger` | the items are still live | `state` goes into the escalation as written |
| `irrelevant` | the thing the instruction watches is Done, Cancelled or deleted | same, but flagged `NO LONGER RELEVANT` |

**Both verdicts escalate.** `irrelevant` is not a silent delete — the advisor decides whether it
deserves a `remove` proposal, and the owner decides whether to approve it.

There is no third "not now" verdict. That would be a firing gate, and gates are quiet windows.

### Two skips

Neither is an optimisation guess; both are exact.

**No `#id` in the instruction** ⇒ there is nothing to look up. Skip the session entirely, verdict
`trigger`, no `State:` line. A plain `re.search(r"#\d+", instruction)`. This is why "the instruction
names items by `#id`" is a prompt rule and not a nicety: it is what makes a text-only Reminder free.

**Nothing changed since last time** ⇒ the same lookup returns the same answer, because the session
reads only Safwa data and every real mutation bumps `workspace.revision`:

```python
if reminder.evaluated_revision == workspace.revision:
    verdict, state = reminder.last_verdict, reminder.last_state   # skip the session
```

This holds *only* because the session carries no clock into its judgement and because advancing
`next_fire_at` does not bump the revision.

### Writing instructions

The instruction carries the ids, so the main advisor resolves *"the posture Card"* into `#42` **once**,
at authoring time, with the `query_safwa` it already has. The relevance session then re-reads that id
on every fire.

| The owner said | Instruction stored | Schedule |
|---|---|---|
| "every 2 h between 9 and 22, check my posture" | `Check my posture — Check #5 "Is my posture straight?"` | `interval=120`, `quiet_windows=["22:00-09:00"]` |
| "every morning while the posture Card is open" | `Ask how Card #42 (posture) is going.` | `days=[all seven]`, `time="09:00"` |
| "remind me about the launch until it ships" | `Review the launch plan — Card #88.` | … |
| "ask me each morning what to start with" | `Ask me what to start with today.` | `days=[all seven]`, `time="08:30"` |

The last row names no id and so never runs a session.

## The escalation turn

### The gate

```python
can_escalate = (
    not guard.active
    and no ChangeProposal with status == 'pending'
    and no AgentStep(kind='approval_batch') left unresolved
)
```

An open proposal is an unanswered question; raising a second one on top of it — one the owner did not
even initiate — turns the chat into a stack of screens. "Resolve completely" includes the model's
continuation after the last queue item (`resolve_approval` → `continue_agent_approval`).

Blocked escalations are **not** queued anywhere. `next_fire_at` is simply not advanced, so the rows
stay due and the next tick retries.

### The turn

1. Gate closed → stop. Advance nothing.
2. Take the guard, marked **background** (see below), and call `AIAdvisor.handle` with the main system
   prompt and the formatted escalation as the request. `dialogue=None` — it builds only the cacheable
   prefix, needs no session boundary, and never re-reads Telegram.
3. Do **not** pass `allow_silence`. An escalation is a real request; an empty turn is an upstream
   failure, retried by `AI_EMPTY_RESPONSE_ATTEMPTS` and then raised. That flag exists for resuming
   after an approval queue, where the receipts are already the answer.
4. Register the reply as **`MessageKind.REMINDER`**, not `DIALOGUE_ASSISTANT`. That kind already means
   "proactive bot message" and is already included in dialogue, so the model later reads it as
   something it volunteered rather than an answer to a message that is not there.
5. Only after the turn succeeds: delete the one-shots, advance the repeats, write
   `evaluated_revision` / `last_verdict` / `last_state`.

Because `dialogue=None`, the instruction text must stand on its own. The setup prompt says so; there
is no validator behind it.

### Rule 9: the owner always wins

`OwnerAndWritingMiddleware` ([telegram/_core.py:100](../src/safwa/telegram/_core.py:100)) currently
**deletes** any owner message that arrives while the guard is held. That rule exists to keep history
consistent with what the running answer is being generated from — but an escalation is generated with
`dialogue=None` and never reads the conversation, so here it protects nothing and costs a message.

`GenerationGuard` therefore records whether the holder is the owner or a background escalation:

- owner event + **background** holder → cancel the escalation, hand the guard to the owner, exactly as
  `/newsession` does today;
- owner event + **owner** holder → unchanged behaviour.

Nothing is lost. The half-finished turn is discarded, `next_fire_at` was never advanced, so the row is
still due and the next tick picks it up.

## Deletion

| Trigger | Path | Approval |
|---|---|---|
| a one-shot fired | same transaction, after the turn succeeds | none |
| relevance check returned `irrelevant` | in the escalation → advisor proposes `remove` | Save |
| the owner decides | `/reminders` → 🗑 | one confirm |
| the model notices in an ordinary turn | `remove(type="reminder")` | Save |

No backoff: if the check keeps saying the thing it watches is gone, Safwa keeps saying so. Silence
would hide a Reminder watching something that no longer exists.

## What the rework removed

`ReminderPolicy` and its 13 candidate kinds, and `send_reminder`'s `{"send","message"}` veto in
`main.py`. Everything that existed only to ration unsolicited nudges went with them:

| Removed | Why |
|---|---|
| `proactive_limit`, `reminder_cooldown_minutes`, `weekend_enabled`, `morning_checkin`, `evening_checkin` | they rationed nudges nobody asked for; a Reminder the owner set needs no daily cap |
| `quiet_start`/`quiet_end`, `wake_time`/`bed_time`, and `/setquiet`, `/setwake`, `/setbed` | suppression is a per-Reminder quiet window now |
| `reminder_state` table | the global snooze moved to `UserProfile.reminders_snoozed_until`, so `snooze_reminders` is a profile update and `/snooze` is unchanged from outside |
| `scheduled_jobs` table | never written, never read |

Kept: `reminders_enabled` (the master switch) and `MessageKind.REMINDER` at code 5
(`_KIND_MARK_CODES` is append-only).

`/settings` is now `/setabout`, `/setadvisor`, `/setcapacity`, `/setmemtime`.
Tables: 28 − 2 + 1 = **27**. Bot commands: 21 − 3 + 1 (`/reminders`) = **19**.

**Not in this plan:** `FeedbackQueue` is not stranded — only the `feedback` *candidate kind* was. It
is still used by `/feedback`, `/status`, `domain.finish_action`, `Card.liked` and
`analytics.retrospective_recommendations`. Same for a "Today ends at HH:MM" setting. Both parked.

## AI surface

```
reminder(mode: "create" | "edit",
         id: int | null,
         instruction: str,        # handed over at fire time; self-contained, names items by #id
         when: str | null)        # free text — "every weekday at 8am", "in 90 minutes"
                                  # required on create; omit on edit to keep the schedule (rule 10)
```

- `when` omitted on `edit` ⇒ no setup mini-session runs and no schedule column is touched. That is how
  rule 10 is enforced in code rather than by prompt.
- `AgentChange.entity` gains `"reminder"`; `RemoveToolInput.type` gains `"reminder"`, and
  `mutation_change_from_tool` maps its removal to `delete` — there is no archive.
- One new branch each in `MUTATION_TOOL_MODELS`, `MUTATION_TOOL_DESCRIPTIONS`, `_create_proposal`'s
  model map, `_proposal_display_line`, `_proposal_result_details`, `ProposalService.apply`. All of
  them render the schedule through the single `describe()` helper, so the proposal screen, the
  `/reminders` list and the escalation text can never disagree.
- `ai_reminders` view — `(id, instruction, schedule, next_fire_at_local, last_fired_at, fire_count)`
  — must be added in **three** places: `create_ai_views`, `ALLOWED_VIEWS`
  ([ai/sql.py:24](../src/safwa/ai/sql.py:24)), and the view list inside `SYSTEM_PROMPT`
  ([ai/context.py:28](../src/safwa/ai/context.py:28)). Missing any one fails differently.
- A `# Reminders` section in `SYSTEM_PROMPT`: a Reminder is a trigger whose text arrives as a request;
  the instruction must stand alone and must name every item it concerns by `#id` (resolve the id with
  `query_safwa` before proposing); timing is free text resolved elsewhere and is never edited by a
  text change; a one-shot deletes itself; when an escalation arrives, answer it like any other request.
- **Do not** inject Reminders into `planning_context` — `next_fire_at` moves on every fire and that
  block is the cacheable prefix. The model reads them through `query_safwa`.

**Trap:** `_on_proposal_approve` ([telegram/callbacks.py:956](../src/safwa/telegram/callbacks.py:956))
routes **any** change with `action == "delete"` into the "Final destructive confirmation" screen,
whose copy is about permanently removing a Card tree. That check must become entity-aware; a Reminder
needs one Save, not two.

## UI surface

`/reminders` → a list, one `token_button` per Reminder (`describe()` + truncated instruction), plus
`menu_row()`. Paginate with the existing `paginate`. Tap → detail:

```
<b>Reminder</b>
Every weekday at 08:30 · next today 08:30
Fired 14 times · last yesterday 08:30

Ask me what to start with today.

[ ✏️ Text ]  [ 🗑 Delete ]
[ ↩️ Back ]
```

- **Text is the only editable field.** The schedule is read-only, with one sentence saying the advisor
  changes it. Reuse `UiSession(kind="reminder_text")` + `delete_text_input` +
  `edit_registered_message`, exactly like `render_item_text_prompt`
  ([telegram/items.py:155](../src/safwa/telegram/items.py:155)). It must not touch `next_fire_at`, and
  it must clear `evaluated_revision` / `last_verdict` / `last_state`.
- **No creation button** — advisor-only, like Requests.
- Delete asks one confirmation, reusing the `item_archive_prompt` two-step shape.

New `telegram/reminders.py` at the `items.py` / `checks.py` layer; handlers stay in `commands.py` and
`callbacks.py`, still the only modules registering `@router` handlers. Add `nav:reminders` to
`menu_markup` and the `navigation` map.

## Startup

`recover_startup` ([recovery.py:19](../src/safwa/recovery.py:19)) gains `reconcile_reminders`:

1. roll `next_fire_at` forward for repeating Reminders overdue beyond the grace window;
2. leave in-grace overdue Reminders alone — the first poll fires them, and that *is* the catch-up;
3. recompute wall-clock schedules if `workspace.timezone` changed since the last run.

**`SAFWA_SCHEDULER_ENABLED` defaults to `False`** ([config.py](../src/safwa/config.py)) and must flip
to `True`, or Reminders never fire on a default install.

## Known weaknesses

1. **Cost.** Every fire is a full advisor turn — main system prompt, planning state, memory, whole
   toolset. A 2-hour posture Reminder is ~7 turns a day. `dialogue=None` keeps the prefix
   byte-identical so prompt caching should hit nearly all of it, batching helps when fires coincide,
   and the two skips mean the relevance session runs only when an item is named *and* the data moved
   — but this is still materially dearer than the old nudge path on a metered provider. Measure before
   leaning on intervals.
2. **Self-contained instructions are unenforced.** With `dialogue=None`, "remind me to continue what
   we discussed" is unanswerable at fire time. Same for a missing `#id`: the relevance session is
   skipped and the advisor gets no state. Both are prompt rules with no validator. The failure is a
   vague reminder, not a broken one.
3. **Recursion.** A background turn holds the `reminder` tool and could reschedule Reminders. The guard
   serialises it and only one turn runs per poll, so it cannot run away — but say so in the prompt.
4. **`workspace.revision` mid-turn.** `dialogue.ordinary_text` discards an answer if the revision moved
   while generating. A background turn has no equivalent check. Probably fine, but decide it
   deliberately rather than omitting it.

## Rebuilding the database

No migrations, no Alembic. `models.py` is the only schema source and `create_all` never alters an
existing table, so adopting this needs `uv run safwa-backup` and a fresh `data/safwa.db`.

`UtcDateTime` ([models.py](../src/safwa/models.py)) exists because SQLite has no time zone type:
`DateTime(timezone=True)` accepts an aware value and returns a naive one, so `now - next_fire_at`
raises. The rest of the codebase patches this per-site; a table that is nothing but datetime
arithmetic gets it in the column instead. The DDL is unchanged, so it is not a schema change.

### Tests

- `tests/test_reminders.py` — `next_fire` for every shape, quiet windows including the midnight
  wrap, a DST boundary, the one-shot rule, catch-up roll-forward. Anchors specifically: a future start
  on an interval and on a weekly (nothing fires before it), a `days`+`date` start landing on a
  non-matching weekday, a past start on a recurrence (first fire is next occurrence, no backfill), and
  a past one-shot (rejected).
- `tests/test_scheduler.py` (rewritten) — the gate advancing nothing, batch cap, one-shot deleted only
  after a successful turn, both verdicts, both skips (no `#id`, unchanged revision), the escalation
  text itself.
- `tests/test_telegram_*.py` — an owner message cancelling a background escalation and leaving
  `next_fire_at` untouched.
- `tests/e2e/` — advisor → setup mini-session → **proposal → Save** → correct `next_fire_at`, and
  Discard → no row; `irrelevant` → escalation → deletion proposal; `/reminders` → detail → text edit
  not moving `next_fire_at` but clearing the verdict cache.

Real SQLite and real services, `ScriptedProvider` at the provider boundary only.

## Deferred

- Monthly and yearly shapes — one more branch in `next_fire`.
- Per-Reminder snooze; the global `/snooze` is enough for now.
- Adaptive poll sleep (`min(next_fire_at) - now`, clamped to 5–30 s) for minute-accurate delivery.
- Removing feedback; the "Today ends at HH:MM" day boundary.
- Reminder-authored Checks — a Reminder points at an existing Check, it does not create one.
