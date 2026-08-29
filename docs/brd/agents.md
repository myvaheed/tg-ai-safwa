# Agents, packet one — the session, the hand-over, and what comes back

Status: **approved** by the owner on 2026-08-29, after one round of pruning.
Batch: Phase 7.a, together with [agents_interrupted.md](agents_interrupted.md) — both packets write
into the same `tests/brd/agents.feature`, because one `.feature` file is one package.
Sources: `CLAUDE.md` §"A session is the unit, and `route` hands one turn to another";
[docs/AGENT_ARCH.md](../AGENT_ARCH.md); `archived_docs/SUBAGENTS_PLAN.md` rules 1–19, read for intent
and checked against the code; current code — [ai/service.py](../../src/safwa/ai/service.py)
(`handle`, `_run_agent_loop`, `_execute_route_tool`, `_run_child`, `_provider_turn`),
[ai/subagents.py](../../src/safwa/ai/subagents.py), [cues/runtime.py](../../src/safwa/cues/runtime.py),
[telegram/dialogue.py](../../src/safwa/telegram/dialogue.py),
[recovery.py](../../src/safwa/recovery.py), and the E2E suite named in the audit table.

Fifteen scenarios, all of them describing what Safwa already does. This packet writes down rules
that have been enforced by code and by nothing else since they were built. Nothing here proposes a
change.

The change is packet two, [agents_interrupted.md](agents_interrupted.md), and it is deliberately
separate: this one is approved on evidence, that one on judgment, and reviewing them together would
put a behaviour change inside a characterization review.

Every number is written out with its constant named next to it, and the tests read the constant.

---

## What this packet is about, for a reader who has not seen this codebase

Safwa is a Telegram bot with one owner. The owner writes to it in ordinary words, and Safwa answers
in ordinary words. Behind that single conversation there is more than one model session, and this
packet is about how they are arranged.

Five words to fix first.

**The Advisor** is the part of Safwa the owner is talking to. It is the only part that writes into
the chat. It can read anything in the workspace and it can change nothing.

**A subagent** is the part that changes something. There is one per area of the product: `board`
covers everything on the owner's board — Cards, Checks, Values, Tags, Requests and Reminders — and
`diary` covers the Diary. Each has its own instructions and its own tools, and each can only touch
its own area.

**Routing** is the hand-over. When the owner asks for something Safwa cannot write itself, the
Advisor hands the turn to the subagent that owns it. The hand-over carries the subagent's **name**
and nothing else: no summary of the request, no restatement of what the owner wants. The subagent
reads the same conversation the Advisor read.

**A receipt** is what comes back. The subagent does its work, and hands the Advisor a short record:
what it did, what it wrote, or how it failed. The Advisor writes the one message the owner sees.

**A session** is one model's side of one piece of work, from its first step to whatever ends it. It
has its own instructions, its own tools, its own record of what it has said and done, and its own
budget. A session is not a single question and answer — it can be paused for hours while a screen
waits for the owner, and it picks up exactly where it stopped.

**A proposal** is how any change reaches the workspace. A subagent never writes anything directly:
it prepares the change, and the owner sees a screen with **Save** and **Discard** on it. That whole
mechanism is [proposals.md](proposals.md) and [proposals_interrupted.md](proposals_interrupted.md);
this packet only cares that a screen is what makes work pause.

## Scope

**In this packet:** who does the work, what the hand-over carries, what comes back, how long a
session lives, what happens to it when the process dies, when Safwa may speak without being asked,
and how a request that never produces words is stopped.

**Out, and why:**

- **What a proposal is, and what its screen does.** Approved in [proposals.md](proposals.md). Here a
  screen is only the thing that pauses a session.
- **What happens when the owner writes instead of deciding.** That is packet two,
  [agents_interrupted.md](agents_interrupted.md), and it changes today's behaviour. Approving it
  here would approve the defect it fixes.
- **The mechanics of holding the turn.** AG-TURN-010 and AG-TURN-015 fix the two rules the owner
  observes — one request at a time, and Safwa never opens a second conversation over an open one.
  How the text is held, when it is deleted from the chat, and how the queue is drained are the
  Telegram side of it, and belong to Phase 8's packet.
