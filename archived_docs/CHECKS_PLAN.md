# Safwa — Checks (design record)

Why the entity is shaped the way it is. **Implemented**; the behaviour itself is now described in
[INITIAL_PLAN.md](INITIAL_PLAN.md) (product spec), [ARCHITECTURE.md](ARCHITECTURE.md) (orientation),
and [STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md) (module index). This file keeps the reasoning and the
rejected alternatives, which those documents do not carry.

## What a Check is

A Check records a state observation, not planned work. It answers "did this hold?", where a Card
answers "what do I do?". Two motivating cases:

- **Checklist** — one Card "Go to the market" with linked Checks "milk", "bread". No Card per item.
- **Probe** — "is my posture straight?", answered many times a day, where the value is the trend.

A Check carries no effort and never enters Sprint accounting or effort derivation. It is item-shaped,
like Tag and Value, not Card-shaped.

## Entity


```
checks
  id, title
  repeatable         bool      — respawn a Pending successor on resolve
  outcome            nullable  — passed | missed
  resolved_at        nullable
  resolved_by        nullable  — ActorType; who supplied the outcome
  series_id          nullable  — groups one Check's successors, for trend queries
  source_instance_id nullable  — the row this one was cloned from
  version, created_at, updated_at

card_checks (card_id, check_id)   the one Check relationship, mirroring card_values / card_tags
```

`series_id` and `source_instance_id` reuse the Card repeat pattern exactly
([models.py:107](../src/safwa/models.py:107), set in `_copy_repeat_successor`
[domain.py:1013](../src/safwa/domain.py:1013)). Do not invent a second lineage vocabulary.

`CheckOutcome` is a `StrEnum`; the column stores plain strings, so always compare and assign `.value`.

## Derived state

**`Pending` is derived, never stored**: `outcome IS NULL`. There is no reset path and therefore no
reset bug. Only two settable outcomes exist.

Invariant: at most one Pending Check per `series_id`. Guard it — every double-spawn path is otherwise
silent until the Done-gate fires twice.

### The two outcomes

`passed` and `missed`. Pending covers "not answered", and a Check that stops matching reality is
removed rather than answered sideways.

The stored value and the shown word are the same word: `CHECK_OUTCOME_LABELS` only capitalizes it, so
nothing the model reads and nothing the owner reads can drift apart.

### Re-answering overwrites, and that is accepted

A resolved Check can be re-answered. The previous outcome is overwritten and lost. There is no
`card_events`-style audit table for Checks.

This keeps Checks consistent with every other entity — Card text, effort, and priority are all
editable through the normal proposal diff, so a write-once field would have been the exception. The
data actually lost is "the user once answered `missed` before correcting it", which nothing in a
single-owner tool reads.

Two rules follow, and both are easy to get wrong:

- **A successor spawns on the Pending → resolved transition only.** Re-answering an already-resolved
  Check changes `outcome` and nothing else. Without this, each correction spawns another successor,
  breaking the one-Pending-per-`series_id` invariant and leaving rows the Done-gate will trip on.
- **`resolved_at` records the first resolution, not the latest edit.** It is the observation time the
  trend is keyed on; correcting a typo weeks later must not move the data point. `updated_at` from
  `TimestampMixin` already carries the edit time. `resolved_by` tracks the current answer's actor.

Re-answering cannot return a Check to Pending — Pending is `outcome IS NULL` and the editor only sets
`passed` or `missed`. A Done Card therefore cannot be re-blocked by editing its Checks, and since both
outcomes satisfy the gate, re-answering a Check on a closed Card changes nothing about that Card.

## Repeat semantics

Two orthogonal respawn mechanisms. Both are legitimate; they must not both fire for one Check.

| Mechanism | Trigger | Case |
|---|---|---|
| Check `repeatable` | the Check is resolved | probe — posture respawns Pending on the same Card |
| Card repeat | a linked Card is finished | checklist — the market list returns with the successor Card |

Checklist items set `repeatable = False`; you buy milk once per trip. Setting it true on a checklist
item produces two rows per cycle. Document this at the field.

A successor Check copies title and repeatability, carrying `series_id` and setting
`source_instance_id`. Same fields the Card successor copies.

It is linked to the source's **live Cards only** — an archived or terminal Card is dropped from the
successor's link set, and when that leaves nothing the series ends instead of spawning. Without this, a
repeatable Check guarantees a Pending row forever and its Card can never reach Done: the two features
deadlock. Sharing does not weaken the rule, it narrows it — a Check on a closed Card and a live one
keeps going for the live one.

