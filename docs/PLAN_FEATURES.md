# Which entry to build first

[FUTURE_FEATURE.md](FUTURE_FEATURE.md) records candidates without ordering them. This file sets
their implementation order, costs and dependencies.

**This approves nothing.** An entry still leaves FUTURE_FEATURE.md only by becoming a scenario
package under [tests/brd/](../tests/brd/README.md) or by being dropped, and the owner still decides
which. Usage scenarios here describe future behavior; they do not claim implemented BRD coverage.

## How the order was chosen

Four rules, applied in this order.

1. **The pre-release window closes once.** There are no migrations: a schema change costs a rebuild
   of a database the owner already treats as disposable, and costs a migration forever after v1.
   Every entry that changes a column or a stored word is worth more now than it will ever be again.
2. **A prerequisite before whatever waits on it.** The hook switches in the Profile come before
   the first initiative; the Committed adapter before a hook on a saved transition; the Tick
   adapter before a hook that checks state on a schedule. Service operations, tool availability
   and Advisor instructions need none of them. Proposal fulfillment validation belongs to its
   own architecture.
3. **Agreed before undecided.** An entry marked *Agreed* needs a scenario package and a batch. One
   marked *Noticed* or *Discussed* needs an owner decision first — that is a question, not an
   implementation, and several questions cost one conversation rather than several.
4. **Then cost.** Among entries of equal standing, the pure-screen ones go first: one adapter, no
   schema, no prompt, and they improve every day of ordinary use.

## The complexity scale

| | What it means |
|---|---|
| **S** | One or two files inside one feature. No schema, no snapshot, no new mechanism. |
| **M** | One feature end to end — model, use cases, adapter — or a mechanical change across many files. Usually moves the prompt-prefix snapshot. |
| **L** | Crosses features, or adds a mechanism, or changes a column and everything that reads it. Moves the prompt snapshot, and often the schema one. |
| **XL** | A new mechanism with reliability questions still open. The design is a batch of its own before any code exists. |

A schema entry carries one extra cost the scale does not show: the schema snapshot is its own
declared batch, and the database is rebuilt. Group the schema entries so that happens a few times,
not once per entry.

## Wave 0 — the questions that block, asked together

None of these is code. Each decides what a later batch builds, so asking them in one conversation
is cheaper than discovering them one wave at a time.

Answered 2026-09-08, and written into the entries themselves:

- **The middle kind became Subgoal.** Wave 1 also introduced raw capture as Idea, and the
  owner's 2026-09-13 decision retired that fourth kind again, in the follow-up below;
  Subgoal keeps its meaning.
- **The effort rungs are approved as written**, 0.5 included.
- **The daily summary and the Diary nudge are two switches, and neither replaces the other.** Both
  on, one message carries both; one on, only that one appears.
- **Hooks need an explicit, inspectable registration contract.** A literal model tool call is
  not required for every reaction. [HOOK_ARCH.md](HOOK_ARCH.md) proposes typed event inputs,
  a central connection list, runtime reactions and staged implementation.
- **Proposal fulfillment validation is not a hook.** Its separate architecture is proposed in
  [PROPOSAL_VALIDATION.md](PROPOSAL_VALIDATION.md); it is not gated by hook registration.

## Wave 1 — the schema and vocabulary window, shipped

Shipped 2026-09-09, in five batches: Cancelled removed, the middle kind renamed to Subgoal
and required under a Goal, Idea reborn as raw capture, the effort rungs with 0.5, and the
Sprint number as `yy.MM-xx`. Their entries are out of FUTURE_FEATURE.md and their rules are
in [cards.feature](../tests/brd/cards.feature), [planning.feature](../tests/brd/planning.feature)
and [profile.feature](../tests/brd/profile.feature). The database is rebuilt for them once,
not five times.

### Follow-up — retire raw-capture Ideas, shipped

Owner decision 2026-09-13, shipped the same day: the fourth Card kind, its list and Expand,
its creation choice, its view and every prompt line naming it, and the default «Все идеи»
Request are gone; Goal, Subgoal and Action keep their rules. No column changed, but a
pre-release row with `kind = 'idea'` is orphaned, so the database is rebuilt. The same day
a subagent started saying what it will change before its calls become proposals
([PR-PLAN-028](../tests/brd/tg_agent_shell/proposals.feature)).

### Follow-up — a kind changes in two places and by no proposal, shipped

Owner decision 2026-09-13, shipped the same day: no proposal changes a Card's kind, and the
entry that would have allowed conversion is dropped. A kind changes in exactly two places,
each written as an `edit_kind` event: a Subgoal whose Goal is deleted alone becomes a Goal
(Wave 2), and a Goal a proposal places under a Goal becomes a Subgoal
([CD-TREE-002](../tests/brd/cards.feature)). No column changed.

### Follow-up — the daily summary, shipped

