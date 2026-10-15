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

## The method, said once

What a good Value, Goal, Action, Check and Value link are is written once, by the feature
that owns each, as one `Method` on its `api.py`: Values and Value links in
[values/api.py](../src/safwa/features/values/api.py), Goals and Actions in
[cards/api.py](../src/safwa/features/cards/api.py), Checks in
[checks/api.py](../src/safwa/features/checks/api.py). Each holds what the item is, the criteria a
check reads it against, how to write one, and examples. The feature's check on a new item reads
its criteria and examples, the Advisor and every subagent that writes items read what each is
and how to write one, and Compass reads the whole of one when asked: the onboarding subagent
calls `get_more_about` for it.

The tree reads in two directions. Down it, a child moves its parent's result forward: that is
how. Up to a Value, an item makes more of the Value in the owner's life: that is why. What a
result needs, such as punctuality to earn money, belongs under it as a Subgoal, a practice or a
Check, never above it as its Value.

Compass reads the owner's whole tree, which the checks on new items each see one item of. The
Advisor routes to it when the owner asks whether their own Values, Goals, Actions, Checks or
links are set right, which Value an item serves or where an item belongs, and when a request
starts with "Compass.". It reads Cards, Checks, Values and Tags and changes nothing. What may be
set better is one list in [compass/graph.py](../src/safwa/features/compass/graph.py): a root Goal
that serves no Value, a Goal carrying `GOAL_VALUES_MANY = 4` Values or more and a Value no open
Goal, Action or Check carries, which SQL finds; a Value that is really a Goal, a practice or a
rule, a reversed link and two Values with one root, which a model finds. An Action carrying the
Value of a Goal above it is fine. One answer names at most `COMPASS_FINDINGS_MAX = 3` findings, each with a
question. Every `COMPASS_REVIEW_DAYS = 9` days after the workspace's first day, in the Morning,
the hook "Compass review" (`compass.graph_review`) writes the tree out for one mini session and
hands what it finds to Compass; a clear answer still keeps what SQL found, and nothing found asks
nothing.

Compass also draws the tree as one picture, the Constellation
([compass/constellation.py](../src/safwa/features/compass/constellation.py)): the Values as stars
across the top of a night sky, each Goal as high as its `tree_level`, the Actions at the bottom,
lines from each parent to its child and from each Value to what carries it, and what SQL finds
ringed in red. A Card under several Goals is drawn once, with a line from each. It goes to the chat as a file, at full size, so zooming loses nothing. Compass sends
it when asked, for the whole tree, one Value or one Goal's branch; the Dashboard's
"🌌 Constellation" sends the whole tree.

## The Card tree, and what is derived

Advisor and workspace mutator formulate Goals and Subgoals as specific, verifiable results,
attainable with the owner's resources and meaningful to them (SMAR; a Deadline is optional).
The title names the result; the Note states its observable completion criterion. A Subgoal's
result contributes to its parent's result. Directions belong to Values and ongoing practices
to repeating Actions. Unclear results or completion criteria need clarification before a new
or reframed Goal is proposed; targets, resources and deadlines are never invented. Advice
works backward from results to Actions and assesses progress toward results, not Action counts.
These are agent instructions, not restrictions on manual Card editing or ordinary updates.

After a Goal or Subgoal is created, by hand or by a saved proposal, a mini session reads its
title, Note, Deadline and the titles of its parent Goals and answers clear, or unclear with what is unclear and
one advice: whether it names one concrete result rather than a direction, an ongoing practice
or two results, takes more than one day, and has an observable Done. Attainable and
meaningful are the owner's to know, so the check leaves them to the conversation. It also
asks whether a Subgoal moves each parent's result forward. A clear
Goal says nothing; an unclear one is kept with its advice by the hook "Goal clarification"
(`cards.goal_clarification`), rechecked before keeping, and one request asks about every
Goal still open: the Advisor says it looked at the new Goals again and they could be
improved, follows each advice, and writes the criterion in Note or proposes what the Goal
should be instead. A Goal carrying the Inbox Tag is a
capture and is not checked. Only creation starts the check: the Note written from the owner's
answer is an update, so the answer never asks again. Nothing changes before the owner answers.

After an Action is created, by hand or by a saved proposal, the hook "Action review"
(`cards.action_review`) checks it the same way: a mini session reads its title, Note,
Schedule, the titles of its Goals, Categories and Energy types and the owner's last five
messages with their answers, and, with Effort Points on, its Effort Points and up to ten
similar Actions with an estimate from the search index, finished ones first. It answers
unclear only for an Action that takes more than one day, a direction rather than one step, a
state to observe rather than work, one that does not move its Goals forward, a label that
clearly does not fit, a clearly missing main one, or Effort Points clearly apart from the
similar Actions or not set. What the owner named in those
messages is theirs and is never advised on. The Advisor says it looked at the new Actions
again and they could be improved, names each change with its reason, and asks; an Action
carrying the Inbox Tag is not checked, and only creation starts the check.

