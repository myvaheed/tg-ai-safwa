# The domain, and the words for it

What a Card, a Check and a Sprint are, and the invariants every write path has to hold. The rule
itself is `tests/brd/`; this is the shape those scenarios add up to, in one place.

## The workspace and Planning are not the same word

**One package per `.feature` file**, so a rule and the code that keeps it are found together.

- **The workspace** is what the owner keeps: Cards, Checks, Values, Tags, Requests and Reminders —
  the set, not one entity. `workspace_mutator` is the subagent that proposes every change to it,
  and [features/workspace_mutator](../src/safwa/features/workspace_mutator) is that subagent and
  nothing else: the roster lets it declare mutation tools the features that own those entities
  publish.
- **Planning** is the workspace mode without a running Sprint (`WorkspaceMode.PLANNING`), the Sprint
  itself, and the screen where the next one is planned.
  [features/planning](../src/safwa/features/planning) is exactly that and nothing else.

A Card is not "planning data". Say Card, Check, Value, Tag, Sprint — or say the workspace.

`AgentSpec.workspace_state` is that set's current state, sent to a subagent that asked for it, and the
model reads it under that name. The `Workspace mode:` line inside it is the other word: there
`planning` is the mode with no Sprint.

## The Card tree, and what is derived

- A Goal is created root-level, and a Goal placed under a Goal becomes a Subgoal; a Subgoal is
  always under a Goal; an Action may be root or under Goal/Subgoal and has no children. **Live stage**, effort, time spent, categories, energy and **Blocked** belong to an
  Action alone, and are stripped for Goal/Subgoal at both the AI and the domain boundary. A
  Card's parent is set by proposal only; no screen offers the control, which is why no screen
  offers Subgoal as a kind either.
- **Schedule** is plain-language timing on an Action or independent Check, and the
  **Deadline** of a Goal or Subgoal: one date, never repeating, never gating Done and never
  planned. Its compiled revisions live in `schedules`; instances retain their revision and
  assigned period. A typed Schedule is compiled before it is saved; a proposed one after the
  commit. An Action repeats at most `ACTION_DAILY_EXECUTIONS_MAX` times a day. Deterministic
  code handles quotas, progress and successor placement. See [SCHEDULES.md](SCHEDULES.md).
- Planned Actions count the executions left from today through the window's last day, a
  partly covered week by its share of the quota, and an Action on the plan at least once;
  planned EP multiply that count by the unit estimate. `SprintCommitment.planned_count`
  carries the remaining reservation across generated copies; `TodayDay.planned_count`
  snapshots the local day's morning plan. Unknown counts make totals lower bounds. Actual
  Done and time count once.
- A Goal and a Subgoal show what their **direct children** add up to. Each child already carries its
  own derived values, so the recursion reaches the Actions, and a child that never started still
  counts: a Subgoal with nothing in it is in Backlog and holds its Goal there.
- `propagate_ancestors` is the one walk that writes it, into the plain `effective_stage`, `blocked`,
  `effort_points`, `tracked_mins` and `archived_at` columns, so Safwa reads one column that means
  the same thing on every row. Every path that changes an Action ends there.
- An open parent with no live child shows Backlog. Done requires explicit completion after
  all its Actions are finished; completing a parent never completes another Card. Finishing
  the last Action queues an Advisor question about closing each open parent or adding an Action.
  A new or reopened Action reopens closed ancestors and resets their Checks. A parent has no
  `blocked_description` of its own. Summing `effort_points` or `tracked_mins` over every row counts
  each Action again inside every ancestor — a real total says `WHERE kind = 'action'`.
- `ai_cards` exposes blocking through `blocked_description` alone: NULL means unblocked;
  a blocked Action carries its reason, and a blocked Goal or Subgoal carries an empty string.
  Filter blocked Cards with `blocked_description IS NOT NULL`.
- `manual_stage` is what the user set on an Action, or explicit Done on a parent; `effective_stage` is what
  dashboards and queries read.
- Effort is optional for Actions and restricted to `EFFORT_POINTS` when supplied; the `Literal` in
  [cards/agent.py](../src/safwa/features/cards/agent.py) mirrors it — change both together.
  A rung says what the Action costs the owner rather than how long it takes, and
  `EFFORT_RUNGS` is that wording, read by the effort selector and by the tool's field
  description alike. The column is a float because 0.5 is a rung; recovery does not add
  up, so a Sprint total is a load signal of the right order and never a percentage base.
