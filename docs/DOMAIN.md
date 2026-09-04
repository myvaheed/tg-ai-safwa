# The domain, and the words for it

What a Card, a Check and a Sprint are, and the invariants every write path has to hold. The rule
itself is `tests/brd/*.feature`; this is the shape those scenarios add up to, in one place.

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

- Goal is root-only; Idea may be root or under a Goal; Action may be root or under Goal/Idea and has
  no children. **Stage**, effort, repeat, categories, energy and **Blocked** belong to an Action
  alone, and are stripped for Goal/Idea at both the AI and the domain boundary. A Card's parent is
  set by proposal only; no screen offers the control.
- A Goal and an Idea show what their **direct children** add up to. Each child already carries its
  own derived values, so the recursion reaches the Actions, and a child that never started still
  counts: an Idea with nothing in it is in Backlog and holds its Goal there.
- `propagate_ancestors` is the one walk that writes it, into the plain `effective_stage`, `blocked`,
  `effort_points` and `archived_at` columns, so Safwa reads one column that means the same thing on
  every row. Every path that changes an Action ends there.
- A parent with nothing under it shows Backlog and never Done or Cancelled, and it has no
  `blocked_description` of its own. Summing `effort_points` over every row counts each Action again
  inside every ancestor — a real total says `WHERE kind = 'action'`.
- `manual_stage` is what the user set, and it is an Action's alone; `effective_stage` is what
  dashboards and queries read.
- Effort is restricted to `EFFORT_POINTS` and required for Actions; the `Literal` in
  [ai/contracts.py](../src/tg_agent_shell/ai/contracts.py) mirrors it — change both together.

## Checks

- A Check records a state observation, never planned work: no effort, never in a Sprint, and Pending
  is derived rather than stored. A Check hangs on **one** Card or on none.
- Three rules govern a Check across a Card's life: a Card closes when every Check series on it was
  answered at least once **on this Card**; closing deletes whatever is still Pending; reopening puts
  each plain Check back to Pending and opens one fresh instance of each repeating series. They live
  in [features/checks](../src/safwa/features/checks), and Cards asks for them by name in
  `checks/use_cases.py`.

## Deleted, and archived

- **Everything is deleted; only a Card and a Check are also archived**, two Sprints after they
  closed (`ARCHIVE_AFTER_SPRINTS`). Archived is a matter of sight: it still counts everywhere it
  counted. A Value, a Tag and a Saved Request carry no `archived_at` at all.
- **Only an Action is archived; a Goal and an Idea are derived, like everything else they show.**
  A branch leaves sight when its last Card does and comes back the moment one is reopened, so a
  parent is never stamped, never restored and never carries a `card_events` row of its own.
- **A list by stage leaves an archived item out; every other list shows it, marked `[📦]`.** It
  opens, it reads as archived, and no proposal changes it — only the owner, by reopening or deleting
  it. `foundation.marks.title_marks` is the one place both marks are written, and `ai_cards` and
  `ai_checks` render the same wording in SQL.

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

A **Cue** is a Reminder that came due or a Sprint that ended, and nothing else. The producer writes
the finished request into `cues` in its own transaction; `CueRuntime` owns the gate, the lease and
the one turn, and deletes the row only once the owner has the words. That row is the single record
of what Safwa still owes, so the Reminder poll does schedule arithmetic and nothing else, and one
thing waits to be said at a time. The mechanism is in [AGENT_ARCH.md](AGENT_ARCH.md).
