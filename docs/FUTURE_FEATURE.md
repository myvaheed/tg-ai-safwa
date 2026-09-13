# Entries kept, and questions already settled

**These entries are not implemented specifications.** An entry records a gap or a proposed change;
Agreed records the owner's direction, while the usage cases still await their implementation batch.
An entry leaves this file by becoming an approved scenario package under
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

Agreed 2026-09-08: the two are separate switches and neither replaces the other. With both on, one
message carries both — the account of the day, and the invitation to write it down when that day
has no entry yet. With one on, only that one appears. Either way the Diary is asked about once.
The time is still undecided.

## Onboarding covers the first start and a return after an absence

Noticed 2026-09-05, discussing what a new owner meets.

The first start helps the owner with an actual intention, without requiring a Goal, a Subgoal
and two Actions, or a fixed number of questions. The prompt should cover the owner's different
starting points. Keeping this guidance active through the first day, rather than one turn, is a
candidate; its duration and completion condition are undecided.

A return after a long absence is a second situation: review what is still relevant and help the
owner restart the Sprint rhythm without treating all old work as a renewed commitment. Two weeks
is a candidate threshold, not a settled rule. What counts as absence also needs a definition.

A dedicated onboarding.feature is being considered for these two flows. Its changing state must
remain outside the stable system-prompt prefix. No scenario package is approved yet.

## Hard Time carries a computed time and an explanation

Agreed 2026-09-06. Hard Time gains a description and reuses the existing Reminders mechanism for
resolving natural-language timing, validating parameters and calculating occurrences. Reuse its
one-off, interval, daily and weekly schedules, timezone handling and advancement rules; do not
design a separate scheduling system. The proposal shows the resolved schedule and description
before Save.

Repeatable creates the next Action instance; a repeating Hard Time schedule determines its time
using the same occurrence calculation as Reminders. An Action can still repeat without Hard Time.

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
With the visible-plan flow, expiry keeps the plan and reports the stop in the Receipt; it does
not count as successful completion and cannot restart the chain through fulfillment repair.

## A Card's kind can change while its structure and work history allow it

Agreed 2026-09-06. Allow kind changes on open, unarchived Cards, with restrictions only where the
conversion changes the tree or the meaning of recorded work:

- The final tree must be valid: a Goal is root-level, a Subgoal belongs to a Goal, and an Action
  has no children. Children block conversion to Action; Goal-to-Subgoal conversion may keep
  Action children, but cannot keep Subgoal children. Subgoal-to-Goal can keep its children.
- An Action being converted must be in Backlog, have no current or past Sprint commitments, and
  neither repeat nor belong to a repeat series. This keeps conversion out of scope accounting
  and repeat history. These Action restrictions do not apply to Goal-to-Subgoal or the reverse.
- Becoming an Action requires its own effort and stage. Becoming a container clears Action-only
  fields, shown explicitly in the proposal, and derives the container's state from its children.
  Any necessary parent change happens in the same proposal, with both affected branches updated.
- Preserve the Card's identity, text, Values, Tags and Checks. Answered Checks do not block a
  conversion: their links and observations remain on the same Card, and later Action completion
  uses the existing Check rules without resetting answers.

## Retire raw-capture Ideas

Agreed 2026-09-13. Remove the raw-capture Idea entity introduced in Wave 1. Cards retain Goal,
Subgoal and Action; no separate capture entity replaces Idea. Retire its list, Expand entry point,
creation choices, AI view and references, including the default «Все идеи» Request. Handle existing
pre-release Idea data and any saved Requests querying the retired view in the declared removal
batch, leaving no dangling query entry points. The current Idea scenarios remain implemented
behavior until that batch replaces their coverage; this planning decision does not change data now.

The replacement investment is the Proposal workflow below, which helps the owner see and control
the changes Safwa is preparing for their current request.

## A visible plan accompanies a Proposal chain

Agreed 2026-09-13. Before preparing Proposals, Safwa shows what it understood and intends to
change as a plain-text plan. It then shows a second, separate progress message: preparation is
running, and the owner can stop it with an inline **Cancel** button. There is no separate plan
approval step. The plan is part of the current request, not a new workspace entity.

