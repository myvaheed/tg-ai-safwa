# Ideas kept, and questions already settled

**Nothing here is agreed.** Not a commitment, not a specification, and not a scenario — an entry
is a real gap noticed while something else was being decided, written down so the reasoning does
not have to happen twice. An entry leaves this file by becoming an approved scenario package under
[tests/brd/](../tests/brd/README.md), or by being dropped. `tests/test_docs.py` checks that the
links and code names here still resolve, which keeps the file readable and approves none of it.

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
`Literal` in [cards/agent.py](../src/safwa/features/cards/agent.py) and the whole-number check in
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

## A repeating Action shows when it has already been done today

Agreed 2026-09-07. Change the repeating Action's marker so the owner can see on a board or in a
citation that the series has already had a completion today. The open successor carries this
indication too: completing an instance must not leave the next one looking as though nothing
was done. Today is the owner's calendar day in the workspace timezone, and the marker reflects
actual Done completions in that series.

The indication acknowledges today's work without completing the successor or preventing another
completion that day. Its exact wording or symbol is undecided. Boards and citations must show
the same meaning. A finished repeating Action still cannot be reopened, because its successor
already exists; deletion remains available.

## A Card opens in a compact view with full editing one button away

Agreed 2026-09-07. Open a Card in a compact view with its essential information and primary
actions. A button named "Полное редактирование" (Full editing) opens the full view with all of
the Card's applicable editing buttons. The owner can return to the compact view.

The compact view reduces the controls shown during everyday use. The existing fields and
operations remain available through full editing. The exact compact fields and primary buttons
are still to be decided.

## POTENTIAL HOOKS

Discussed 2026-09-07. Candidates for helping the owner remember intentions and keep work moving.
The numbering follows the discussion. Some candidates belong in Advisor instructions instead;
none of these entries is an approved scenario package.

### 1. Capture an intention from the conversation — Advisor instruction

Teach the Advisor to offer a Card or a Reminder when the conversation makes one useful. Checking
every new intention with a separate hook would trigger too often; the Advisor can make this
offer from the context it already reads. This is an instruction candidate, not a hook.

### 2. After setting a blocker, offer a Reminder — hook

Once a blocker has actually been saved, ask whether to set a Reminder for when the owner can
return to the question. If they want one, agree when and prepare the ordinary Reminder proposal.
An already arranged follow-up should not cause the same question again. Preparing the blocker
proposal alone is not the trigger: the blocker must have been applied.

### 3. Match new information against existing blockers — rejected

Do not add a hook that checks every new piece of information against existing blockers and
analyses whether one has been resolved. That would require too much repeated checking, and
the owner cannot be expected to report every change in their circumstances or intended activity.

### 4. Mention another suitable task in context — Advisor instruction

The intended Advisor context includes the Today and Sprint task lists. Instruct it to use those
lists to mention a relevant task when the conversation presents a useful opportunity to do it.
The context provision is part of this idea; a separate hook for each opportunity is unnecessary.

### 5. Goals and Directions with no Actions — hook

After a grace period, tentatively about one day after creation, scan Goals and Directions for
the absence of any descendant Action. Look through the whole branch, so an Action under a
Direction also counts for its Goal. Report the matching parents together and offer to create
at least one Action. If the owner is not ready to decompose the work, offer an Action such as
"Запланировать действия для цели X" (Plan Actions for Goal X).

The grace period, scan timing and conditions for repeating the reminder remain to be decided.

### 6. An unfinished Action repeatedly selected for Today — hook

When an Action has been selected for Today on several consecutive days without reaching Done,
mention it in the morning and suggest tackling it early to get the repeatedly postponed task
out of the way. This should follow the same unfinished occurrence: a repeating Action completed
successfully and replaced by its successor is not evidence of postponement.

The number of days and what counts as another day's selection into Today remain to be decided,
including how an Action simply left in Today across midnight should be treated.

### 7. Today's work exceeds the daily capacity — hook

Consider a daily capacity of roughly 13-15 EP, with a warning when the day's total exceeds
15 EP. Explain that too much work has been selected for one day and offer to move something
else out of Today to make room. This is a daily load check, separate from Sprint capacity.

The numbers are candidates. The counting rule still needs a decision, particularly whether
work already completed that day contributes alongside the remaining planned work.

### 8. Retrieve similar existing entities before creating another — candidate hook

Consider fast retrieval over database entities, using RAG or a similarity search. Before Card
creation, a similarity score around 0.94-0.95 could trigger a hook that gives the AI a short list
of existing Cards to inspect. It can then point out a likely match and ask whether that is the
Card the owner meant. Similarity selects candidates for review; it does not establish identity.

The retrieval approach and score threshold remain experimental and need to be checked against
the actual retrieval model and the owner's data.

### 9. Validate fulfillment after Proposals execute — candidate hook

Associate each executed Proposal with a validator that receives the original user request and
the actual Proposal outcome. Use validation to keep pursuing any missing work until the request
has been fully resolved. For example, saving the requested Card does not fulfill a request that
also asked for a Reminder if that Reminder was never created.

Validation must run as foreground work. A new owner message must be accounted for through an
explicit cancellation path, with a notice that the AI was validating the save. The precise
interruption and message-handling behavior still needs design; already saved changes remain
saved when validation is interrupted.

Reliability questions still to settle:

- How each Proposal's validator contributes to validation of the whole request, including
  dependent Proposals and work that is still awaiting the owner's decision.
- How the validator verifies actual saved state rather than trusting a model's statement that
  the request is complete.
- How saved, discarded and failed outcomes are distinguished, so validation neither duplicates
  saved work nor recreates a change the owner deliberately rejected.
