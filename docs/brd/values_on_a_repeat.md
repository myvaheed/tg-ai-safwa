# A repeat carries its Values — approval packet

Status: **approved** 2026-08-26.
Batch: Phase 5.g
Sources: the approved `VL-CHECK-012`, `CH-DELETE-014`, `CD-LINK-012`, `VL-DELETE-015`,
`TA-DELETE-008`; `clone_checks_for_successor`, `_copy_check`, `apply_check_outcome`,
`drop_pending_checks`; the owner's ruling of 2026-08-26 — the Values move to the copy, and the
three strings that still say "planning state" come with it.

One new scenario, one rewritten line, and five fixes that need none. No schema change.

## Why the batch exists

A repeating Action carries everything it has to its next cycle except one thing.

The owner keeps "Pull-ups", a repeating Action, with a repeating Check "Did 20 pull-ups?" that
carries the Value "Health". They finish the Action and answer the Check.

| | On the new Card | Where the Value went |
|---|---|---|
| Values of the Card | Health | copied |
| Tags, Categories, Energy | copied | copied |
| The Check | there, Pending | **no Value at all** |

Health stayed on the answered Check, which stays on the closed Card, which is archived two Sprints
later. The Check the owner answers tomorrow measures nothing, and the next one after that measures
nothing either.

Measured on a database built for it: successor Card values `[1]`, successor Check values `[]`, old
answered Check values `[1]`.

`_spawn_repeat_successor` copies the Card's four link sets one loop each, then calls
`clone_checks_for_successor`, and `_copy_check` takes `title` and `repeatable` and stops.

## What is already decided

`VL-CHECK-012` states the rule this path breaks:

> a Value is carried by the Check the owner is still answering, and never by a pile of finished ones

Here it ends on a finished one, and the one still being answered has none. So the fix is not a new
rule — it is `VL-CHECK-012` applied to the path that forgot it.

The three paths after this batch:

| When | The Check's Values |
|---|---|
| a repeating Check is answered inside a cycle | move to the fresh copy (`VL-CHECK-012`) |
| a Card closes and does not repeat | stay on the answered one — it is the record |
| a Card closes and repeats | **move to the copy on the new Card** |

## Scenarios

### VL-CHECK-016 — A repeating Card hands the Check's Values to the next cycle

Status: proposed
Sources: `VL-CHECK-012`, the owner's ruling of 2026-08-26

```gherkin
Given a repeating Action with a Check that carries a Value
When the owner finishes the Action and the next cycle's Card appears
Then the Check on the new Card carries that Value
And the answered one no longer does
And so the Value is measured by every cycle, not by one finished Check on an archived Card
```

### CD-LINK-012 — the archived clause

The line said "any one of those three has been archived, or no longer exists". A Value and a Tag
have carried no `archived_at` since Phase 4.d, so for two of the three it named something that
cannot happen. Rewritten to say what each one can be:

```gherkin
When the Check has been archived, or any one of the three no longer exists
```

## What else this batch fixes

None of these needs a scenario: each is a wrong string, a missing test for a rule already approved,
or a name.

1. **A Check has no Delete button on any screen.** `CH-DELETE-014` says "the owner or Safwa asks to
   delete it", and only the `remove` tool implements it. The screen gets Delete with the same
   prompt-and-confirm the Card screen uses. It also gives an archived Check a way out: since 5.e its
   screen offers only `🔄 Current` and Back.
2. **"does not exist or is archived" about a Value or a Tag.** Neither archives. `ReferenceSpec`
   already carries `archivable`, so the sentence follows the spec instead of assuming.
3. **`_apply_stage_change`'s docstring says `finish_action` owns "feedback".** Feedback was removed
   in Phase 5.
4. **Three strings the model reads say "planning state".** The block they head holds Values, Tags,
   the Sprint and the critical Cards — the board. It also carries a `Workspace mode:` line, where
   "planning" means the mode with no Sprint, so one block used the word for two things. The mode
   line does not change. Phase 5.a made this rename in code and left these three because they move
   the `SYSTEM_PROMPT` hash; this batch declares that move.
5. **`ai_comment` is a `diary` tool field in the namespace the views own.** Every view a model is
   told about is `ai_*`, so a small model reading `ai_comment=…` in a tool signature has been given
   a reason to try selecting from it. Renamed to `remark`; it is screen-only and stored nowhere, so
   nothing but the tool schema and the review screen changes. The Diary's prompt can then rejoin the
   check that no prompt names a view the database does not have.

## Checked and already right

**`item_delete_prompt` and `item_delete_confirm` do have a behavioural test.**
`test_manual_tag_and_value_delete_unlinks_cards` drives both handlers through the real callback
path, for a Tag and for a Value, and asserts the rows and their links are gone. The earlier note
that they were untested was wrong.

## Not in this batch

**`render_card` is 250 lines.** Splitting its button rows from its reading is the obvious next cut,
and it belongs to the batch that moves the Telegram adapters into their features.

**Automatic archiving writes no `card_event`, and should not.** The log records what the owner did
on a screen and what an approved proposal did; the sweep that archives what has waited two Sprints
is neither, `archived_at` on the row already records it, and a row per archived Card would grow the
log with the board. After 5.f there is also no actor left for it to claim. The gap was never the
missing row — it was a reader assuming `archive` covered both halves, and `ai_card_events`'s own
documentation now says it does not.

## Gates

- `prompt_prefix.json` moves on `SYSTEM_PROMPT` (the board-state wording), and on `tool:diary` and
  `agent:diary` (the renamed field, which its prompt names twice). On nothing else.
- `schema.json` byte-identical: `remark` is not stored, and no model changes.
- `uv run pytest -q` green, `uv run ruff check .` clean.
- `scripts/architecture_metrics.py`: no new cycle, no new Rule G or Rule H violation.