- **The heavy analyzer.** Asking a read-only helper a question is
  [heavy_analyzer.feature](../../tests/brd/heavy_analyzer.feature), already approved.
- **Which views each part may read.** A reader's view list is part of its prompt, and each feature's
  packet owns the views it publishes.
- **The wording of anything Safwa says**, and which model or provider is used.

---

## Scenarios

### AG-ROUTE-001 — A change is written by the part that owns it, and Safwa itself writes none

Status: approved
Sources: `SAFWA_TOOLS = (QUERY_SAFWA_TOOL, OPEN_TOOL)`; `_tools_for` gives a subagent its own area's
mutation tools; `CLAUDE.md` §"Every mutation tool belongs to a subagent, never to the Advisor"

```gherkin
Given the owner asks Safwa to change something on their board
When Safwa works on it
Then the change is prepared by the subagent that owns that area
And the part answering in the chat has no way to change anything itself, in any area, so a change
  is always someone else's work
When the owner instead asks a question about anything in their workspace, the Diary included
Then that same part answers it from its own reading, without handing the turn to anyone
```

### AG-ROUTE-002 — What the subagent is given to read is the conversation itself

Status: approved
Sources: `RouteInput` carries a name and nothing else; `_routed_context`; `conversation_block`;
`SUBAGENT_HISTORY_LAST_MESSAGES = 10`

```gherkin
Given the owner wrote a request in their own words
When the turn is handed to a subagent
Then the subagent is given the name of its area and no retelling of the request
And it reads the same recent conversation, so the owner's exact words are what it works from
```

```gherkin
Given the conversation the subagent is handed
When it reads it
Then the newest 10 messages arrive as one block, each tagged with who wrote it
  (SUBAGENT_HISTORY_LAST_MESSAGES = 10)
And none of them arrives as if the subagent had said it
```

### AG-ROUTE-003 — A request that names two areas is one request, not two

Status: approved
Sources: `_run_agent_loop` continues after a receipt; `CLAUDE.md` §"a request naming two domains is
two routes and one message"

```gherkin
Given the owner asks for one thing on their board and one thing in their Diary, in a single message
When Safwa works on it
Then each area is handed to the subagent that owns it, one after the other, within the same request
And the request is not finished until both have been dealt with
```

### AG-ROUTE-004 — The part doing the work cannot hand the work on again

Status: approved
Sources: `_tools_for` gives a subagent only its own read and mutation tools; `ROUTE_TOOL` is added
to the Advisor's tools alone

```gherkin
Given a subagent is doing the work it was handed
When it looks for a way to hand that work on to another subagent
Then it has none, so the chain is never more than two deep
```

### AG-ROUTE-005 — Handing the turn over is the only thing Safwa does in that step

Status: approved
Sources: `_run_agent_loop` — the `route_is_not_shared` branch

```gherkin
Given Safwa decides to hand the turn to a subagent
When it tries to do something else in the same step
Then nothing in that step runs, Safwa is told to hand over on its own, and it tries again
```

### AG-RECEIPT-006 — Only Safwa writes to the chat

Status: approved
Sources: `_answer`; `_route_receipt`; `_deliver_to_parent`; `render_citations`

```gherkin
Given a subagent finished the work it was handed and has something to say about it
When the request ends
Then its words went back to the part answering in the chat, never into the chat themselves
And that part writes the single message the owner reads, keeping the links to any item named
```

### AG-RECEIPT-007 — Work that breaks comes back as a report, not as a silence

Status: approved
Sources: `_run_child` — the `TimeoutError` and `Exception` branches both return a receipt carrying
`error`; `failure_reason`

```gherkin
Given a subagent fails part-way through the work it was handed
When the failure happens
Then what comes back is a short account of the failure, in place of a result
And Safwa still answers the owner in the same request, telling them what did not happen
```

### AG-SESSION-008 — Work paused on a screen picks up where it stopped

Status: approved
Sources: `AgentSession.state` and `AgentSession.restore`; `_suspend_for_child`; `agent_runs`

```gherkin
Given a subagent prepared a change and its screen is waiting in the chat
When the owner saves it, minutes or hours later
Then the subagent carries on from the step it stopped at, with everything it had already worked out
And its result then reaches the part that handed it the work, which answers the owner
```

```gherkin
Given the subagent stopped for the owner
When it starts again
Then what it knows about the board and the time of day is read fresh, not replayed from when it
  stopped
```

