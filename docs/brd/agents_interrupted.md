# Agents, packet two — when the owner writes instead of deciding

Status: **approved** by the owner on 2026-08-29, after one round of pruning.
Batch: Phase 7.a, together with [agents.md](agents.md) — both packets write into the same
`tests/brd/agents.feature`, because one `.feature` file is one package.
Sources: [MIGRATION.md](../MIGRATION.md) §"Phase 7 notes: how an interruption is meant to work",
ruled 2026-08-28; `archived_docs/SUBAGENTS_PLAN.md` rules 9 and 10, read for intent;
current code — `ai/service.py` (`cancel_approval_for_proposal`,
`_close_lapsed_sessions`, `_resume_suspended`, `_resumed_transcript`),
[shell/chat.py](../../src/safwa/shell/chat.py) `dismiss_prior_ui`.
Supersedes nothing. PR-INTERRUPT-017, PR-INTERRUPT-018 and SC-LIVE-001 in
[proposals.feature](../../tests/brd/proposals.feature) and
[screens.feature](../../tests/brd/screens.feature) stay exactly as approved — they own what happens
to the **screen**, and this packet owns what happens to the **work behind it**.

Six scenarios. **This packet changes behaviour.** It is the one place in Phase 7 where the code that
exists today is wrong on purpose, and packet one ([agents.md](agents.md)) deliberately says nothing
about it.

---

## What this packet is about, for a reader who has not seen this codebase

Read [agents.md](agents.md) first for what the Advisor, a subagent, routing and a session are.

Safwa proposes changes rather than making them. The owner gets a screen with **Save** and
**Discard** on it, and the work that made that proposal stops there and waits.

Save and Discard are answers. But Telegram is one column of messages, and the owner has a third
option that is not a button at all: they can just keep typing. "No, call it Z." "Forget it, do this
instead." That third answer is what this packet is about.

**Today it is handled badly**, and the badness has a single cause. When the owner types over a
screen, the whole request that opened it is thrown away and their words start a brand new request —
even though those words were almost always a correction to the request just discarded. To soften
that, five separate mechanisms were built: the interrupted subagent's work is kept alive for exactly
one more turn in case the new request happens to hand the turn back to it; a sweep closes it if that
does not happen; the parts of the request above it are walked and cancelled one by one; a hint tells
the new request that a proposal it never saw may have just been corrected.

Five mechanisms all compensating for one missing property is the signal that the property is what
should have been built. The property is: **the owner's words continue the request that is already
running.** Once that is true, all five are deleted.

Two things follow, and they are what the scenarios below fix.

**The request is resumed, not replaced.** It was in the middle of something — possibly a two-part
request whose first part is already saved. It gets told what was proposed, what was refused, what was
saved, and what the owner wrote, and it carries on from there. That is also what makes the correction
reach the right place: only the part that talks to the owner can tell whether "no, call it Z" is a
correction to the open proposal or a change of subject, and it is the part that gets the words.

**The subagent keeps its work.** It was interrupted, not finished, so it still holds its own plan and
everything it had worked out. A correction reaches it with that plan intact — which is the whole
reason "the same, but capitalise the name" produces a corrected draft rather than a rewritten one.

## Scope

**In this packet:** what becomes of the running request when the owner writes over a screen, what
each part is told, how long unfinished work lives, and what a repeated hand-over inside one request
gets.

**Out, and why:**

- **What happens to the screen and to the proposals behind it.** Approved as PR-INTERRUPT-017,
  PR-INTERRUPT-018 and SC-LIVE-001: the pending proposals are refused, anything already saved stays
  saved, and the screen becomes a written account naming all of them. That order is unchanged and
  still happens before anything is generated. This packet changes only what happens to the two
  paused sessions afterwards.
- **How a session is paused, restored and budgeted.** Approved in [agents.md](agents.md).
- **Everything about the proposal record.** [proposals.md](proposals.md).
- **The wording of what any part is told** — except in AG-WORDS-019, where what the words have to
  *mean* is exactly the rule.

## The cost this packet asks the owner to accept knowingly

A subject change inside one request. The owner interrupts "rename X" with "forget it, create Y". The
subagent resumes still carrying the refused rename and its old plan, while it does the creating.

This is accepted rather than fixed. It is bounded by the request: the owner's next message ends it
with an answer, and the work dies with it. The alternative — deciding whether words are a correction
or a new subject before handing them anywhere — is a judgment made without the conversation in front
of it, which is exactly the mistake `route` carrying only a name was designed to avoid.