- How corrective Proposals and repeated validation make progress, with a bounded retry policy
  and an explicit unresolved result when completion cannot be achieved.
- What a new message cancels, how that message is handled, and whether interrupted validation
  is resumed or superseded by the new request.

### 10. Key Actions tied to Sprint Success criteria — hook

After a Sprint starts, have the AI identify which of its Actions directly contribute to its
Success criteria. Keep that classification for this particular Sprint, with a way for the
owner to correct it. The relationship belongs to the Sprint and the Action together, rather
than making an Action permanently key. A separate table or an extension of the existing
Sprint commitment relationship remains an implementation choice.

When classification finishes without finding any key Actions, ask whether the owner wants to
add Actions that serve the Success criteria. Classification not yet performed is not the same
as classification that found none. When a previously non-empty set of current key Actions
becomes empty, discuss what happens next: return an Action, choose another, or finish the
Sprint early and plan a new one.

Keep the history and the reason each key Action left the remaining work. Key Actions that were
completed call for a question about whether the Success criteria were achieved; Actions removed
from the Sprint call for a question about the plan. An empty remaining set alone does not prove
that the Sprint failed or succeeded. Evaluate the result after a whole Proposal chain settles,
so replacing one key Action with another does not trigger a warning between the two writes.

Key Actions should be prominent when the owner opens Today, alongside applicable Hard Time and
Critical priority. A candidate order is Hard Time relevant now, then Critical, then key Actions,
then the rest. The exact ordering and the time window that makes Hard Time relevant are undecided;
a future appointment should not stay at the top all day merely because it has a time.

### 11. Repeated Missed observations — hook with a flexible Advisor response

After an answer is saved, check whether the repeating Check's series meets a condition such as
three consecutive Missed answers. This applies to any repeating Check, including one with no
Action attached. Pending is not Missed. The threshold remains to be decided.

Give the Advisor the observed pattern and relevant context, without prescribing one response.
Its instruction should allow support, a clarifying question, an offer of a Reminder, a change
of approach or activity, rest, or another appropriate response. It may also decide that no
suggestion is needed. The meaning of the Check and the owner's circumstances determine the
response; a repeated Missed result does not establish its cause.

### 12. Added work displacing the original Sprint plan — rejected

Do not add a separate mid-Sprint hook comparing completion of added work with the original
commitments. The owner may simply have omitted work from the initial plan, and the comparison
alone does not justify interrupting them. Leave examination of what happened to the retrospective.

### 13. An approaching Hard Time is outside the plan — hook

Once Hard Time carries its planned schedule, check whether an open Action's upcoming occurrence
falls within the period being planned but the Action has been left out. At Sprint start this
can reveal an Action still in Backlog; when choosing today's work it can reveal one absent from
Today. Offer to include that specific Action, for example: "Подача документов назначена на
четверг, но задача осталась в Backlog. Включить её в текущий спринт?"

This brings up relevant work outside the Today and Sprint lists already supplied to the Advisor.
The advance window and check timing remain to be decided. Repeated checks must respect prior
offers and owner decisions through the occurrence journal below.

### 14. Remember hook occasions and the owner's decisions — shared design candidate

Persist the specific occasion for a suggestion and what happened to it. A stable identity is
the hook type, its subject and the particular occasion. A hash may encode that identity, but
hashing all Card, Hard Time and Sprint data would make unrelated edits trigger the same question
again. Each hook defines which changes constitute a genuinely new occasion.

| Hook | One occasion |
|---|---|
| Hard Time outside the plan | The Card or repeat series and one particular scheduled occurrence |
| Repeated Missed answers | The Check series and the current run of unsuccessful observations |
| Follow-up after a blocker | The Card and one period of being blocked |
| Daily capacity exceeded | One calendar day in the owner's timezone |
| Key Actions disappear | The Sprint and one transition from having current key Actions to having none |

For Hard Time, renaming the Card or starting another Sprint does not by itself invalidate a
refusal concerning the same occurrence. The next scheduled repetition is a new occasion;
rescheduling the occurrence may also justify asking again. Whether adding to Sprint and adding
to Today are the same offer or distinct questions still needs an explicit rule.

For Checks, a fourth Missed after an offer on the third is still the same occasion. A later run
after recovery may create a new one. The reset condition belongs to that hook. Likewise, raising
the day's load from 16 to 17 EP should not produce another copy of the same capacity warning.

Distinguish waiting for processing, an offer actually shown, and an explicit owner decision:
accepted, declined or postponed. Remember when the Advisor considered an occasion and chose
not to intervene as well. Silence from the owner is not a refusal, but an optional suggestion
already shown normally does not need repeating. Postponement establishes when to return; an
explicit request never to suggest this for a particular subject has a wider scope than one
occasion. Exact response handling and storage remain to be designed.

Use the existing Cue delivery mechanism, with a durable journal that survives delivery: the
Cue itself is deleted once delivered and cannot be the lasting record of an offer or refusal.
Before speaking, recheck that the occasion still applies. A generation or delivery failure is
not evidence that the owner saw the offer or rejected it.

The journal suppresses repeated approaches to the owner; condition checks may keep running.
Proposal fulfillment validation is a different responsibility and must not be disabled merely
because a related suggestion has already been shown.

### 15. Carry retrospective decisions through memory — Advisor instruction

Put the owner's agreed retrospective takeaway or experiment in memory and instruct the Advisor
to use it in later planning and advice. Preserve its intended scope, such as "try this in the
next Sprint", rather than turning a temporary experiment into a permanent fact about the owner.
An AI suggestion the owner never adopted is not an agreed decision. The existing retrospective
memory review remains the place to save it; no separate next-Sprint reminder hook is needed.
