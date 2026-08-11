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
  id, title, note
  repeatable         bool      — respawn a Pending successor on resolve
  outcome            nullable  — passed | failed | not_applicable
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
reset bug. Only three settable outcomes exist.

Invariant: at most one Pending Check per `series_id`. Guard it — every double-spawn path is otherwise
silent until the Done-gate fires twice.

### Why three outcomes, and why these names

The three stay distinct because they record different **causes**, and cause is what the trend is for.
Three `failed` in a week means the user is slipping; three `not_applicable` means the Check does not
match reality and should be deleted. Collapsed, the two are indistinguishable in exactly the case
worth acting on.

`passed` rather than `checked`: "posture Check — checked" reads as *the Check was performed*, not
*the posture was good*, and that ambiguity would sit in the most-queried field. Passing implies
performing. `failed` is its symmetric partner and reads correctly for both cases.

There is deliberately no `skipped`. It would mean "not answered", which is what Pending already
means. `not_applicable` is answered — the answer is that the question does not apply.

Enum values are not labels. Store `failed`; render "Missed" if that reads better on a checklist, the
way `kind_label` and `CHOICE_TITLES` already separate the two
([telegram/_presentation.py:39](../src/safwa/telegram/_presentation.py:39)).

### Re-answering overwrites, and that is accepted

A resolved Check can be re-answered. The previous outcome is overwritten and lost. There is no
`card_events`-style audit table for Checks.

This keeps Checks consistent with every other entity — Card text, effort, and priority are all
editable through the normal proposal diff, so a write-once field would have been the exception. The
data actually lost is "the user once answered `failed` before correcting it", which nothing in a
single-owner tool reads.

Two rules follow, and both are easy to get wrong:

- **A successor spawns on the Pending → resolved transition only.** Re-answering an already-resolved
  Check changes `outcome` and nothing else. Without this, each correction spawns another successor,
  breaking the one-Pending-per-`series_id` invariant and leaving rows the Done-gate will trip on.
- **`resolved_at` records the first resolution, not the latest edit.** It is the observation time the
  trend is keyed on; correcting a typo weeks later must not move the data point. `updated_at` from
  `TimestampMixin` already carries the edit time. `resolved_by` tracks the current answer's actor.

Re-answering cannot return a Check to Pending — Pending is `outcome IS NULL` and the editor only
cycles the three settable outcomes. A Done Card therefore cannot be re-blocked by editing its Checks,
and since all three outcomes satisfy the gate, re-answering a Check on a closed Card changes nothing
about that Card.

## Repeat semantics

Two orthogonal respawn mechanisms. Both are legitimate; they must not both fire for one Check.

| Mechanism | Trigger | Case |
|---|---|---|
| Check `repeatable` | the Check is resolved | probe — posture respawns Pending on the same Card |
| Card repeat | a linked Card is finished | checklist — the market list returns with the successor Card |

Checklist items set `repeatable = False`; you buy milk once per trip. Setting it true on a checklist
item produces two rows per cycle. Document this at the field.

A successor Check copies title, note, and repeatability, carrying `series_id` and setting
`source_instance_id`. Same fields the Card successor copies.

It is linked to the source's **live Cards only** — an archived or terminal Card is dropped from the
successor's link set, and when that leaves nothing the series ends instead of spawning. Without this, a
repeatable Check guarantees a Pending row forever and its Card can never reach Done: the two features
deadlock. Sharing does not weaken the rule, it narrows it — a Check on a closed Card and a live one
keeps going for the live one.

The Card-repeat clone is the mirror case: it links the copy to the **successor Card only**. The other
Cards in the series' link set keep their own rows; the repeat belongs to the Card that repeated.

### Suppressing the spawn, not clearing the flag

An earlier draft had `finish_action` clear `repeatable` on the Checks it resolves, to stop a repeatable
Check from putting a new Pending row on the Card it had just unblocked. That created an ordering
constraint — the repeat successor had to be cloned *before* the clearing, or the flag was lost after
one cycle — and the AI's two-step path (`resolve_for_card`, then complete) reintroduced the same
hazard on a second code path.

Suppressing the spawn removes the need entirely. `_apply_check_outcome` takes `spawn=False` on both
gate paths, `repeatable` is never touched, the clone carries the original flag whatever the order, and
`_spawn_check_successor` independently refuses to spawn onto a terminal or archived Card. Nothing is
lost: a Check only spawns on its Pending → resolved transition, so an already-answered row cannot
spawn later regardless of its flag.

## Done-gate

`finish_action` raises `DomainError` when the Card has Pending Checks and `terminal_stage` is Done.

- Gate `Done` only. Cancelling a Card with Pending Checks is legitimate — abandoning work.
- `passed`, `failed`, and `not_applicable` all satisfy the gate. Only Pending blocks.
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

- every Pending Check listed, defaulting to `failed`;
- tapping one cycles `passed` → `failed` → `not_applicable`;
- `Save` resolves them, clears `repeatable`, and finishes the Card in one transaction;
- `Back` cancels the move to Done and changes nothing.

The default is `failed`, not `passed`. A one-tap "all done" button is a lie-button: it lets the user
clear the gate by asserting checks that never happened, which corrupts the trend the entity exists to
record. Erring toward `failed` over-reports failure, which is the safe direction.

Transient state lives in a new `UiSession` kind, alongside `card_create` — deleted on navigation,
persisting nothing until Save.

## AI flow

1. Model calls `card(mode="update", id=A, stage="done")`.
2. Preparation fails. [`_create_proposal`](../src/safwa/ai/service.py:858) is the verifier; a plain
   `DomainError` raised during preparation is already wrapped into a model-visible retryable
   `ToolPreparationError` at [ai/service.py:1103](../src/safwa/ai/service.py:1103). No new plumbing.
