# The helper the Advisor calls — approval packet

Status: **approved** 2026-08-25.
Batch: Phase 5.f
Sources: the owner's rulings of 2026-08-25 — nothing is added to the Advisor's prompt for this, the
helper appears only when a trigger fires, and it forwards a result rather than retelling it;
`CLAUDE.md` §"A session is the unit, and `route` hands one turn to another", §"Read-only SQL is
triple-guarded"; `ai/mini.py` (`run_mini_session`, `TerminalTool`, `ReadToolSpec`), `ai/sql.py`
(`SqlView`, `QueryOutcome.notice`), `ai/service.py` (`AgentSession`, `_execute_query_tool`),
`bootstrap/modules.py` (`_routing_rules`).

Twelve scenarios. One new package, `src/safwa/features/heavy_analyzer`. No view changes shape, and
the only model change is one dead enum member.

## Why the batch exists

Phase 5.e gave the data the shape a hard question needs: a series names itself, a Check names its
Card's series, and an archived row is still counted. What it did not change is who writes the query.

The Advisor is a 4B–12B model. It reads well and it joins badly. Its prompt already carries the two
rules that exist because of this — total effort needs `WHERE kind = 'action'`, the Checks on a Card
are a second query — and each of them is a rule about one query the model got wrong. There is no
room for a third: the prompt is what the owner refused to grow.

So the correction moves out of the prompt and into the moment it is needed. A read that joins,
groups, nests a query or opens with `WITH` is a read this model gets wrong. The result of that read
says so, and offers a helper that is good at exactly that.

## What a helper is

A helper is **not** a routed subagent, and none of the routing machinery is touched.

`route` hands the turn to a subagent that writes: it proposes, the screen suspends the whole chain,
and what comes back is a receipt of what it did. That is a session with its own row, its own parent,
its own resume.

A helper reads and hands back **rows**. It has one job, a step budget, and exactly two ways to end.
That is the shape `ai/mini.py` already runs for the Reminder scheduler and the autoapproval
reviewer: read tools, a set of terminal tools of which exactly one must be called, and prose is
never an answer.

`heavy_analyzer` is the first helper. It holds `query_safwa` and two terminals, and it never speaks.

## The helper never says anything in its own words

`forward_output()` takes no arguments. The last read the helper made **is** the answer, and it
travels to the Advisor as rows.

This is what makes the helper worth having. A result of fifty rows cannot be retold — a retelling
of numbers by a small model is where the numbers get invented. The helper's job is to write the
query the Advisor could not, and its answer is that query's result, untouched.

`report_failure(explanation)` is the other ending: it could not work the question out. One sentence,
and the Advisor answers the owner with what it has.

Everything a routed subagent needs and a helper does not therefore goes away: no persona, no
citation rules, no rule about the owner's language, no receipt, no `parent_run_id`, no suspension.

## The Advisor waits

`call_helper` is awaited inside the Advisor's own loop, exactly like `route`. Nothing runs beside
it: `GenerationGuard` already holds the whole generation, callbacks are rejected and owner text is
queued for the duration. No approval can arrive while the helper runs, because no screen exists to
approve. `/cancel` kills the whole request, helper included.

## What changes

**`call_helper` is not in the base tool set.** It is added to the session's tools by the tool result
that needed it, and it stays for the rest of that session. An Advisor that never ran a complex read
never sees the tool and never reads a word about it. `SYSTEM_PROMPT` does not change by one byte.

**The offer travels on the notice.** `QueryOutcome.notice` already exists to tell the model its
result was cut. One more producer writes to it, so the offer costs nothing in the prompt and arrives
attached to the read that earned it.

**The helper is given the Advisor's question.** A routed subagent works out from the conversation
what the owner wants. A helper is asked something the Advisor derived, which the conversation does
not say. Its context is the same `<Conversation>` block a routed subagent gets, and the question
straight after it as `<Request from AI>…</Request from AI>`.

**Each view documents itself.** See D5.

## Decisions