---

## Scenarios

### AG-WORDS-016 — Words typed over a screen continue the request that opened it

Status: approved
Sources: MIGRATION.md §"Phase 7 notes" — "Typed words resume the Advisor's turn. They never start a
new one … they never reach a subagent first."
Changes today's behaviour: today the running request is cancelled and the words start a new one.

```gherkin
Given a subagent proposed a change and its screen is waiting in the chat
When the owner writes a message instead of pressing Save or Discard
Then the request that opened that screen carries on and answers those words
And it is not thrown away and started again from nothing
And the part answering in the chat is what reads them, before anything is handed anywhere
```

### AG-WORDS-017 — The resumed request is told the whole of what happened

Status: approved
Sources: MIGRATION.md §"Phase 7 notes" — "The Advisor's pending `route` call is answered with what
was proposed, what was rejected, what was already saved, and the owner's words."

```gherkin
Given a request that had handed the turn to a subagent and is waiting on its result
When the owner writes over the screen that subagent opened
Then the waiting request is given what was proposed, what was refused, what was already saved, and
  what the owner wrote
And it answers from those four things together, so a half-finished request is not reported as done
```

### AG-WORDS-018 — A correction reaches the subagent that wrote the refused proposal

Status: approved
Sources: `archived_docs/SUBAGENTS_PLAN.md` rule 9 and the "`route` restores rather than restarts"
decision; MIGRATION.md §"Phase 7 notes" — "An interruption does not finish it, so it stays and keeps
its own plan."

```gherkin
Given a subagent proposed a Diary entry and the owner refused it by writing "the same, but shorter"
When Safwa hands the turn back to that subagent
Then the same subagent picks the work up, still holding the entry it had written
And what it proposes next is that entry corrected, not a new one written from scratch
```

### AG-WORDS-019 — The subagent's own record says the owner refused and wrote instead

Status: approved
Sources: MIGRATION.md §"Phase 7 notes" — "Its text has to say the owner refused **and wrote
instead**. On 'rejected' alone the subagent reads its own transcript and proposes the same thing
again."

```gherkin
Given a subagent's proposal was refused by words the owner typed
When it picks the work up again
Then its own record of the work says both that the proposal was refused and that the owner wrote
  something instead of deciding
And it does not propose the same thing a second time
```

Note for the batch: the owner's words also reach the subagent through the conversation, but the
conversation it reads is a token budget and a long exchange can push them out. Its own record is what
makes this independent of that.

### AG-WORDS-020 — Unfinished work ends when the request that started it ends

Status: approved
Sources: MIGRATION.md §"Phase 7 notes" — "The turn that routed to it is the outer bound … it closes
its unfinished children by `parent_run_id`."
Changes today's behaviour: today unfinished work survives one further turn and is closed by a sweep.

```gherkin
Given a subagent was interrupted and its work was left unfinished
When the request that had handed it the turn finally answers the owner, or fails
Then that unfinished work is ended right there
And nothing from it can be picked up by a later request
```

### AG-WORDS-021 — Saving finishes the subagent, so asking it again starts it fresh

Status: approved
Sources: MIGRATION.md §"Phase 7 notes" — "Save finishes it: it plays out, hands back its receipt and
closes, so a second `route` to the same subagent in the same turn gets a fresh session."

```gherkin
Given the owner saved a change a subagent proposed, and that subagent finished and reported back
When the same request hands the turn to that same subagent again
Then it starts from nothing, with no memory of the change already saved
And it works from the conversation, where the saved change is already visible
```

---

## What was dropped after the first reading, and why

- **"What was already saved is still saved, and the screen is settled first."** Already approved,
  word for word, as PR-INTERRUPT-017 and PR-INTERRUPT-018. Repeating it here would be a second copy
  of a rule the owner has already ruled on.
- **"A correction is never handed to a subagent first."** The same rule as AG-WORDS-016, stated as a
  negative. Folded into it as a third `Then`.

## What this packet deletes

Named here so the review can check each one is gone rather than left running beside the new path.

| What | Where it was | What replaced it |
|---|---|---|
| The one-turn grace: any saved session of that kind, from any earlier turn | `_resume_suspended` — `kind` + `awaiting_approval` + unclaimed, newest first | `_resume_interrupted_child` — this run's **own** unfinished child of that kind |
| The sweep that closed work the next turn did not resume | `_close_lapsed_sessions`, called twice | `_close_unfinished_children(run_id)`, at the moment the turn ends |
| Cancelling the running request when a proposal is interrupted | `run.status = CANCELLED` for `kind == "advisor"` | the run keeps its turn and is resumed by the words |
| The walk that cancelled every caller above the interrupted subagent | the `while caller_id is not None` loop | nothing; the chain is intact throughout |
| The hint that a proposal may have just been corrected | a notice to the next turn | nothing; there is no next turn to warn |

