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

## Proposal fulfillment validation

Agreed 2026-09-08: fulfillment validation belongs to the architecture of Proposals and is
designed independently of hooks and their registration.

The intent is to compare the owner's request and subsequent corrections with actual saved,
discarded, failed and pending Proposal outcomes, then pursue still-authorized missing work.
A requested Card does not fulfill a request that also asked for a Reminder. Validation must
avoid duplicating saved work or recreating an intention the owner deliberately withdrew.

[PROPOSAL_VALIDATION.md](PROPOSAL_VALIDATION.md) holds the architecture proposal, including
request-wide aggregation, bounded correction, foreground execution and interruption handling.
Those details still need their own agreement; moving this feature out of hooks does not
implicitly settle them.

## POTENTIAL HOOKS

Discussed 2026-09-07. Candidates for helping the owner remember intentions and keep work moving.
The numbering follows the discussion and is preserved for references. This is a mixed candidate
catalogue: 1, 4 and 15 are instructions, 9 belongs to Proposals, and 14 is rejected.
The classification audit is in [HOOK_ARCH.md](HOOK_ARCH.md#7-проверка-архитектуры-на-всём-наборе-кандидатов).
None of these entries is an approved scenario package.

### The shape every hook has

Agreed 2026-09-08: a hook does not have to be a literal model tool call. A known service
operation may run directly; other reactions may offer a tool or involve the Advisor.

The owner wants one explicit registration contract and an easy way to see which hooks are
enabled. Wave 3 began on 2026-09-15: the central connection list and the AfterTurn/Run and
AfterTool/OfferTool paths now carry automatic Summary and the Heavy analyzer offer. Their
registration and session rules are covered by
[agents.feature](../tests/brd/tg_agent_shell/agents.feature); disabling automatic Summary keeps
its command, as covered by [summary.feature](../tests/brd/summary.feature).
[HOOK_ARCH.md](HOOK_ARCH.md) distinguishes this implemented contract from the planned committed
events and timed checks. This entry remains open for those later stages.

A hook is on or off as a whole, and only the owner switches it, by hand in the Profile, where
every hook that has a switch is listed with a title and a description. A hook without one, such
as the Heavy analyzer offer, is always on, the way a system Reminder cannot be deleted. There
are no exceptions for one Card or Goal, no journal of consents and refusals, and no editing of
these switches by the AI or through a Proposal; "never ask about this again" is the switch.

The trigger is distinct from delivery. An initiative is one request to the Advisor, delivered
through the existing `Cue` queue; what the Advisor says and what the owner replies are ordinary
turns, and the hook reads neither. One hook has one pending initiative at a time: a new trigger
adds to it rather than queueing a second question, and just before it is delivered the feature
rereads what it refers to and either asks about what is still current or drops the initiative.
A hook that checks state on a schedule may ask about the same thing again in its next period.
Preparing a tool result is neither saving a Proposal nor asking the owner.

A hook is an automatic reaction to a named lifecycle event: a completed turn, a tool boundary,
a committed domain change or a scheduled condition check. A command or button that explicitly
starts the requested work calls its workflow directly. Thus requested retrospective analysis,
manual /summarize are not hooks; automatic Summary after a turn can be one. The injection of a prompt or use of RAG does not determine which category
an operation belongs to.

### 1. Capture an intention from the conversation — Advisor instruction

Teach the Advisor to offer a Card or a Reminder when the conversation makes one useful. Checking
every new intention with a separate hook would trigger too often; the Advisor can make this
offer from the context it already reads. This is an instruction candidate, not a hook.

### 2. After setting a blocker, offer a Reminder — shipped

Shipped 2026-09-15 as [CD-BLOCKED-034](../tests/brd/cards.feature); the number stays for
references. Whether a follow-up is already arranged is not read: nothing links a Card to a
Reminder, and the Advisor has the dialogue.

### 3. Match new information against existing blockers — rejected

Do not add a hook that checks every new piece of information against existing blockers and
analyses whether one has been resolved. That would require too much repeated checking, and
the owner cannot be expected to report every change in their circumstances or intended activity.

### 4. Mention another suitable task in context — Advisor instruction

The intended Advisor context includes the Today and Sprint task lists. Instruct it to use those
lists to mention a relevant task when the conversation presents a useful opportunity to do it.
The context provision is part of this idea; a separate hook for each opportunity is unnecessary.

### 5. Goals and Subgoals with no Actions — shipped

Shipped 2026-09-16 as [CD-EMPTY-035](../tests/brd/cards.feature); the number stays for
references. The grace is 24 hours and the check runs at 09:00 local time, both constants of
the Cards feature; the request puts both ways forward in one message — plan the Actions now,
or create one Action "Запланировать действия для цели X" — and waits for the choice.

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
interactive clarification and continuation still need design.

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
The advance window and check timing remain to be decided. The check repeats on its own period;
the Profile switch is what stops it.

### 14. Remember when a hook asked — rejected

Do not add a table of hooks, subjects and when each was asked about, nor any other memory of
the owner's replies. A hook remembers only its one pending request, and that request is deleted
when it is delivered, dropped or its hook is switched off. Whether something is still worth
asking about is a read just before delivery, not a record of the past; a hook that checks state
on a schedule simply asks again in its next period, and the Profile switch is the only way to
stop it. The Advisor's request and the owner's answer are ordinary turns; a refusal or a
postponement are words in the dialogue the Advisor reads like any other. Proposal fulfillment
validation belongs to its own architecture and is unaffected by hooks.

### 15. Carry retrospective decisions through memory — Advisor instruction

Put the owner's agreed retrospective takeaway or experiment in memory and instruct the Advisor
to use it in later planning and advice. Preserve its intended scope, such as "try this in the
next Sprint", rather than turning a temporary experiment into a permanent fact about the owner.
An AI suggestion the owner never adopted is not an agreed decision. The existing retrospective
memory review remains the place to save it; no separate next-Sprint reminder hook is needed.
