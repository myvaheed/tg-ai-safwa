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
  always under a Goal; an Action may be root or under Goal/Subgoal and has no children. **Stage**, effort, repeat, categories, energy and **Blocked** belong to an
  Action alone, and are stripped for Goal/Subgoal at both the AI and the domain boundary. A
  Card's parent is set by proposal only; no screen offers the control, which is why no screen
  offers Subgoal as a kind either.
- **Hard Time** is when a Card must happen: a Reminder's schedule carried by the Card as the
  same payload (`hard_time`), its next occurrence (`hard_time_at`, what a list sorts by), and
  what fixes the time (`hard_time_description`). `cards/hard_time.py` writes all three, through
  the Reminders door. A repeating Action's next instance takes the next occurrence of a
  repeating Hard Time; one that was a single moment does not carry over.
- A Goal and a Subgoal show what their **direct children** add up to. Each child already carries its
  own derived values, so the recursion reaches the Actions, and a child that never started still
  counts: a Subgoal with nothing in it is in Backlog and holds its Goal there.
- `propagate_ancestors` is the one walk that writes it, into the plain `effective_stage`, `blocked`,
  `effort_points` and `archived_at` columns, so Safwa reads one column that means the same thing on
  every row. Every path that changes an Action ends there.
- A parent with nothing under it shows Backlog and never Done, and it has no
  `blocked_description` of its own. Summing `effort_points` over every row counts each Action again
  inside every ancestor — a real total says `WHERE kind = 'action'`.
- `manual_stage` is what the user set, and it is an Action's alone; `effective_stage` is what
  dashboards and queries read.
- Effort is restricted to `EFFORT_POINTS` and required for Actions; the `Literal` in
  [cards/agent.py](../src/safwa/features/cards/agent.py) mirrors it — change both together.
  A rung says what the Action costs the owner rather than how long it takes, and
  `EFFORT_RUNGS` is that wording, read by the effort selector and by the tool's field
  description alike. The column is a float because 0.5 is a rung; recovery does not add
  up, so a Sprint total is a load signal of the right order and never a percentage base.

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
- **Only an Action is archived; a Goal and a Subgoal are derived, like everything else they show.**
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

A **Cue** is a Reminder that came due or a Sprint that ended, and nothing else. The producer writes
the finished request into `cues` in its own transaction; `CueRuntime` owns the gate, the lease and
the one turn, and deletes the row only once the owner has the words. That row is the single record
of what Safwa still owes, so the Reminder poll does schedule arithmetic and nothing else, and one
thing waits to be said at a time. The mechanism is in [AGENT_ARCH.md](AGENT_ARCH.md).
