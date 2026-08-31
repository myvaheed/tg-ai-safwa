# Diary, packet two — reading one named day

Status: **approved 2026-08-30**, written into `tests/brd/diary.feature` in 8.a step 2
Batch: Phase 8.a. Writes into `tests/brd/diary.feature`, alongside [diary.md](diary.md), because
one `.feature` file is one package. Numbering continues from `DI-READ-015`.
Sources: current code — [features/diary/agent.py](../../src/safwa/features/diary/agent.py)
(`read_day`, `DayReader`), [history.py](../../src/safwa/history.py) (`day_transcript`, and
`recent`'s `stop_at_summary`).
Supersedes: none.

## Why this packet exists, and why it is here rather than in the history packet

It was first drafted as the second half of `TG-SUMMARY-006`, which says the newest Summary is where
the conversation window ends. The owner ruled on 2026-08-30 that it belongs to the Diary instead:
reading a named day is something the Diary does, not something the chat does, and writing it beside
the window rule made it look like a general capability. It is not one — the Advisor cannot reach a
day at all, which is recorded in [docs/FUTURE_FEATURE.md](../FUTURE_FEATURE.md).

## What this packet does not cover

| Rule | Where it already lives |
|---|---|
| Safwa reads a day back itself and points the owner at it | `DI-READ-006` |
| A day nobody talked about reads as empty, not as a failure | `DI-READ-015` |
| Neither the conversation nor the data can write a day alone | `DI-READ-013` |
| Today is the owner's today | `DI-DATE-012` |
| The Summary is where the conversation window ends | `TG-SUMMARY-006` |

## Scenario

### DI-READ-016 — Reading one named day reads the whole day

Status: draft
Sources: `features/diary/agent.py` `read_day`; `history.py` `day_transcript`, which asks for the
period with Summaries skipped rather than treated as an edge
Supersedes: none; `tests/test_diary.py` drives `read_day` through a fake day reader, so nothing
today asserts what a real one returns

The Diary is writing down what was actually said. A Summary is a compression of it, so for this one
reader a Summary is skipped rather than read, and the day is read from end to end however many
Summaries fall inside it. The day's two ends are worked out in code from the date and the owner's
timezone; the model supplies the date and nothing else.

```gherkin
  Scenario: DI-READ-016 — Reading one named day reads the whole day
    Given the Diary is asked to read one day, named by its date
    When a Summary was written in the middle of that day
    Then the whole day is read as it was spoken, and the Summary is not read at all
    And the day runs from midnight to midnight in the owner's timezone
```

## Audit table

| Scenario | Existing tests | Class | Decision | New tests | Status |
|---|---|---|---|---|---|
| DI-READ-016 | `tests/test_diary.py` — the `read_day` tests, which use a fake day reader | characterization_valid | cite, and add one against the real reader | one adapter test over a chat with a Summary mid-day | draft |