The Card-repeat clone is the mirror case: it links the copy to the **successor Card only**. The other
Cards in the series' link set keep their own rows; the repeat belongs to the Card that repeated.

### Suppressing the spawn

`repeatable` is never cleared. `_apply_check_outcome` takes `spawn=False` on both gate paths, so
resolving through the gate cannot put a new Pending row on the Card it just unblocked, and
`_spawn_check_successor` independently refuses to spawn onto a terminal or archived Card. A Check only
spawns on its Pending → resolved transition, so an already-answered row cannot spawn later.

### The closed instance is off-limits to the model

An answered repeatable Check is a spent instance: its series lives on the newer row, and an edit to
the closed one never reaches it. `ChangePreparer.prepare` therefore refuses every proposal that
targets one or names one in a Card's `check_ids`, with `live_repeat_instance_id` in the hint so the
retry lands on the open row. Only the newest instance can be open, so that lookup is one query.

The model is told which row is spent before it proposes anything: `ai_checks.title` (and `ai_cards.title`)
names a closed instance ` Posture straight? [🔄1]`, numbered by its place in the series. The marker is
rendered, never stored — the owner's title is untouched, and `render_citations` puts the same marker on
a citation link so a closed instance cannot pass for the open one in the chat either.

The owner keeps the manual screen: they navigated to the row they are editing, and correcting a past
answer is what `resolved_at` already accounts for. A closed screen carries a `🔄 Current` button to the
live instance when the series still has one. The same rule covers repeatable Cards, where it
additionally forbids reopening a closed one from either side — two live instances of one series is
what the series exists to prevent.

## Done-gate

`finish_action` raises `DomainError` when the Card has Pending Checks and `terminal_stage` is Done.

- Gate `Done` only. Cancelling a Card with Pending Checks is legitimate — abandoning work.
- Both `passed` and `missed` satisfy the gate. Only Pending blocks.
- A shared Check gates every Card it hangs on, and one answer clears all of them at once.
- No gate on `archive_subtree`. Both `archive_subtree` and `delete_subtree` reach Checks, but only
  those the subtree still holds alone: a Check another live Card needs is neither archived nor
  deleted, and `delete_subtree` removes the link rows explicitly rather than trusting the FK cascade,
  which is a per-connection pragma.
- The gate lives in `finish_action` alone. Goal and Idea are never finished directly, so
  `propagate_ancestors` ([domain.py:886](../src/safwa/domain.py:886)) — which only recomputes derived
  stage and has no error path — is untouched.

The error message carries the Pending Check **ids and titles**, not ids alone. `MAX_REPAIR_ROUNDS = 5`;
inlining titles saves the model a `query_safwa` round.

`finish_action` takes `check_outcomes`, a mapping that must cover exactly the Card's own Pending
Checks. It never touches a standalone Check.

## Manual flow

`Done` on a Card with Pending Checks opens a resolution screen instead of finishing:

- every Pending Check listed with a `✅ Passed` and a `❌ Missed` button, nothing prefilled;
- `Save` appears once an answer is set; it resolves them and finishes the Card in one transaction, and
  is refused while any Check is still unanswered;
- `Back` cancels the move to Done and changes nothing.

Transient state lives in a new `UiSession` kind, alongside `card_create` — deleted on navigation,
persisting nothing until Save.

## AI flow

1. Model calls `card(mode="update", id=A, stage="done")`.
2. Preparation fails. [`_create_proposal`](../src/safwa/ai/service.py:858) is the verifier; a plain
   `DomainError` raised during preparation is already wrapped into a model-visible retryable
   `ToolPreparationError` at [ai/service.py:1103](../src/safwa/ai/service.py:1103). No new plumbing.
3. Model reads the Pending ids and titles from the error. For an answer the user already gave it
   proposes `check(mode="complete")` (Passed) or `check(mode="cancel")` (Missed), one proposal per
   Check; otherwise it cites them as `[Milk](check:14)`, and the link leads to the manual screens,
   which write at once.
4. Save applies the same `resolve_check` the manual screen calls.
5. The batch resolves, the model resumes through `resolve_approval` → `continue_agent_approval` with
   every mutation result, sees the Checks are resolved, and re-proposes the close.
6. The user saves the close.

Check mutations call `_bump_workspace` like every other `domain.py` mutation, so a manual resolve
invalidates an in-flight proposal through the normal `StaleStateError` path.