- **Effort Points** (`UserProfile.effort_tracking`) are off by default. The Profile switch
  controls the estimate selectors, Card/list/citation displays, Sprint capacity, Today
  overload and the Effort Points reminder after Done without an estimate. Off, Sprint and
  retro screens and AI context use Action counts; AI read views hide stored effort with NULL,
  and a proposal carries no estimate. Both effort hooks follow the switch the way the Time
  tracking reminder follows Time tracking. Switching off
  preserves estimates and configured capacity and leaves Time tracking alone. On, the model
  proposes an estimate with each new Action; saving without one stays allowed.
- A Sprint commitment freezes the estimate as it joins, including NULL for an unestimated
  Action. Count totals include every Action; effort totals sum known estimates and name
  missing ones. Retro keeps the missing count. Incomplete records supply no effort
  completion percentages or EP/hour rates; effort aggregates omit incomplete Sprints and
  state how many records supplied each number. The current Profile switch governs both
  past retro displays and the numbers handed to AI analysis.
- **Time spent** (`tracked_mins`) is the minutes the owner says an Action took, 1 to
  `TRACKED_MINS_MAX`, shown as hours and minutes by `minutes_label`. It is optional and never
  written on creation. The column and the `card` tool field always exist, because the prompt
  prefix cannot follow a Profile setting; **Time tracking** in the Profile governs only the
  "⌛ Time spent" button, whether a closing Sprint keeps its time for the retro, and the
  question after Done. A finished repeat takes its own time and effort estimate; other
  proposed edits belong to its open successor.

## Checks

- A Check records a state observation, never planned work: no effort, never in a Sprint, and Pending
  is derived rather than stored. A Check hangs on **one** Card or on none.
- A scheduled Check is independent and cannot be attached to a Card. A plain linked Check
  gates Done, follows the Action cycle and resets when its ordinary Action is reopened.
  Answering an independent scheduled Check opens one successor according to its rule.
  Passed and Missed both count as observations; absence of an answer is not Missed.

## Deleted, and archived

- **Everything is deleted; only a Card and a Check are also archived**, two Sprints after they
  closed (`ARCHIVE_AFTER_SPRINTS`). Archived is a matter of sight: it still counts everywhere it
  counted. A Value, a Tag and a Saved Request carry no `archived_at` at all.
  Deleting a Value or Tag removes its links and frees its name; creating that name later creates
  a new item. Their names are unique under Unicode case folding, for both create and rename.
  `DatabaseFile.connect` registers `UNICODE_NOCASE`, used by their name columns' unique indexes
  and by proposal reference lookups.
- **Only an Action is stamped as archived; a closed Goal and a closed Subgoal derive their archive.**
  A branch leaves sight when its last Card does and comes back the moment one is reopened, so a
  parent is never stamped, never restored and never carries an `archive` event of its own.
- **A list by stage leaves an archived item out; every other list shows it, marked `[📦]`.** It
  opens, it reads as archived, and no proposal changes it — only the owner, by reopening or deleting
  it. `foundation.marks.title_marks` is the one place both marks are written, and `ai_cards` and
  `ai_checks` render the same wording in SQL.
- **A repeating Action also carries `[🔄✓]` while its series has a completion today**, on the
  finished instance and the open successor alike, so finishing one never leaves the next
  looking untouched. It is the one mark SQL does not mirror — today is the owner's calendar
  day in the workspace timezone, which SQLite cannot work out — so `cards.telegram`
  writes it and the model is told nothing of it.
- **Deleting a Card is the whole branch or that Card alone**, and the owner chooses on the
  confirmation screen; Safwa only ever proposes the branch. Deleting one Card alone leaves its
  children standing where the tree allows, which makes a Subgoal a Goal. That and a Goal placed
  under a Goal are the two places a kind changes without being asked to; `delete_one_card` and
  `set_card_parent` each write an `edit_kind` event saying so.

## Links

A Card owns three link sets of one shape — Values, Tags, Checks — and a Check owns one, its Values.
All four are `ReferenceSpec`s: adding another means adding a spec, not a special case. A Check's
Values are its own statement about what it measures; nothing is derived between them and the Values
of the Cards that Check belongs to.

## Versions, and what invalidates what

Entities carry a `version`, and `workspace.revision` is what a pending proposal is checked against
before it applies. `StaleStateError` is the expected failure. Only `dialogue_revision` invalidates
an in-flight answer, because the answer's own autoapproved change moves `workspace.revision`.

## Safwa speaks first only from a Cue

A **Cue** is a Reminder that came due or a hook's finding — a Sprint that ended is one of those.
The Reminders' tick writes its words into `cues` in its own transaction; a hook's row carries only
its name and what it refers to, and its feature words it just before it is said. `CueRuntime` owns
the gate, the lease and the one turn that says everything waiting, and deletes the rows only once
the owner has the words. That row is the single record of what Safwa still owes, so the
Reminders' tick does schedule arithmetic and nothing else. The mechanism is in [AGENT_ARCH.md](AGENT_ARCH.md).
