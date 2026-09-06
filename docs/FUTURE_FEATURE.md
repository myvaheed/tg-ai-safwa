# Ideas kept, and questions already settled

Nothing here is a commitment and nothing here has a scenario. Each entry is a real gap noticed
while something else was being decided, written down so the reasoning does not have to happen
twice. An entry leaves this file by becoming an approved scenario package, or by being dropped.

## The Advisor cannot read the conversation by date

Noticed 2026-08-30, reviewing how the Advisor reads history.

Asked what was said on Tuesday, the Advisor answers from the window it always reads — the newest
messages up to the token budget, stopping at the newest Summary. Only the Diary subagent reaches a
named day, through `read_day`, and a question about the chat is not routed there.

A Summary is a compressed account, not an index into the original messages. Its dated sections do
not give the Advisor a way to retrieve a named day's conversation.

This is a recorded limitation, not an approved request for date-based search. `DI-READ-016` keeps
the Diary reading a whole named day; a separate Advisor history reader remains undecided.

## The middle Card kind is named for the one thing it cannot be

Noticed 2026-09-05, discussing the shape of the tree rather than any code.

An Idea carries no field of its own: stage, effort, energy, repeat and Blocked are an Action's, and
everything an Idea shows is derived from its children. The word promises something optional; the
mechanism is a container under a Goal. Two changes were agreed, neither has a scenario yet:

- Idea becomes Direction (Направление). Its stage, including Done, remains derived from its
  children. Goal keeps its name; Values may cross Goals.
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
`Literal` in [ai/contracts.py](../src/tg_agent_shell/ai/contracts.py) and the whole-number check in
[profile/use_cases.py](../src/safwa/features/profile/use_cases.py).

## The daily summary is a second system Reminder

Noticed 2026-09-05, after a digest that decides for the owner was declined.

A daily summary acknowledges what the owner did that day, with encouragement grounded in the
actual work. It is not an overdue-work report. When that day's Diary is empty, it offers to write
the day down; when an entry exists, it does not ask again. It never invents an achievement to
make an empty day look productive.

RM-SYSTEM-022 already carries the shape: the Diary nudge is a Reminder Safwa set up, absent from the
Reminder list, switched in Settings. A daily summary is a second one of those, on by default and
turned off in the same place. RM-WRITE-010 is untouched, because deleting stays the only off switch
for a Reminder the owner wrote.

The time is undecided. The summary's Diary invitation and the existing Diary nudge must not ask
the same question twice; whether the summary replaces that nudge is still to be decided.

## Onboarding covers the first start and a return after an absence

Noticed 2026-09-05, discussing what a new owner meets.

The first start helps the owner with an actual intention, without requiring a Goal, a Direction
and two Actions, or a fixed number of questions. The prompt should cover the owner's different
starting points. Keeping this guidance active through the first day, rather than one turn, is a
candidate; its duration and completion condition are undecided.

A return after a long absence is a second situation: review what is still relevant and help the
owner restart the Sprint rhythm without treating all old work as a renewed commitment. Two weeks
is a candidate threshold, not a settled rule. What counts as absence also needs a definition.

A dedicated onboarding.feature is being considered for these two flows. Its changing state must
remain outside the stable system-prompt prefix. No scenario package is approved yet.

## A Goal with no Value is not visible as one

Noticed 2026-09-05, placing Values above Goals rather than beside them.

A Goal's screen shows whether it has linked Values. The many-to-many link stays optional: an
absent link says nothing about whether the owner has a reason for the Goal.

## A Sprint number says nothing about when it ran

Noticed 2026-09-05, reading a Sprint number in a receipt.

`Sprint.number` is the highest ever used plus one, so it has to be looked up before it means a date.
Proposed instead: yy.MM-xx, where the last pair counts that month's Sprints — 26.09-01, 26.09-02.
The zero-padded parts sort as text by start year, month and sequence; the last pair is a sequence
number, not a day of the month.

Two things it needs decided. The month is the one the Sprint started in, so a Sprint crossing a month
boundary keeps the month it began. The last pair follows the highest ever used within that month, so
deleting a Sprint cannot hand its label to another, which is what the integer already guarantees. The
column stops being an integer, so this is a schema change and a rebuild.

## Hard Time carries a computed time and an explanation

