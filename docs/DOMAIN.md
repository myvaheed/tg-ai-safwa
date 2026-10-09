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

Advisor and workspace mutator formulate Goals and Subgoals as specific, verifiable results,
attainable with the owner's resources and meaningful to them (SMAR; a Deadline is optional).
The title names the result; the Note states its observable completion criterion. A Subgoal's
result contributes to its parent's result. Directions belong to Values and ongoing practices
to repeating Actions. Unclear results or completion criteria need clarification before a new
or reframed Goal is proposed; targets, resources and deadlines are never invented. Advice
works backward from results to Actions and assesses progress toward results, not Action counts.
These are agent instructions, not restrictions on manual Card editing or ordinary updates.

- The two Card kinds are Goal and Action. A Goal may be root-level or under any Goal;
  a Goal with a parent is displayed as a Subgoal, without changing its kind. An Action may
  be root-level or under any Goal and has no children. The tree has at most 7 levels
  (`CARD_TREE_DEPTH_MAX`), counting the root as 1 and including Actions. Creating and moving
  a branch checks that limit for its deepest Card and refuses self-parenting or a parent
  anywhere inside the branch. **Live stage**, effort, time spent, categories, energy and **Blocked** belong to an
  Action alone. The model writes a Goal or a Subgoal with the `goal` tool, which has none of
  them, and an Action with the `action` tool, whose call on a Goal or a Subgoal is refused before
  review; the domain strips them from a Goal. A Card's parent is set by proposal only;
  no screen offers the control or a separate Subgoal kind.
- A Goal's marker shows the deepest Subgoal chain inside it: `🎯` without Subgoals,
  `🎯₁` with one level, `🎯₂` with two, and so on. Actions do not count. Screens,
  lists, citations and proposal previews read the subtree without storing a depth.
- **Schedule** is plain-language timing on an Action or independent Check, and the
  **Deadline** of a Goal or Subgoal: one date, never repeating, never gating Done and never
  planned. Its compiled revisions live in `schedules`; instances retain their revision and
  assigned period. A Schedule is compiled before it is saved, typed or proposed, and a day
  without a clock is due by its end. An Action repeats at most `ACTION_DAILY_EXECUTIONS_MAX` times a day. Deterministic
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
- `propagate_ancestors` is the one walk that writes it, into the plain `effective_stage`,
  `effort_points`, `tracked_mins` and `archived_at` columns, so Safwa reads one column that means
  the same thing on every row. Every path that changes an Action ends there.
- An open parent with no live child shows Backlog. Done requires explicit completion after
  all its Actions are finished; completing a parent never completes another Card. Finishing
  the last Action queues an Advisor question about closing each open parent or adding an Action.
  A new or reopened Action reopens closed ancestors and resets their Checks. Summing
  `effort_points` or `tracked_mins` over every row counts each Action again inside every
  ancestor — a real total says `WHERE kind = 'action'`.
- Only an Action is blocked, and it is blocked exactly while its `blocked_description` holds a
  reason; `Card.blocked` reads that. A Goal and a Subgoal are never blocked and derive nothing
  from blocked Actions under them. `ai_cards` shows an empty reason as NULL, so blocked Actions
  are `blocked_description IS NOT NULL`.
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
  written on creation. The column and the `action` tool field always exist, because the prompt
  prefix cannot follow a Profile setting; **Time tracking** in the Profile governs only the
  "⌛ Time spent" button, whether a closing Sprint keeps its time for the retro, and the
  question after Done. A finished repeat takes its own time and effort estimate; other
  proposed edits belong to its open successor.

## The Goals Safwa starts from

Each turn carries up to 10 open root Goals (`CONTEXT_PRIORITY_GOAL_LIMIT`) under **Priority Goals**,
across all Priorities. An overdue compiled Deadline or one within the next 7 local calendar days
(`PRIORITY_GOAL_DEADLINE_DAYS`) comes first. Within urgent and other Goals, the order is Priority,
a directly linked Value in focus, then Actions in Sprint or Today, including work through a Subgoal.
Remaining ties use the earlier Deadline (none last), creation time and id. Each Goal carries its
Priority, derived stage and compiled Deadline. The Advisor starts advice and planning from that order.

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
  Deleting a Value or an ordinary Tag removes its links and frees its name; creating that name later creates
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
- **A finished instance reads `[✅3, 🔄#5]`**: how it ended — ❌ for a Check answered Missed —
  its place in the series and the open one, or `[✅3]` once the series has ended. **The open
  instance of a repeating Action carries `[🔄]` on a screen**, so the one to work with stands
  apart from the finished ones at a glance. That is the one mark SQL does not mirror: the model
  is told the open one by its plain title, so `cards.telegram` writes it and nothing else does.
- **Deleting a Card is the whole branch or that Card alone**, and the owner chooses on the
  confirmation screen; Safwa only ever proposes the branch. Deleting one Card alone gives its
  direct children its parent, or leaves them root-level if it had none. Their descendants stay
  attached. A Card's kind never changes; `delete_one_card` and `set_card_parent` record parent
  changes as `set_parent` events and repair the ancestors' derived values.

## Links

An Action can hold a note, captured idea or draft. The built-in Tag **Inbox** marks these
captures without requiring extra detail. It is seeded on startup and cannot be renamed or
deleted; its links and description remain editable. Ordinary field edits leave the Tag
attached; taking it off requires no particular set of filled fields.
Request **Inbox**, supplied when the Tag is first installed, lists tagged Cards oldest
first. It is an ordinary Request and stays deleted or renamed across restarts.

A Card owns three link sets of one shape — Values, Tags, Checks — and a Check owns one, its Values.
All four are `ReferenceSpec`s: adding another means adding a spec, not a special case. A Check's
Values are its own statement about what it measures; nothing is derived between them and the Values
of the Cards that Check belongs to.

An Action **serves** the Values it carries and those its Goal and Subgoal carry: a Value on a Goal
says why the work under it is there. `every_finished_action` in the Cards door is where that is
worked out, and Life in weeks counts a Value's Actions by it.

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