Owner decision 2026-09-13, shipped the same day: the summary comes at a Profile time of
its own — 20:00 out of the box ([PS-SUMMARY-014](../tests/brd/profile.feature)). It was a
second Reminder Safwa set for itself; since 2026-09-17 it and the Diary nudge are daily hooks
that read their Profile time, switched in the Profile like the other reactions, and no
Reminder stands behind either. `summary_time` on the Profile is a column of its own.

### Follow-up — Hard Time is a Reminder's schedule, shipped

Shipped 2026-09-14: a Hard Time is when a Card must happen, resolved the way a Reminder's
timing is — typed by hand as `Mon Wed 09:00`, or by the model in plain words through the
same setup session — and stored as the same schedule payload, with its next occurrence and
what fixes the time ([CD-HARDTIME-033](../tests/brd/cards.feature)). `reminders/api.py`
is the door Cards reads it through. Three columns replaced one, so the database is rebuilt.

### Follow-up — a review nobody answers is closed after 30 minutes, shipped

Shipped 2026-09-14, at 30 minutes rather than the hour the entry named: a review screen
that stands `PROPOSAL_REVIEW_MINUTES` without an answer is closed by the Cue poll — its
pending proposals recorded as expired, what was saved kept, the screen frozen into an
account that the system closed the request, and the whole session chain ended so the
owner's next words start afresh ([PR-EXPIRE-029](../tests/brd/tg_agent_shell/proposals.feature)).
The Reminders that waited behind it are said on that same tick. The entry's second
mechanism, an expiry for manual editors, is dropped: a manual screen holds no lease and
shuts no gate, so there is nothing for one to release. No column changed.

## Wave 2 — screens that cost almost nothing, shipped

Shipped 2026-09-09, in five batches: deleting a Card is now the branch or that Card alone
(a Wave 1 debt: a Subgoal that loses its Goal becomes one), a Goal says when it carries no
Value and every Value on a Card is a button, the Card opens compact with full editing one
button away, a repeating Action carries `[🔄✓]` when its series was already done today, and
a new workspace starts with «Все цели» beside a screen explaining Requests.
Their rules are in [cards.feature](../tests/brd/cards.feature),
[values.feature](../tests/brd/values.feature) and
[saved_requests.feature](../tests/brd/saved_requests.feature). No column changed.

## Wave 3 — the mechanisms other entries wait on, shipped

Started 2026-09-15 with hook stages 1 and 2: one checked connection list, and the existing
Summary and Heavy analyzer offer moved onto it. Their former automatic paths are removed. The
shell works with an empty hook list. The same day the switches reached the Profile
([PS-HOOKS-015](../tests/brd/profile.feature)) and the first initiative shipped: an Action
becoming blocked is recorded beside its transaction, handed to the hooks after the commit, and
kept as that hook's one pending `Cue` — worded from what is still blocked when it is next in
line ([AG-HOOK-037](../tests/brd/tg_agent_shell/agents.feature), AG-HOOK-038,
[CD-BLOCKED-034](../tests/brd/cards.feature)). One Profile column, two `Cue` columns and one
prompt line changed; the Reminder's Sprint column went, its warnings being found by their
`system_key` like the Profile's own. The database is rebuilt once for all of it.

A hook is switched on or off as a whole, by the owner in the Profile. An initiative is one
request to the Advisor, delivered through the existing `Cue` queue; one hook has one pending
initiative at a time, rereads what it refers to just before delivery, and the owner's reply is
an ordinary next turn the hook never reads. The next day the Tick adapter shipped with its
first consumer, Goals and Subgoals without Actions ([AG-HOOK-039](../tests/brd/tg_agent_shell/agents.feature),
[CD-EMPTY-035](../tests/brd/cards.feature)): one shell poll, the last look kept in process
memory; nothing stored, so no rebuild. The queue was corrected first — words are made only once
the chat is free, a failed wording keeps the request, delivery settles only what was read, and
switching a hook on drops a late write. No table beyond the `Cue` row is added.

Closed the same day: the daily hour became the Profile's Morning time, read at every look
([PS-MORNING-016](../tests/brd/profile.feature)); hooks 11, 7 and 13 shipped on the two
adapters as they stood ([CH-MISSED-017](../tests/brd/checks.feature),
[CD-TODAY-036](../tests/brd/cards.feature), [PL-HARDTIME-021](../tests/brd/planning.feature)) —
each a definition, a reading at delivery and a line in `HOOKS`, hook 13 with two subscriptions
on one definition; and the retro screen shows the Sprint's own numbers
([RT-STATS-003](../tests/brd/retro.feature)). One Profile column, so the database is rebuilt once
more.