**D1. The trigger is anything past one flat scan.** `JOIN`, `GROUP BY`, `HAVING`, `UNION`,
`INTERSECT`, `EXCEPT`, `WITH`, a window function, or a query inside a query. A bare aggregate over
one view with a `WHERE` is not a trigger: `SELECT count(*) FROM ai_cards WHERE stage = 'today'` is a
simple lookup, and firing on it would offer the helper on most turns. Counting is hard when it is
grouped or joined, and both of those are caught.

**D2. A failed query does not offer the helper.** Its `hint` already tells the model to repair that
one SELECT, and this model follows the last instruction it read. A **capped** result does offer it:
capped means the question was too broad, which is the helper's job.

**D3. The helper has a step budget, not a first-failure rule.** `HEAVY_ANALYZER_MAX_TOOL_CALLS = 10`
reads, then `run_mini_session` gives up and the Advisor is told so. A broken SELECT inside those ten
is the helper's to repair, the same way the Advisor repairs its own.

**D4. A session remembers that it was offered the helper.** `AgentSession.state()` does not persist
`tools`; a resumed session rebuilds them from its kind, which is the base set. The helper itself
never suspends, but the Advisor can suspend after being offered one — it routes a change, the screen
opens, the owner saves — and would come back to a tool that had vanished. One field fixes it.

**D5. Each view carries its own documentation, in the feature that owns it.** The view catalogue
exists twice today, in the Advisor's prompt and the board's, and the two have already drifted: the
Advisor's `ai_checks` block carries a line about counting across a series that the board's does not.
The helper needs it too, and reading is its whole job.

One shared constant is the wrong fix — the board's catalogue leaves out `ai_diary` and
`ai_current_sprint_metrics` on purpose, because the view list in a prompt is what scopes a reader.
So `SqlView` gains its documentation next to its SQL, an agent names the views it reads, and the
catalogue is composed at import time the way the routing rules already are.

This is mechanical but it is not small: eight `views.py` files, both prompts, and the Rule I
snapshot. It is in this batch because the alternative is writing the catalogue a third time and
deleting it in the next one.

**D6. The event log goes to the helper, and it keeps `actor`.** `ai_card_events` is documented only
in the Diary's prompt today, where it says what work a day held. It is a stream of one row per
change over time — the shape a question about a stretch of time uses, which is the helper's whole
subject — so the helper is told about it too, and the Advisor and the board still are not. That
split is the first thing per-view scoping buys.

`actor` stays. It names the source of a change, not a person: `user_ui` is the owner on a screen and
`ai` is an approved proposal, so it is what answers "how much did I close myself, and how much on
Safwa's suggestion". A single owner is exactly the case where that distinction is the only one left.

`ActorType.SYSTEM` goes. It appears once in the whole codebase, as a column default that cannot
fire because `record_card_event` always passes an actor, so no row can ever hold it.

## Scenarios

### HAN-OFFER-001 — A complex read offers the helper

Status: proposed
Sources: the owner's ruling of 2026-08-25

```gherkin
Given the Advisor is answering the owner
When it runs a read that joins views, groups rows, nests a query, or opens with WITH
Then the result carries a notice naming call_helper
And call_helper is on its tool list for the rest of the session
```

### HAN-OFFER-002 — A simple read offers nothing

Status: proposed
Sources: D1

```gherkin
Given the Advisor is answering the owner
When it runs a read over one view that only filters and counts
Then the result carries no notice about a helper
And call_helper is not on its tool list
```

### HAN-OFFER-003 — A result that was cut offers the helper

Status: proposed
Sources: D2

```gherkin
Given a read matches more rows than its budget holds (DEFAULT_ROW_LIMIT = 50)
When the Advisor runs it
Then the notice that says the result was cut also names call_helper
```

### HAN-OFFER-004 — A read that failed does not offer the helper

Status: proposed
Sources: D2

```gherkin
Given the Advisor runs a read the database refuses
When it reads the error
Then it is told to fix that one SELECT
And nothing in the error names call_helper
```

### HAN-OFFER-005 — The offer outlives a screen the Advisor opened

Status: proposed
Sources: D4

```gherkin
Given the Advisor was offered the helper
And it then routed a change that opened a screen
When the owner saves it and the Advisor's session resumes
Then call_helper is still on its tool list
```

### HAN-ASK-006 — The helper is given the question and the conversation

Status: proposed
Sources: the owner's ruling of 2026-08-25