### AG-SESSION-009 — A restart ends every piece of work that was waiting

Status: approved
Sources: `recover_startup` — `running` becomes `interrupted`, `awaiting_approval` becomes
`abandoned`, and the claim is released

```gherkin
Given work was paused on a screen, or was running when the process stopped
When Safwa starts again
Then none of it is picked up, and the owner starts from a request they make now
And no button left in the chat can revive it
```

### AG-TURN-010 — One request at a time, and words that arrive during one join it

Status: approved
Sources: `GenerationGuard.acquire(queue_messages=True)` in `dialogue.py`; `queue_owner_text`;
`materialize_queued_dialogue`; `command_cancel`

```gherkin
Given Safwa is working on a request
When the owner writes another message
Then it does not start a second request
And it is held, and taken up as part of the same request once the running one finishes
When the owner presses a button on any screen instead
Then it does nothing while the request is running
When the owner runs /cancel
Then the running request is stopped, and that is the one thing they can always do
```

### AG-BUDGET-011 — One request has one tool budget, however many screens it opens

Status: approved
Sources: `AgentSession.tool_count` in `state_json`, restored by `AgentSession.restore`;
`_run_agent_loop` raises past the limit

```gherkin
Given a request that opens several screens before it is finished
When the owner answers each of them and the work carries on
Then every step it has taken counts against one budget of 64 (MAX_TOOL_CALLS = 64)
And the budget is not refilled by a pause, so a request that never settles is stopped
```

### AG-BUDGET-012 — A subagent that takes too long is stopped by the clock

Status: approved
Sources: `_run_child` — `asyncio.wait_for(..., SUBAGENT_DEADLINE_SECONDS)` wraps the loop only, so
time spent waiting on a screen is outside it

```gherkin
Given a subagent is working while the owner waits for an answer
When it has been working for 300 seconds without finishing (SUBAGENT_DEADLINE_SECONDS = 300)
Then it is stopped, and the owner is told it did not finish
When instead a screen it opened is waiting for the owner
Then the time the owner takes to decide does not count against that, however long they take
```

### AG-ANSWER-013 — A step that produces nothing is asked again, and the owner is never left with nothing

Status: approved
Sources: `_run_agent_loop` — the empty-content branch and `MAX_REPAIR_ROUNDS`; `_answer` composes
the fallback line

```gherkin
Given a step ends with neither an answer nor anything to do
When that happens
Then Safwa is told it stopped without answering, and asked again, up to 5 times
  (MAX_REPAIR_ROUNDS = 5)
And if it still has nothing, the owner is told so in one line rather than left with silence
```

### AG-ANSWER-014 — A subagent's first step is always the work

Status: approved
Sources: `_provider_turn` — `tool_choice="required"` while `tool_count == 0` and the session is a
routed subagent; `Settings.ai_tool_choice_required`

```gherkin
Given a subagent has just been handed a turn
When it takes its first step
Then it has to do something, not answer in words — it was handed the turn for the work
And every step after that is free to be the answer, or the work would never finish
```

### AG-TURN-015 — Safwa speaks unasked only when nothing of the owner's is open

Status: approved
Sources: `CueRuntime.can_speak` — refuses while `guard.active`, while any review is open, and while
any session is claimed; `CueRuntime.still_current` compares `dialogue_revision` around the turn

```gherkin
Given the system has something for Safwa to say without being asked, such as a Reminder coming due
When a request of the owner's is running, or a screen of theirs is waiting for a decision
Then Safwa says nothing, and what it owes them is still owed
When nothing of the owner's is open
Then it is said as one ordinary answer, and only then is it no longer owed
When the owner writes while Safwa is part-way through saying it
Then the owner wins, the half-written message is thrown away, and it is still owed
```

---

## What was dropped after the first reading, and why

- **"The same paused work is never picked up twice."** Written from the claim in the code. There is
  no reachable way for two answers to one screen to arrive at once: a button is a single-use record,
  and a button press is refused outright while a request is running. So the claim is a guard against
  a race nothing can cause, not a rule the owner can observe. It belongs to a technical batch as an
  invariant test, and Phase 7 has a question open about whether it survives at all —
  see [MIGRATION.md](../MIGRATION.md) §"Before starting Phase 7".