Every proposal screen is read-only: exactly Save and Discard, Checks included. A decision only the
user can make is handed over by opening the item, not by putting a control on a proposal.
INITIAL_PLAN and MEMORY_HISTORY_USAGE both record it.

## UI surfaces

The Check list hangs off a Card, because the Card side is where the link lives; a single Check screen
stands on its own, since an advisor citation reaches one that hangs on no Card.

- The Card screen carries `☑️ Checks (pending/total)` **only when at least one Check hangs on the
  Card**. A first Check arrives through an AI proposal, so an empty list would lead nowhere.
- The manual screens answer Checks and nothing else. Creating one, renaming it, and linking or
  unlinking it are proposal-only, so the list carries just the Checks and Back, and the Check screen
  carries `🔁 Repeat: On/Off` plus `✅ Passed` / `❌ Missed`, which write the outcome on the spot.
- There is no Archive Check action. A Check's states are Pending, Passed, and Missed; the
  `archived_at` column stays, but only the Card archive/delete cascade writes it.
- Tag and Value screens have no Checks button. They no longer classify Checks.
- Checks are item-shaped: reuse the `render_item_editor` pattern
  ([telegram/items.py:21](../src/safwa/telegram/items.py:21)), not the Card renderer.
- Dashboards still list Actions only. Checks do not appear there.

**Residual gap, accepted for now**: a Check linked to no Card is reachable only through the AI, which
finds it in `ai_checks` and cites it. A `/checks` command is deferred.

## Deferred

Cadence. `repeatable` means "respawn on resolve"; it does not mean "ask me every four hours". Nothing
in this plan fires a Check.

Consequence: the checklist case works on day one, the probe case needs a Reminder. That rework has
since landed ([REMINDERS_PLAN.md](REMINDERS_PLAN.md)) and it *replaced* the 13 deterministic kinds
rather than adding to them: a Reminder is user-authored text plus a schedule, and the posture case is
a Reminder whose text names the Check by `#id`. Budgeting is no longer a problem to solve — the owner
sets the frequency, so there is nothing to ration.

## AI surface

The link is a **Card relationship, so the `card` tool writes it** — `check_id` / `check_ids` /
`check_query`, the third group beside Values and Tags, in `create`, `edit`, `link` and `unlink`. The
`check` tool creates, edits and answers a Check and nothing else; it takes no `card_id` at all.

`ReferenceSpec` grew one field for this, `name_attr`, because a Check is named by `title` while a Tag
and a Value are named by `name`. Everything else — resolution, the proposal diff, approval-time
application — is the shared path. Check titles are **not** unique, so a `check_query` matching several
live Checks resolves to `ambiguous` and the model is told to use ids; that path already existed for
Tags and Values, it just never fired there.

## Build notes

Before the first release there are no migrations or Alembic. The table changes mean the existing
`data/safwa.db` will **not** match until it is rebuilt — run `uv run safwa-backup`, then recreate the
database. Migration support begins after v1.

`ai_checks` must be in `ALLOWED_VIEWS` ([ai/sql.py](../src/safwa/ai/sql.py)) **and** in the view list
inside `SYSTEM_PROMPT` ([ai/context.py:20](../src/safwa/ai/context.py:20)). Either alone leaves the
model unable to use it. `ai_cards` gained `direct_checks` and `pending_checks`, so the prompt's column
list for it must be updated too.

Touched: `models.py`, `enums.py`, `domain.py`, `constants.py`, `ai/sql.py`, `ai/context.py`,
`ai/contracts.py`, `ai/service.py`, `telegram/checks.py` (new), `telegram/cards.py`,
`telegram/items.py`, `telegram/callbacks.py`, `telegram/dialogue.py`, `telegram/proposals.py`, plus
`INITIAL_PLAN.md`, `MEMORY_HISTORY_USAGE.md`, `ARCHITECTURE.md`, `STRUCTURE_GRAPH.md` and `CLAUDE.md`.

Tests: `tests/test_checks.py` (domain invariants, including sharing across Cards) and
`tests/e2e/test_checks_e2e.py` (gate → answer → complete, a citation for the screen the user acts on,
plus linking by `check_ids`, by `check_query` title, and through the manual screen, against real
SQLite and a `ScriptedProvider`).
No `--live-telegram` case covers Checks yet.

## Open

Nothing blocking. Both earlier questions are settled above: two distinct outcomes named
`passed | missed`, and re-answering allowed with the previous outcome overwritten
and no audit table.
