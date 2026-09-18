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
code — shipped 2026-09-16 as [RT-STATS-003](../tests/brd/retro.feature): effort taken and
finished, the plan's initial, added and removed parts, Actions finished, remaining and blocked,
and Passed and Missed per Check series tied to a Value. The owner may then press "Анализ с ИИ"
to begin a guided analysis. The screen is useful even if the owner does not request AI analysis.

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
catalogue: 15 is an instruction, 1 is withdrawn, 4 became two hooks, 9 belongs to Proposals,
and 14 is rejected.
The classification audit is in [HOOK_ARCH.md](HOOK_ARCH.md#7-проверка-архитектуры-на-всём-наборе-кандидатов).
None of these entries is an approved scenario package.

### The shape every hook has

Agreed 2026-09-08: a hook does not have to be a literal model tool call. A known service
operation may run directly; other reactions may offer a tool or involve the Advisor.

The owner wants one explicit registration contract and an easy way to see which hooks are
enabled. Wave 3 began on 2026-09-15: the central connection list and the AfterTurn/Run and
BeforeTool/RefuseTool paths now carry automatic Summary and the Heavy analyzer offer. Their
registration and session rules are covered by
[agents.feature](../tests/brd/tg_agent_shell/agents.feature).
[HOOK_ARCH.md](HOOK_ARCH.md) distinguishes this implemented contract from the planned committed
events and timed checks. This entry remains open for those later stages.

A hook is on or off as a whole, and only the owner switches it, by hand in the Profile, where
every hook whose effect reaches the agent — a helper offered, a request handed to the Advisor —
is listed with a title and a description. A hook that runs work of its own, such as the
automatic Summary, is always on, the way a system Reminder cannot be deleted. There
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

### 1. Capture an intention from the conversation — withdrawn

Withdrawn 2026-09-17. The Advisor already proposes a Card or a Reminder when the conversation
makes one useful; a standing instruction to do so more often is not wanted, and a hook that
checks every new intention would trigger too often.

### 2. After setting a blocker, offer a Reminder — shipped

Shipped 2026-09-15 as [CD-BLOCKED-034](../tests/brd/cards.feature); the number stays for
references. Whether a follow-up is already arranged is not read: nothing links a Card to a
Reminder, and the Advisor has the dialogue.

### 3. Match new information against existing blockers — rejected

Do not add a hook that checks every new piece of information against existing blockers and
analyses whether one has been resolved. That would require too much repeated checking, and
the owner cannot be expected to report every change in their circumstances or intended activity.

### 4. A spread of energy over the Sprint and the day — shipped as two hooks

Replaced 2026-09-17: mentioning another suitable task in passing was dropped, and what the
owner wanted from it — the plan not leaving out a kind of work — became two checks, shipped
2026-09-18. When a Sprint starts, each of the four energy types and the Rest category on no
open Action in the Sprint while an open Backlog Action carries it is brought up, with up to
three Backlog candidates per kind, Critical first
([PL-ENERGY-022](../tests/brd/planning.feature)). Each morning, a day with no open Rest Action
in Today and none finished that day, while the Sprint holds one, is brought up with the
Sprint's Rest Actions ([CD-REST-037](../tests/brd/cards.feature)). Both are switches in the
Profile; nothing is moved before the owner's answer.

### 5. Goals and Subgoals with no Actions — shipped

Shipped 2026-09-16 as [CD-EMPTY-035](../tests/brd/cards.feature); the number stays for
references. The grace is 24 hours, a constant of the Cards feature; the check runs at the
Profile's Morning time, 09:00 unless moved; the request puts both ways forward in one message —
plan the Actions now, or create one Action "Запланировать действия для цели X" — and waits for
the choice.

### 6. An unfinished Action repeatedly selected for Today — shipped

Shipped 2026-09-18 as [CD-STALE-038](../tests/brd/cards.feature); the number stays for
references. Each morning the open Actions in Today are written down for that local day, and an
Action found there three mornings in a row (TODAY_STALE_DAYS = 3), and again at each multiple,
is brought up with its count of mornings. Chosen for a day means standing in Today when the
morning comes: one left there across midnight is chosen again, a morning it is not there starts
the count over, and the next instance of a finished repeating Action is another Action with
mornings of its own.

### 7. Today's work exceeds the daily capacity — shipped

Shipped 2026-09-16 as [CD-TODAY-036](../tests/brd/cards.feature); the number stays for
references. The day holds 15 EP, and everything of the day counts: the open Actions in Today
and the Actions finished that local day. An Action entering Today is the trigger; the sum is
taken when the question is about to be said. This is a daily load check, separate from Sprint
capacity.

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

### 10. Key Actions tied to Sprint Success criteria — shipped

Shipped 2026-09-18 as [PL-KEY-023](../tests/brd/planning.feature), PL-KEY-024 and PL-KEY-025;
the number stays for references. The mark is `key_action` on the Sprint commitment — the
Sprint's and the Action's together, never the Action's alone. When a Sprint starts, and when an
Action joins it, the model is asked in batches of ten, yes or no per number, in the background;
the owner is told nothing. A Sprint left with no key Action open and none finished is told once
that its criterion does not look reachable, with nothing proposed. Today puts a Hard Time today
or tomorrow first, then Critical, then key, then the rest.

Not built, and not asked for yet: the owner correcting a mark by hand; a history of why each
key Action left; a question when a key Action is finished about whether the criterion was
reached — the Sprint summary and the retrospective are where that is discussed.

### 11. Repeated Missed observations — shipped

Shipped 2026-09-16 as [CH-MISSED-017](../tests/brd/checks.feature); the number stays for
references. After a Missed answer is saved, the series' run of Missed is counted back to its
last Passed, Pending skipped; every multiple of three asks — the third, the sixth — and nothing
remembers having asked, so a re-answer counts by itself. The Advisor is handed the pattern and
asked to raise it and ask what gets in the way, without one prescribed response: a repeated
Missed result does not establish its cause.

### 12. Added work displacing the original Sprint plan — rejected

Do not add a separate mid-Sprint hook comparing completion of added work with the original
commitments. The owner may simply have omitted work from the initial plan, and the comparison
alone does not justify interrupting them. Leave examination of what happened to the retrospective.

### 13. An approaching Hard Time is outside the plan — shipped

Shipped 2026-09-16 as [PL-HARDTIME-021](../tests/brd/planning.feature); the number stays for
references. Both readings, in one message about every such Action: an occurrence by the
Sprint's last day while the Action sits in Backlog, and one today or tomorrow while it is not in
Today. It runs when a Sprint starts and each morning at the Profile's Morning time, and the
Profile switch is what stops it.

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