| Entry | | What it unlocks, and what to watch |
|---|---|---|
| **POTENTIAL HOOKS — the shape every hook has** | M per batch | Stages 1–2 and batches 1–3 implemented, in [HOOK_ARCH.md](HOOK_ARCH.md). Each batch is one new port or event/reaction pair, one real consumer and one line in `HOOKS`. |
| **Hook switches in the Profile — batch 1, shipped** | M | Every hook whose effect reaches the agent is listed by title — the Heavy analyzer offer, the blocker and the morning checks; the automatic Summary runs work of its own and is always on; switching one off stops its condition and drops its pending initiative. |
| **The retrospective — the statistics half, shipped** | M | Effort taken, finished and its share; initial, added and taken out; Actions finished, remaining and blocked; Passed and Missed per Check series tied to a Value, over the Sprint's days. Read off the record, no model involved. The AI analysis half is Wave 5. |

## Wave 4 — the hooks, in dependency order

Hooks on a committed transition need only the Committed adapter, shipped in batch 2. Hooks that
poll state on a schedule need the Tick adapter, shipped in batch 3. Instruction 15 needs
neither; 1 is withdrawn.
Explicit retrospective workflows do not depend on hook registration.
The classification audit and implementation stages are in [HOOK_ARCH.md](HOOK_ARCH.md).

Shipped 2026-09-18, batch 6: entry 4 as two hooks on the adapters as they stood, the energy
balance of a starting Sprint against the Backlog
([PL-ENERGY-022](../tests/brd/planning.feature)) and the day's rest against the Sprint's
([CD-REST-037](../tests/brd/cards.feature)) — two definitions, two readings at delivery, two
lines in `HOOKS`, no column. The same day, batch 7: hook 6 as a `Run` on the morning tick that
writes each open Action in Today down for that day, `today_days`, and hands a fact on, and an
`Advise` on that fact that asks on every third morning in a row
([CD-STALE-038](../tests/brd/cards.feature)). One new table, created at startup. Batch 8, the
same day: hook 10 — the registry's first `Run` on a commit, done once the commit's facts are
handed on and outside their order; the classifier marks `key_action` on the commitment, a
warning hook reads the marks, and Today is ordered by them
([PL-KEY-023](../tests/brd/planning.feature), PL-KEY-024, PL-KEY-025). One column on an
existing table, so the database is rebuilt.

| Entry | | Depends on |
|---|---|---|
| **15 — Advisor instruction, not a hook** | S | The AI half of the retrospective, where the takeaway it carries is agreed. |

## Wave 5 — deliberately later

| Entry | | Why it waits |
|---|---|---|
| **The retrospective — the AI analysis half** | XL | Four questions open in its own entry, and it is also where memory upkeep is removed from `memory/upkeep.py`. Two batches, not one. |
| **Onboarding covers the first start and a return after an absence** | L | Undecided in both halves, and it puts changing state next to the byte-stable prefix. |
| **Proposal fulfillment validation** | XL | A separate part of Proposal architecture; see [PROPOSAL_VALIDATION.md](PROPOSAL_VALIDATION.md). It coordinates request completion, actual outcomes and interruptions. It does not depend on hooks. |
| **8 — Retrieve similar existing entities before creating another** | XL | The general BeforeTool interception, retrieval thresholds and clarification/resume contract remain experimental. |
| **The Advisor cannot read the conversation by date** | L | A recorded limitation. Nothing else waits on it. |

**3** and **12** are already rejected in their entries. They stay written so the reasoning is not
repeated, and they are not scheduled.

## What blocks what

```mermaid
flowchart LR
  h10["Hook 10 — key Actions, shipped"]
  shape["The shape every hook has"] --> profile["Batch 1 — switches in the Profile"]
  profile --> committed["Batch 2 — Committed → Advise, shipped with hooks 2, 11 and 7"]
  profile --> tick["Batch 3 — Tick → Advise, shipped with hooks 5 and 13"]
  committed --> h10
  tick --> h6["Hook 6 — mornings in Today, shipped"]
  committed --> h6
```

## Where to look hardest

**The next initiative reuses the path the first five share.** Every hook so far ships as one
pending request per hook in the `Cue` queue, reread just before delivery, and nothing more —
the timed ones add one poll that hands the hour on and nothing it stores. The one table a hook
has needed, `today_days`, is a record of the workspace's mornings that any reader may use, not
a memory of what was asked. A hook that needs a second delivery path or a table of what was
asked means the shared part was fitted to the ones before it.

**The pre-release window.** Declare a schema batch only if an implementation changes stored
structure.

**What reaches the cacheable prefix.** Onboarding's changing guidance belongs outside
`messages[0]`.

**The rungs are the unit now.** Hook 7's 15 EP, the Sprint's committed and capacity figures, the
retro's numbers and the Advisor's judgement of a day's load all count in a scale that says what
work costs the owner.
Recovery does not add up, so a total is a load signal of the right order and never something to
take a percentage of.