The Advisor and its subagents keep their existing history windows, workspace context, tool
results and instructions. The plan does not replace the conversation or narrow execution to
its own text. It describes intended changes, never claims those changes are already saved.

**The two messages have different lifetimes.** When review becomes available, the preparation
message is removed and the normal Proposal queue takes its place, one Save/Discard screen at
a time. The plan remains visible throughout the entire request's chain, including dependent
work prepared after an earlier Save. Only successful completion of the whole chain removes
the plan. Resolving one batch or showing the first Proposal is not completion.

**Cancel stops; it does not pause.** Cancel during preparation stops the current work, removes
the progress message, preserves the plan and shows a Receipt. Pending work cannot later open
review screens or continue in the background. Any already saved changes remain saved.

**Discard stops the remaining chain.** The selected Proposal is discarded, every undecided
remainder is closed without applying it, and the review is replaced with a Receipt. The plan
stays. The Receipt distinguishes saved work, the owner's discarded item and work stopped before
execution; it does not pretend every remaining item was individually rejected.

**Text written over a review stops that chain too.** Close its pending reviews, preserve the
plan and replace the screen with a Receipt before handling the text. The text is an ordinary
new Advisor request, not permission to resume the stopped chain automatically. A correction may
authorize a revised plan; a cancellation ends the work; ambiguity is handled in normal dialogue.
Discard by itself generates only the Receipt and no automatic revision or replacement Proposals.

Cancelled, interrupted or unsuccessful work keeps the plan for reference. A retained plan is
not an active job and never restarts itself. Fulfillment validation must respect this terminal
stop; it may not recreate stopped work as a missing part of the old request.

Planned acceptance cases for the implementation batch:

| Situation | Expected behavior |
|---|---|
| Safwa starts preparing one or several Proposals | The owner sees the plan first and a separate preparation message with a clickable Cancel button second. No extra plan approval is requested. |
| A subagent prepares changes | Its normal history, workspace data and tool outcomes remain available; there is no plan-only context. |
| Cancel is pressed before review is ready | Preparation ends, its status disappears, the plan stays and a Receipt reports the stop. No late result opens a review. |
| Proposals become available | The preparation message disappears; the first Save/Discard review opens while the plan stays visible. |
| More preparation is needed after an earlier Save | The same plan covers the dependent work; the active preparation can be cancelled, without another plan or duplicate progress messages. |
| An early batch was saved but dependent work remains | The plan stays until the whole chain finishes successfully. |
| Every intended change was saved successfully | The plan is removed after the chain ends; the ordinary completion Receipt/result remains. |
| The owner discards the first or a later Proposal | The rest of that chain stops. The plan stays, a Receipt replaces the review, and earlier saved changes remain. Nothing is automatically regenerated. |
| The owner writes a correction over a review | The old chain closes with a Receipt and its plan stays. The Advisor considers the new words normally; a new request can produce a revised plan and a fresh chain. |
| The owner writes a cancellation over a review | The chain closes with a Receipt and the plan stays; no replacement Proposals appear. |
| Preparation fails or the chain ends with unresolved errors | Keep the plan, remove active preparation controls and report the actual outcomes without claiming full completion. |
| An old Cancel button or generation result arrives after the request ended | It cannot cancel a newer request, restart old work or publish a stale review. |
| All initiative hooks are disabled | Plan, Cancel, review and Receipt behavior still works as part of Proposals. |