Agreed 2026-09-06. Hard Time gains a description and reuses the existing Reminders mechanism for
resolving natural-language timing, validating parameters and calculating occurrences. Reuse its
one-off, interval, daily and weekly schedules, timezone handling and advancement rules; do not
design a separate scheduling system. The proposal shows the resolved schedule and description
before Save.

Repeatable creates the next Action instance; a repeating Hard Time schedule determines its time
using the same occurrence calculation as Reminders. An Action can still repeat without Hard Time.

## One ready-made Request explains Saved Requests

Discussed 2026-09-06. Provide the ordinary Saved Request "Все цели" (All goals) without first
asking the Advisor to create it. It returns every Goal under the normal Request rules, including
archived ones, and rerunning it reflects the current Cards. It is an installation default, not
a protected system Request: the owner can change or delete it, and deleting it must not make it
reappear on every start. How an existing installation receives it remains to be designed.

The Requests list also offers "Что такое Saved Requests?". It opens an explanation with a Back
button returning to the list. Suggested text:

> Сохранённый запрос — это подборка карточек по условию. Например, «Все цели» показывает ваши
> цели, а запрос «Дела дома» может находить действия с тегом «дом». При каждом открытии подборка
> обновляется. Теги помогают объединять карточки из разных целей. Попросите Safwa создать нужную
> подборку — после сохранения её можно открывать одной кнопкой.

This is a planned exception to SR-WRITE-001, which currently requires Advisor proposals for every
Request. No implementation or scenario change is part of this discussion.

## The retrospective is where Safwa learns from a Sprint

Discussed 2026-09-06. When a Sprint ends, its retro screen first shows statistics calculated by
code. The owner may then press "Анализ с ИИ" to begin a guided analysis. The screen is useful
even if the owner does not request AI analysis.

The workflow examines the completed Sprint step by step: compare the work and results with the
Success criteria, examine relevant Diary entries and the owner's explanations, discuss what
helped or got in the way, and end with an assessment and practical advice. Keep each interaction
small enough for a small local model and the existing sequential proposal flow.

Only retrospective analysis updates inferred facts in memory.md; scheduled upkeep throughout the
Sprint is removed. Explicit facts the owner writes remain under the owner's control. The Advisor
continues reading the Diary for mood and period reviews; reading it does not update memory.

Still to decide: the workflow's steps, how an interrupted analysis resumes, how conclusions and
memory changes are reviewed, and what happens when the same Sprint is analysed again. A conclusion
about the owner must be distinguishable from a recorded fact and open to correction.

## An unanswered proposal expires after one hour

Agreed 2026-09-06. After one hour without acknowledged owner interaction with the displayed
review, discard all remaining proposals in its request; time spent queued does not count. Keep
already saved changes. Close the suspended request without regenerating proposals, and replace
the review with an account saying the system closed it due to inactivity, not that the owner
rejected it. Only then deliver waiting Reminders, preserving the single-screen rule. Reading or
unsent typing does not reset the timer. Manual editors also need an expiry rule that releases
their live screen. The timeout bounds waiting rather than guaranteeing delivery at the due time.

## A Card's kind can change while its structure and work history allow it

Agreed 2026-09-06. Allow kind changes on open, unarchived Cards, with restrictions only where the
conversion changes the tree or the meaning of recorded work:

- The final tree must be valid: a Goal is root-level, a Direction belongs to a Goal, and an Action
  has no children. Children block conversion to Action; Goal-to-Direction conversion may keep
  Action children, but cannot keep Direction children. Direction-to-Goal can keep its children.
- An Action being converted must be in Backlog, have no current or past Sprint commitments, and
  neither repeat nor belong to a repeat series. This keeps conversion out of scope accounting
  and repeat history. These Action restrictions do not apply to Goal-to-Direction or the reverse.
- Becoming an Action requires its own effort and stage. Becoming a container clears Action-only
  fields, shown explicitly in the proposal, and derives the container's state from its children.
  Any necessary parent change happens in the same proposal, with both affected branches updated.
- Preserve the Card's identity, text, Values, Tags and Checks. Answered Checks do not block a
  conversion: their links and observations remain on the same Card, and later Action completion
  uses the existing Check rules without resetting answers.

## Cancelled is removed

Agreed 2026-09-06. Remove the Cancelled stage and its controls. The owner deletes a Card that is
no longer relevant. Deleting or moving a Card out of a Sprint remains reflected in that Sprint's
scope accounting; a separate Cancelled state is not needed for this.