- The two Card kinds are Goal and Action. A Card may stand under one Goal or several, its
  parents, kept in `card_parents`. A Goal with no parent is root-level; one with a parent is
  displayed as a Subgoal, without changing its kind. An Action may be root-level or under any
  Goals and has no children. Cards stand at most 7 levels deep (`CARD_TREE_DEPTH_MAX`),
  counting the root as 1, a Card's level by its longest path up, and including Actions.
  Creating a Card or giving it a parent checks that limit for everything under it along every
  path, and refuses self-parenting or a parent anywhere inside its branch. **Live stage**, effort, time spent, categories, energy and **Blocked** belong to an
  Action alone. The model writes a Goal or a Subgoal with the `goal` tool, which has none of
  them, and an Action with the `action` tool, whose call on a Goal or a Subgoal is refused before
  review; the domain strips them from a Goal. A Card's parents are set by proposal only, one
  per proposal: a new Card is proposed under one Goal at most, `link` gives it one more,
  `unlink` takes one away, and an edit of its fields changes none. `ai_cards.parent_ids` shows
  them all. No screen offers the control or a separate Subgoal kind.
- A Goal's marker counts the deepest Goal chain starting with itself: `🎯` for one
  level, `🎯²` for two, `🎯³` for three, and so on. Actions do not count. Screens,
  lists, citations and proposal previews read the subtree without storing a depth, and
  Safwa reads the same index as `tree_level` in `ai_cards`, 0 on an Action.
- **Schedule** is plain-language timing on an Action or a Check, and the
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
- A Goal and a Subgoal take their stage and archive from their **direct children**, so a child
  that never started still counts: a Subgoal with nothing in it is in Backlog and holds its
  Goals there. Their effort and time are the sums over the **distinct Actions** anywhere under
  them, so an Action reached along two paths counts once.
- `propagate_ancestors` is the one walk that writes it, into the plain `effective_stage`,
  `effort_points`, `tracked_mins` and `archived_at` columns, so Safwa reads one column that means
  the same thing on every row. It visits every Goal above along every path, each after the
  Goals below it. Every path that changes an Action ends there.
- An open parent with no live child shows Backlog. Done on a Goal or a Subgoal is explicit and
  the owner's alone: it may close with open Cards under it, they stay open, and nothing under
  it reopens it. Completing a Card never completes another one. Finishing the last Action
  queues an Advisor question about closing each open parent or adding an Action
  (`cards.parent_completion`). Closing a Goal over open Cards that serve no other open Goal
  queues one about keeping it closed, reopening it or closing them (`cards.open_under_closed`),
  and a Card created or reopened under closed Goals only queues one about reopening them
  (`cards.closed_parents`). Summing `effort_points` or `tracked_mins` over every row counts
  each Action again inside every Goal above it — a real total says `WHERE kind = 'action'`.
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
then Actions in Sprint or Today, including work through a Subgoal.
Remaining ties use the earlier Deadline (none last), creation time and id. Each Goal carries its
Priority, derived stage, `tree_level` and compiled Deadline. The Advisor starts advice and planning from that order.

## Checks

- A Check records a state observation, never planned work: no effort, never in a Sprint, and Pending
  is derived rather than stored. A Check hangs on **one** Card or on none.
- A plain linked Check gates Done, follows the Action cycle and resets when its ordinary
  Action is reopened. A scheduled Check hangs on a Goal or on no Card, never on an Action.
  Answering one opens one successor according to its rule: on no Card, or on its Goal while
  that Goal is open. A Card shows, gates and resets only the newest instance of each series
  on it, so finishing a Goal takes one last answer and ends its series, and reopening the Goal
  asks that last instance again. Passed and Missed both count as observations; absence of an
  answer is not Missed.
- A Check observes a state or a result; it is never work done. When the honest answer is "I did
  it", the item is an Action, a repeating one if it recurs; not doing something, such as no
  sweets today, has nothing to do and stays a Check. After a Check is created, by hand or by a
  saved proposal, the hook "Check review" (`checks.check_review`) reads its title, Note,
  Schedule and the Card it is on, and asks only when it is work done, takes time, is not
  answered yes or no, has no fact to show its answer, or is not about its Card's result. The
  next instance of a repeating Check is not a creation and is not checked.

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
- **Deleting a Card deletes that Card alone.** Each Card directly under it loses that parent
  and keeps its others; one left with none is root-level, and what stands under them stays
  attached. The hook "Cards without a parent" (`cards.orphans`) asks where to put the ones left
  root-level, offering the Goals the deleted Card stood under. A Card's kind never changes;
  `delete_card` and `toggle_card_parent` record parent changes as `link_parent` and
  `unlink_parent` events and repair the derived values of the Goals above.

## Links