```gherkin
Given the Advisor calls call_helper with a question of its own
When the helper starts
Then it is given the same conversation a routed subagent is given
And the Advisor's question straight after it, as its own block
```

### HAN-ASK-007 — The helper forwards a result and never retells it

Status: proposed
Sources: the owner's ruling of 2026-08-25

```gherkin
Given the helper has read what answers the question
When it ends the session
Then the Advisor is given those rows and the query that produced them
And nothing the helper wrote in words reaches the Advisor
```

### HAN-ASK-008 — Nothing happens while the helper runs

Status: proposed
Sources: the owner's ruling of 2026-08-25

```gherkin
Given the Advisor has called the helper
Then the owner's turn is still running and no screen is shown
And owner text that arrives is queued, not answered
When the owner cancels the generation
Then the helper stops with the request it belongs to
```

### HAN-ASK-009 — The helper changes nothing

Status: proposed
Sources: CLAUDE.md §"AI mutations are always proposals"

```gherkin
Given the owner's question needs a change as well as an answer
When the helper is asked
Then it holds no tool that changes anything
And the change is still the Advisor's to route
```

### HAN-ASK-010 — A helper is called, never routed

Status: proposed
Sources: the owner's ruling of 2026-08-25 — nothing is added to the Advisor's prompt

```gherkin
Given the Advisor's routing rules
Then they name no helper
When the Advisor routes to a helper by name
Then it is refused and told which subagents it may route to
```

### HAN-ASK-011 — A helper that gets nowhere does not cost the turn

Status: proposed
Sources: D3

```gherkin
Given the helper spends its whole budget without an answer (HEAVY_ANALYZER_MAX_TOOL_CALLS = 10)
When the Advisor reads the result
Then it carries one sentence saying the helper could not work it out
And the Advisor still answers the owner
```

### HAN-READ-012 — The event log is read by the helper and by nobody else

Status: proposed
Sources: D6

```gherkin
Given the owner asks how much of a stretch of work they closed themselves
When the helper reads the log of changes to Cards
Then each change says whether the owner made it on a screen or an approved proposal did
And neither the Advisor nor the board is told that log exists
```

## Audit of existing tests

Nothing is superseded. `route`, `_run_child`, `_routed_context` and `RoutedSubagent` are not
touched; `run_mini_session` gains a caller and no behaviour.

| Existing test | Verdict |
|---|---|
| `tests/test_agent_routing.py` (route, receipts, suspension) | keep, unchanged |
| `tests/test_ai_query.py` (caps, notices, the authorizer) | keep; new cases for the offer |
| `tests/test_architecture.py` Rule I (`prompt_prefix.json`) | hashes the **composed** prompts after D5, so the snapshot moves once, deliberately |

## Gates

- `SYSTEM_PROMPT` carries no helper: asserted directly, not through the snapshot.
- `prompt_prefix.json` moves once for D5 and the diff is reviewed line by line.
- `schema.json` stays byte-identical: Rule J hashes a column's name, type, nullability and key, and
  dropping `ActorType.SYSTEM` changes only a default that never fired.
- `uv run pytest -q` green, `uv run ruff check .` clean.
- `scripts/architecture_metrics.py`: no new cycle, no new Rule G or Rule H violation.
- `tests/test_brd_traceability.py` green with the new `HAN` prefix registered in
  `docs/brd/README.md`.

## Left alone, and named

**Automatic archiving writes no event.** The owner's Archive button records one; the sweep that
archives what has waited two Sprints records nothing. So the log carries `archive` only for the
manual half, and a reader that trusts it concludes archiving is always something the owner did.
Older than this batch, and adding the event changes what the log contains — its own change.

**A Diary field is named like a view.** `ai_comment` is a field of the `diary` tool, and it matches
the `ai_*` shape every view name has. Nothing breaks — the model never queries it — but a check that
reads view names out of a prompt cannot tell the two apart, so the Diary's prompt stays out of that
check. Renaming the field is a schema batch.

**`call_helper` takes a name.** With one helper the argument is a constant the model still has to
spell correctly every time. It stays because the owner asked for the two-argument shape, and because
dropping it later is a smaller change than adding it back.
