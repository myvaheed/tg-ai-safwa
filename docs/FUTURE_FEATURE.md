# Ideas kept, and questions already settled

Nothing here is a commitment and nothing here has a scenario. Each entry is a real gap noticed
while something else was being decided, written down so the reasoning does not have to happen
twice. An entry leaves this file by becoming an approved scenario package, or by being dropped.

## The Advisor cannot read the conversation by date

Noticed 2026-08-30, reviewing how the Advisor reads history.

Asked what was said on Tuesday, the Advisor answers from the window it always reads — the newest
messages up to the token budget, stopping at the newest Summary. Only the Diary subagent reaches a
named day, through `read_day`, and a question about the chat is not routed there.

A Summary cannot be the index: it is free text with no dates of its own. Making one Summary per day
was considered and left alone, because it changes when a Summary is written, which is
`CO-SUMMARY-001`.

Ruled: nothing changes. `DI-READ-016` keeps the Diary reading a whole named day; whether the
Advisor gets a way to reach one is designed later.

## The middle Card kind is named for the one thing it cannot be

Noticed 2026-09-05, discussing the shape of the tree rather than any code.

An Idea carries no field of its own: stage, effort, energy, repeat and Blocked are an Action's, and
everything an Idea shows is derived from its children. The word promises something optional; the
mechanism is a container under a Goal. Two changes were agreed, neither has a scenario yet:

- Idea becomes Direction (Направление). A direction is not completed, it is advanced, which is what
  a derived stage already says. Goal keeps its name: a Goal is not a life area, because a Value is
  one and Values cross Goals.
- A Direction may only exist under a Goal. CD-TREE-003 allows a root-level Idea today, and that is
  the version that rots: one word covering both a committed line of work and a loose thought.

The second change leaves raw capture without a home. A root-level Action needs effort from
`EFFORT_POINTS`, so it asks for the size of something not yet decided on. Either the Diary becomes
where a loose thought goes, or a Card gets a stage before Backlog that requires no effort.
Undecided; the Diary adds no entity.

## Effort is offered in points one owner cannot calibrate

Noticed 2026-09-05, discussing what a plain user reads on a screen.

The effort selector renders `f"{points} EP"` — the Fibonacci scale a team calibrates over months of
estimating together. One owner has nobody to calibrate against, so the number is chosen differently
each week, and the sprint figures it feeds — committed, added, capacity — claim more than they know.

Duration was tried as the label and dropped: half an hour of running is not the smallest thing on
the scale. A rung says what the owner will be able to do afterwards, and what recovery it takes
first — one axis, and the only one that reads the same for a physical, a cognitive and an emotional
load. Which of those it is, `EnergyType` already carries.

    0.5   done in passing, the load is barely noticed
    1     done, and the day goes on as it was
    2     a little tired, but able to carry on without a rest
    3     able to carry on only after a break
    5     after a full rest there is enough for one more serious thing
    8     only light work is left for today
    13    nothing is left for anything else today

Under it stands the one instruction that keeps the rungs steady: how much will the whole thing take
in your usual state — today's tiredness decides how many things you take on, not what one of them
costs. On a repeating Action the effort is one occurrence, not the series. The field description the
model reads carries the same lines, or the model estimates duration while the owner estimates cost,
and the two are summed as one scale.

Two things follow from the rungs being written this way. They are approximate weights, because
recovery does not add up — one thing needs a rest and the next needs a change of activity — so a
sprint total is a load signal of the right order and never a measurement to take a percentage of.
And 13 is a ceiling that carries a rule: work that does not fit one day's reserve is not an Action
but a Direction with Actions under it. Calendar time is not the test — a 13 may sit in Today for
three days.

The set keeps 0.5 rather than doubling to stay whole, which would leave every other number too large
to work with. So `effort_points` and `capacity_effort_points` both turn numeric, together with the
`Literal` in [ai/contracts.py](../src/safwa/ai/contracts.py) and the whole-number check in
[profile/use_cases.py](../src/safwa/features/profile/use_cases.py).

## The daily summary is a second system Reminder

Noticed 2026-09-05, after a digest that decides for the owner was declined.

A Reminder is about a thing the owner chose not to forget. A summary is about state nobody chose —
what is on for today, what is overdue, where the Sprint stands — so it cannot be written as a
Reminder the owner sets.

RM-SYSTEM-022 already carries the shape: the Diary nudge is a Reminder Safwa set up, absent from the
Reminder list, switched in Settings. A daily summary is a second one of those, on by default and
turned off in the same place. RM-WRITE-010 is untouched, because deleting stays the only off switch
for a Reminder the owner wrote.

## The first run has no words of its own

Noticed 2026-09-05, discussing what a new owner meets.

An empty workspace hands the owner the hardest task in the product first — invent the structure —
before they have seen what Save does. A block is added to the instruction while the database and the
history are both empty: ask at most three questions, then propose one Goal, one Direction and two
Actions as a single proposal, and explain no mechanism in words.

The prefix stays byte-stable in the way that matters: the condition turns over once in the life of an
install and never back. A condition that can flip twice — fewer than three Cards, say — would not,
so the emptiness test is written one-way on purpose.

## A Goal with no Value is not visible as one

Noticed 2026-09-05, placing Values above Goals rather than beside them.

A Value stands above a Goal, so a Goal linked to none is one the owner has no stated reason for. The
link is many-to-many and optional by nature, so this cannot be refused the way a root-level Direction
is. It is shown instead, on the Card itself.

## A Sprint number says nothing about when it ran

Noticed 2026-09-05, reading a Sprint number in a receipt.

`Sprint.number` is the highest ever used plus one, so it has to be looked up before it means a date.
Proposed instead: yy.MM.xx, where the last pair counts that month's Sprints — 26.09.01, 26.09.02. It
is unique, it sorts as text in the order it ran, and it reads as a date with nothing to look up.

Two things it needs decided. The month is the one the Sprint started in, so a Sprint crossing a month
boundary keeps the month it began. The last pair follows the highest ever used within that month, so
deleting a Sprint cannot hand its label to another, which is what the integer already guarantees. The
column stops being an integer, so this is a schema change and a rebuild.