An Action can hold a note, captured idea or draft. The built-in Tag **Inbox** marks these
captures without requiring extra detail. It is seeded on startup and cannot be renamed or
deleted; its links and description remain editable. Ordinary field edits leave the Tag
attached; taking it off requires no particular set of filled fields.
Request **Inbox**, supplied when the Tag is first installed, lists tagged Cards oldest
first. It is an ordinary Request and stays deleted or renamed across restarts.

A Card owns four link sets of one shape — its parents, Values, Tags and Checks — and a Check owns
one, its Values. All five are `ReferenceSpec`s: adding another means adding a spec, not a special
case. A Check's
Values are its own statement about what it measures; nothing is derived between them and the Values
of the Cards that Check belongs to.

An Action **serves** the Values it carries and those every Goal above it carries, along every
path: a Value on a Goal says why the work under it is there. `every_finished_action` in the Cards door is where that is
worked out, and Life in weeks counts a Value's Actions by it.

A Value is **in focus** while a Card in Today carries it: an Action in Today, or a Goal or Subgoal
whose derived stage is Today because an Action under it is. Nothing sets it by hand;
`values_in_focus` in the Planning door reads it. A Value's and a Tag's description hold at most
400 characters (`DESCRIPTION_MAX_CHARS`).

Values have unique integer `order` positions from 1 through their count; 1 is highest.
Moving one shifts the intervening Values; a new Value goes last by default, and deletion
closes the gap. The Values list and the Values in Advisor context use this order.

A Value is a direction the owner lives by, never Done: not a result, which is a Goal, nor
one practice, which is a repeating Action, nor a feeling, a means or a rule. It has a name and
a description, and is not created without one. The name is short and may be vivid; the
description says, in two or three vivid sentences built from the owner's words, why it
matters, then what the owner does for it on an ordinary day. How vividly a Value is worded
matters: it makes clear what the owner wants. A name may be about others, such as their
parents' smile, when the description says what the owner does; a means such as money is a
Value when its description says what it gives the owner in itself, not only what it buys.
After a Value is created, by hand or by a saved proposal, a mini session reads its name and
description and answers clear, or unclear with what is unclear and one advice, which may
hold a short exercise such as asking "what for?" again. Whether the Value is right for the
owner, and how vivid its words are, is theirs to know. A clear Value says nothing; an unclear
one is kept with its advice by the hook "Value clarification" (`values.value_clarification`),
rechecked before keeping,
and one request asks about every Value still there: the Advisor says it looked at the new
Values again and they could be improved, and follows each advice. Only creation
starts the check: the description written from the owner's answer is an update. A Value
that is a result may be proposed as a Goal, and one that is a practice as a repeating Action.
Nothing changes before the owner answers.

A Value link runs from the Goal, Action or Check to the Value it serves: achieving the Goal,
doing the Action or the Check holding makes more of the Value in the owner's life, as the
Value's description says. It never runs merely from a needed Value to the work. Earning $4k
serves Freedom through more choices; it serves Health if the money maintains health, but
needing health to earn it is the reverse direction. Needing Perseverance to earn it does not
make earning serve Perseverance, and earning $20k does not serve Punctuality the owner keeps
for their self-respect, while a Check "On time?" does. The owner's own
clear supporting explanation can establish a different connection. If supporting and reversed
meanings are both plausible and the supplied text does not resolve them, the link is unclear.
Unclear reasons belong in Note on both Cards and Checks. A Check keeps its Note when copied.
A Goal's Note holds its completion criterion and its reasons at once, and an update replaces a
Note whole, so the Card and Check tools tell the model to write what it holds, then the new words.

After a new direct link is saved, the hook "Value link clarification"
(`values.link_clarification`) runs a mini session that checks that pair using its title, Note
and the Value's description, with one repair of a malformed answer and no more. An obvious
supporting link says nothing; an unclear or reversed link is kept by the same hook, and one
request asks to clarify Note for every pair still linked. The check rechecks the pair before
keeping it, and the request waits behind the Cue gate like any hook's and tells the Advisor to
read current data before asking. The Advisor may suggest a possible supporting explanation and ask whether it fits the owner's
meaning; it never treats that suggestion as the owner's reason.
Nothing changes before the owner answers. The hook edits or removes no link.

A Note can outlive the link it explains. Taking a Value off a Card or a Check, or deleting the
Value, records one `value.unlinked` fact per change with the Value's name and description and
every item it left, so a deletion off twenty items is one request rather than one per item.
The hook "Value unlink cleanup" (`values.unlink_cleanup`) runs one mini session per item that
has a Note, is live and does not carry the Value again, asking whether the Note still mentions
it; it rechecks the Note before keeping the item. The request tells the Advisor to read the
current Notes and route to workspace_mutator to propose each one without that part. The
proposal's Save or Discard is the owner's answer; the hook changes no Note itself.

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