- **"Safwa reads everything and changes nothing by itself."** The same rule as AG-ROUTE-001 read
  from the other side. Folded into it as a second `When`.

## The audit table

Eleven of the fifteen were already covered by tests that stood without a scenario to point at; they
were cited where they were. Four had nothing.

| Scenario | Class | Decision | Tests |
|---|---|---|---|
| AG-ROUTE-001 | `business_valid` | cited | `test_subagent_e2e.py::test_a_routed_subagent_proposes_for_itself` ; `test_subagents.py::test_the_diary_is_written_only_by_its_subagent` |
| AG-ROUTE-002 | `business_valid` | cited | `test_subagent_e2e.py::test_a_subagent_reads_the_tail_of_the_conversation_as_tagged_data` |
| AG-ROUTE-003 | `business_valid` | cited; its prose docstring replaced by the identifier | `test_subagent_e2e.py::test_two_domains_in_one_request_are_both_finished` ; `test_subagent_e2e.py::test_the_second_subagent_reads_what_the_first_one_saved` |
| AG-ROUTE-004 | `business_valid` | re-cited from `PR-WRITE-002`, which keeps `test_the_board_owns_every_mutation_tool` | `test_subagent_e2e.py::test_a_routed_subagent_is_offered_only_its_own_tools` |
| AG-ROUTE-005 | `missing` | new | `test_subagent_e2e.py::test_route_cannot_share_its_response_with_another_call` |
| AG-RECEIPT-006 | `business_valid` | cited | `test_subagent_e2e.py::test_a_routed_subagent_hands_its_words_back_and_the_advisor_speaks` ; `test_subagent_e2e.py::test_an_autoapproved_board_route_hands_back_its_receipt` |
| AG-RECEIPT-007 | `business_valid` | cited | `test_subagent_e2e.py::test_a_failed_subagent_comes_back_as_an_error_the_advisor_reports` |
| AG-SESSION-008 | `business_valid` | cited | `test_advisor_flow_e2e.py::test_suspended_batch_persists_the_request_dialogue_and_transcript` |
| AG-SESSION-009 | `business_valid` | cited | `test_infrastructure.py::test_startup_releases_the_claim_of_an_interrupted_session` ; `test_infrastructure.py::test_startup_closes_every_session_waiting_on_a_process_local_screen` |
| AG-TURN-010 | `business_valid` | cited | `test_telegram_item_ui.py::test_messages_are_queued_with_placeholders_and_restored_as_one_turn` |
| AG-BUDGET-011 | `business_valid` | cited | `test_advisor_flow_e2e.py::test_the_tool_call_budget_is_carried_across_an_approval` |
| AG-BUDGET-012 | `missing` | new | `test_subagent_e2e.py::test_a_subagent_that_runs_too_long_is_stopped_by_the_clock` |
| AG-ANSWER-013 | `business_valid` | cited | `test_advisor_flow_e2e.py::test_a_turn_that_stops_without_words_is_asked_again` ; `test_advisor_flow_e2e.py::test_a_turn_that_never_finds_words_still_reaches_the_owner` |
| AG-ANSWER-014 | `business_valid` | cited | `test_subagent_e2e.py::test_a_subagent_is_required_to_open_with_a_tool_call` |
| AG-TURN-015 | `missing` | two new, over the real guard and the real store | `test_cues.py::test_ag_turn_015_the_gate_is_shut_while_anything_of_the_owners_is_open` ; `test_cues.py::test_ag_turn_015_an_open_gate_takes_the_background_lease` |

The Cue gate was reached only through a stub before this batch: the six `PL-END-015` tests drive a
`Recorder` whose gate is a boolean, so no test asked `CueRuntime.can_speak` its three questions.
Those six are untouched — a Sprint's end is their rule — and AG-TURN-015 is what covers the gate.

## Gates

| | Before | After |
|---|---|---|
| `uv run pytest -q` | 767 passed / 3 skipped | 773 passed / 3 skipped |
| `uv run ruff check .` | clean | clean |
| `prompt_prefix.json` | must not move | unchanged |
| `schema.json` | must not move | unchanged |

The gates for the whole of 7.a, including packet two's behaviour change, are in
[agents_interrupted.md](agents_interrupted.md).