These are future acceptance cases. The implementation batch must replace the affected existing
BRD cases and their tests together, particularly queue continuation on Discard and reuse of an
interrupted session. [AGENT_ARCH.md](AGENT_ARCH.md#planned-proposal-plan-and-preparation-lifecycle)
owns the runtime boundaries; [PLAN_FEATURES.md](PLAN_FEATURES.md#follow-up--proposal-plan-and-cancel-planned)
places the work after Wave 1. There is no dependency on RAG, a new hook or a separate plan store.

## Proposal fulfillment validation

Agreed 2026-09-08: fulfillment validation belongs to the architecture of Proposals and is
designed independently of hooks, their registration, and initiative suppression.

The intent is to compare the owner's request and subsequent corrections with actual saved,
discarded, failed and pending Proposal outcomes, then pursue still-authorized missing work.
A requested Card does not fulfill a request that also asked for a Reminder. Validation must
avoid duplicating saved work or recreating an intention the owner deliberately withdrew.
Cancel, Discard and text interrupting review end the current chain under the visible-plan flow.
That stop takes precedence over automatic fulfillment repair; further work needs a new request.

[PROPOSAL_VALIDATION.md](PROPOSAL_VALIDATION.md) holds the architecture proposal, including
request-wide aggregation, bounded correction, foreground execution and interruption handling.
Those details still need their own agreement; moving this feature out of hooks does not
implicitly settle them.

## POTENTIAL HOOKS

Discussed 2026-09-07. Candidates for helping the owner remember intentions and keep work moving.
The numbering follows the discussion and is preserved for references. This is a mixed candidate
catalogue: 1, 4 and 15 are instructions, 9 belongs to Proposals, and 14 is shared infrastructure.
The classification audit is in [HOOK_ARCH.md](HOOK_ARCH.md#9-проверка-архитектуры-на-всём-наборе-кандидатов).
None of these entries is an approved scenario package.

### The shape every hook has

Agreed 2026-09-08: a hook does not have to be a literal model tool call. A known service
operation may run directly; other reactions may offer a tool or involve the Advisor.

The owner wants one explicit registration contract and an easy way to see which hooks are
enabled. [HOOK_ARCH.md](HOOK_ARCH.md) proposes the central connection list, typed event inputs,
a small set of runtime reactions, the occasion journal, and concrete implementation stages.
That design remains a proposal; this entry does not approve all of its API details.

The trigger is distinct from execution and delivery. Each initiative defines its own occasion
identity and repetition rules. Preparing a tool result is neither saving a Proposal nor showing
a question to the owner.

A hook is an automatic reaction to a named lifecycle event: a completed turn, a tool boundary,
a committed domain change or a scheduled condition check. A command or button that explicitly
starts the requested work calls its workflow directly. Thus requested retrospective analysis,
manual /summarize and the Proposal plan/preparation lifecycle are not hooks; automatic Summary
after a turn can be one. The injection of a prompt or use of RAG does not determine which category
an operation belongs to.

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

### 5. Goals and Subgoals with no Actions — hook

After a grace period, tentatively about one day after creation, scan Goals and Subgoals for
the absence of any descendant Action. Look through the whole branch, so an Action under a
Subgoal also counts for its Goal. Report the matching parents together and offer to create
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

This candidate is a hook only as an interception before the ordinary Card creation tool. Its
interactive clarification and continuation still need design. The visible Proposal plan does not
require this candidate or introduce another retrieval workflow. A stopped Proposal chain cannot
be resumed by this hook without a new owner request.

### 9. Proposal fulfillment validation — moved out of hooks

Agreed 2026-09-08: this belongs to the architecture of Proposals, not the hook system.
The number is kept only to preserve the discussion's references. See
[the separate feature](#proposal-fulfillment-validation) and
[PROPOSAL_VALIDATION.md](PROPOSAL_VALIDATION.md).

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

This entry combines an event-driven classification/reaction with a screen requirement. Starting
classification after saved Sprint changes and reacting to the saved remaining set are hooks;
sorting Today from that classification is ordinary presentation, not a hook on opening the screen.

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

This is infrastructure used by initiative hooks, not a hook with its own independent trigger.

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
rescheduling the occurrence may also justify asking again. Whether adding to Sprint and adding to
Today are one offer or two is that hook's own rule to state, like every other occasion key.

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
Proposal fulfillment validation belongs to its own architecture and is unaffected by whether
initiative hooks are enabled or a related suggestion has already been shown.

### 15. Carry retrospective decisions through memory — Advisor instruction

Put the owner's agreed retrospective takeaway or experiment in memory and instruct the Advisor
to use it in later planning and advice. Preserve its intended scope, such as "try this in the
next Sprint", rather than turning a temporary experiment into a permanent fact about the owner.
An AI suggestion the owner never adopted is not an agreed decision. The existing retrospective
memory review remains the place to save it; no separate next-Sprint reminder hook is needed.