3. Model reads the Pending ids and titles from the error and proposes a Check-resolution change.
4. The user sets each outcome on the proposal screen and presses Save. This applies the same domain
   function the manual screen calls.
5. The batch resolves, the model resumes through `resolve_approval` → `continue_agent_approval` with
   every mutation result, sees the Checks are resolved, and re-proposes the close.
6. The user saves the close.

The resumed tool result carries **per-Check outcomes**, not a bare "saved". Three `failed` answers are
information the model should have before it re-proposes.

Check mutations call `_bump_workspace` like every other `domain.py` mutation, so a manual resolve
invalidates an in-flight proposal through the normal `StaleStateError` path.

Clearing `repeatable` must be visible in the proposal diff — never an invisible side effect of a
`stage=done` call.

### Spec amendment required

The Check-resolution proposal screen exposes field controls. That contradicts two authoritative lines:

- INITIAL_PLAN: "Proposal screens expose exactly `Save` and `Discard`; fields cannot be edited inside
  AI review."
- MEMORY_HISTORY_USAGE: "no field controls are exposed."

The exception is deliberate and narrow: the model cannot know whether the user bought milk. The user
is not editing the model's proposal, they are supplying information the model has no access to. Both
documents must be amended when this ships, or a later session will read the spec and "fix" the screen.

Proposed wording: *proposal screens are read-only, except Check resolution, where the model proposes
which Checks to resolve and the user supplies the outcomes.*

## UI surfaces

Every Check screen hangs off a Card, because the Card side is where the link lives.

- The Card screen always carries `☑️ Checks (pending/total)` — always, not only when Checks exist,
  or the first one could never be added.
- That list offers `➕ Add Check` (create, linked here) and `🔗 Link Check` (hang an existing live
  Check on this Card too). The Check editor offers `Unlink from this Card`, which never deletes: the
  Check survives on its other Cards.
- Tag and Value screens have no Checks button. They no longer classify Checks.
- Checks are item-shaped: reuse the `render_item_editor` pattern
  ([telegram/items.py:21](../src/safwa/telegram/items.py:21)), not the Card renderer.
- Dashboards still list Actions only. Checks do not appear there.

**Residual gap, accepted for now**: a Check linked to no Card is reachable only through the AI, and
`🔗 Link Check` lists only the most recent `CHECK_LIST_LIMIT` candidates with no search. `ai_checks`
must therefore exist from day one — it is the sole escape hatch. A `/checks` command is deferred.

## Deferred

Cadence. `repeatable` means "respawn on resolve"; it does not mean "ask me every four hours". Nothing
in this plan fires a Check.

Consequence: the checklist case works on day one, the probe case does not. Posture is blocked on the
planned Reminder rework, where a reminder becomes user-authored text plus a time. That rework adds a
candidate source; it does not replace the 13 deterministic kinds in
[scheduler.py](../src/safwa/scheduler.py) — the trigger stays deterministic, only the content becomes
free text. `proactive_limit` budgeting for high-frequency probes is unsolved and belongs to that work.

## AI surface

The link is a **Card relationship, so the `card` tool writes it** — `check_id` / `check_ids` /
`check_query`, the third group beside Values and Tags, in `create`, `edit`, `link` and `unlink`. The
`check` tool creates, edits and resolves a Check and nothing else; its `card_id` survives only on
`resolve_for_card`, where it names the Card being completed and is not a link at all.

`ReferenceSpec` grew one field for this, `name_attr`, because a Check is named by `title` while a Tag
and a Value are named by `name`. Everything else — resolution, the proposal diff, approval-time
application — is the shared path. Check titles are **not** unique, so a `check_query` matching several
live Checks resolves to `ambiguous` and the model is told to use ids; that path already existed for
Tags and Values, it just never fired there.

## Build notes

No migrations and no Alembic. The table changes mean the existing `data/safwa.db` will **not** match
until it is rebuilt — run `uv run safwa-backup`, then recreate the database.

`ai_checks` must be in `ALLOWED_VIEWS` ([ai/sql.py](../src/safwa/ai/sql.py)) **and** in the view list
inside `SYSTEM_PROMPT` ([ai/context.py:20](../src/safwa/ai/context.py:20)). Either alone leaves the
model unable to use it. `ai_cards` gained `direct_checks` and `pending_checks`, so the prompt's column
list for it must be updated too.

Touched: `models.py`, `enums.py`, `domain.py`, `constants.py`, `ai/sql.py`, `ai/context.py`,
`ai/contracts.py`, `ai/service.py`, `telegram/checks.py` (new), `telegram/cards.py`,
`telegram/items.py`, `telegram/callbacks.py`, `telegram/dialogue.py`, `telegram/proposals.py`, plus
`INITIAL_PLAN.md`, `MEMORY_HISTORY_USAGE.md`, `ARCHITECTURE.md`, `STRUCTURE_GRAPH.md` and `CLAUDE.md`.

Tests: `tests/test_checks.py` (domain invariants, including sharing across Cards) and
`tests/e2e/test_checks_e2e.py` (gate → resolve → complete, plus linking by `check_ids`, by
`check_query` title, and through the manual screen, against real SQLite and a `ScriptedProvider`).
No `--live-telegram` case covers Checks yet.

## Open

Nothing blocking. Both earlier questions are settled above: three distinct outcomes named
`passed | failed | not_applicable`, and re-answering allowed with the previous outcome overwritten
and no audit table.