**One correction to the packet as written.** The plan said `_resume_suspended` was deleted. What is
deleted is its *reach*: the lookup existed to find a kept session and still has to, but it is now
keyed on `parent_run_id` instead of on a window of one turn. Keying it on the caller is what makes
AG-WORDS-018 and AG-WORDS-021 both true at once — the same request finds its own unfinished child,
and a finished one is never found by anybody.

`recover_startup` keeps its own sweep. That one is for a process that died mid-request, which is a
different question and is approved as AG-SESSION-009.

## The audit table

| Scenario | Class | Decision | Tests |
|---|---|---|---|
| AG-WORDS-016 | `missing` | new | `test_subagent_e2e.py::test_words_over_a_screen_continue_the_request_that_opened_it` |
| AG-WORDS-017 | `missing` | new | `test_subagent_e2e.py::test_the_resumed_request_is_told_what_was_proposed_and_what_was_refused` |
| AG-WORDS-018 | `implementation_coupled` | the diary test kept its rule and was rewritten to the new flow; one new test beside it | `test_diary_e2e.py::test_a_correction_reaches_the_session_that_wrote_the_refused_day` ; `test_subagent_e2e.py::test_a_correction_reaches_the_session_that_wrote_the_refused_proposal` |
| AG-WORDS-019 | `missing` | new | `test_subagent_e2e.py::test_the_interrupted_session_reads_that_the_owner_wrote_instead` |
| AG-WORDS-020 | `implementation_coupled` | the grace-expiry test kept its rule under a new identifier; one new test beside it | `test_diary_e2e.py::test_a_refused_day_is_over_once_the_advisor_answers_something_else` ; `test_subagent_e2e.py::test_unfinished_work_ends_with_the_request_that_started_it` |
| AG-WORDS-021 | `missing` | new | `test_subagent_e2e.py::test_saving_finishes_the_subagent_and_the_next_route_starts_fresh` |

Two existing tests carried the old design and had to be decided rather than edited quietly:

| Test | Class | Decision |
|---|---|---|
| `test_diary_e2e.py::test_words_over_a_screen_end_the_caller_but_not_the_draft` | `contradictory` | deleted — it asserted the running request is `cancelled`, which is the defect AG-WORDS-016 fixes. Replaced by `test_words_over_a_screen_continue_the_request_that_opened_it`. |
| `test_advisor_flow_e2e.py::test_new_dialogue_cancels_every_unresolved_item_in_suspended_batch` | `business_valid` | kept, still citing PR-INTERRUPT-017. One assertion moved with the code: the session that wrote the refused proposal is `interrupted` rather than `awaiting_approval`, because no screen is open on it any more. |

`test_advisor_flow_e2e.py::test_a_session_can_only_be_claimed_once` stays uncited: the race it
guards against is not reachable, which is why AG-CLAIM-010 was dropped from packet one. It remains
as an invariant test, and whether the claim survives at all is a question for 7.b.

**The assumption the plan said to measure.** Both subagent prompts already carry the line 6.c added
— `board/agent.py:31` and `diary/agent.py:77`, "Write one line saying what you are about to do." A
scripted provider cannot measure whether a real model obeys it; what this batch can say is that the
mechanism no longer depends on it. An interrupted session now carries an explicit line in its own
record saying it was refused and what to do next (AG-WORDS-019), so a session that wrote nothing
into `content` still resumes knowing what happened.

## Gates

| | Before | After |
|---|---|---|
| `uv run pytest -q` | 767 passed / 3 skipped | 773 passed / 3 skipped |
| `uv run ruff check .` | clean | clean |
| `ai/service.py` lines | 2100 | 2193 |
| Modules over 600 lines (DoD #3) | 6 | 6 |
| Import cycles | 0 (651 edges) | 0 (651 edges) |
| Rule violations | 28 | 28 |
| `prompt_prefix.json` | must not move — 6.c already made the prompt change this depends on | unchanged |
| `schema.json` | must not move | unchanged |

`ai/service.py` grew by 93 lines: resuming an interrupted turn is a path that did not exist, and the
two mechanisms it replaced were smaller than it. 7.b is the batch that takes the file apart.
