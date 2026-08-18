# Safwa — Diary (plan)

**Implemented.** This file stays the contract: a change to the behaviour is a change to this file
first. It carries what is specific to the Diary; the session mechanics it runs on —
`route`, resuming, suspension — are in [SUBAGENTS_PLAN.md](SUBAGENTS_PLAN.md). Read
[ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together.

## Rules

These are the contract. Everything below follows from them.

1. A Diary entry is **one day's text in the owner's own voice** (`pov`), plus a `feeling_score` and
   Safwa's one line about it (`ai_comment`) on the screen. One entry per local calendar date. The day
   is usually today, but the owner may ask for any day — "add this to yesterday" — so the date is
   something to work out, never something to assume.
2. The entry is written by the **Diary subagent** and by no one else. The Advisor has neither
   `ai_diary` nor the `diary` tool, so it cannot read a day or write one; it routes.
3. **The subagent is the only reader of the Diary.** Every Diary request — including "what did I
   write on Wednesday" — is a `route("diary")`. A read-only request is answered in prose, citing each
   day as `[dd.mm.yyyy](diary:<id>)`.
4. A write always carries the whole day as it stands now, taking the already saved entry into
   account. Overwriting is the normal path, not an exception.
5. `feeling_score` is 0–10 and may be absent. 5 is an ordinary day and the rest of the ladder is read
   against it. **0 is the owner's word alone** — the subagent never chooses it. The scale lives in
   `FEELING_SCORE_EMOJI` and the rubric in `DIARY_PROMPT`, once each.
6. Saving goes through the ordinary proposal path: `diary` → `ChangeProposal` → a read-only
   Save/Discard screen → `ProposalService.apply` → the same `domain.py` function a manual path would
   call. Every proposal screen stays exactly Save/Discard.
7. The proposal outcome is visible in the conversation, **but the day's text is not**: the receipt
   carries the date, the score and a character count. A receipt stays in the chat and would be
   re-read on every later turn, while the draft is already held by the session that wrote it.
8. Only the newest interactive screen is live. Any command, any menu navigation, and any new dialogue
   text dismisses the ones above it.

## Decisions taken, with the alternative that was rejected

- **`mode="update"` for a day whether or not it exists.** Whether that day is already written is a
  fact about the data, not something the model should restate: preparation reads `ai_diary` and
  settles the action on create or update. A `delete` of a day with nothing saved is refused there
  too, retryably. The tool carries no third verb — every `mode` in Safwa is an existing action.
- **`pov` and `ai_comment` are two fields, not one text.** The day is the owner's voice and the
  comment is Safwa's; one field would make the subagent choose whose voice the screen is in, and the
  Advisor used to be asked to re-word the comment "as your own words" — a paraphrase with no purpose.
- **`ai_comment` is screen-only.** The entry keeps the owner's voice alone, so nothing else is
  stored beside it.
- **The day never travels through a tool result.** A long entry printed into a receipt is spent from
  the history budget on every later turn. The session that wrote it holds it, so it does not have to.
- **No special staleness rule for the Diary proposal.** Any command or navigation dismisses the open
  screen, so the window in which `workspace.revision` could drift under a live Diary proposal is
  closed. The existing `StaleStateError` check stays untouched — no `if` for one entity.
- **A stale system Reminder is skipped, not caught up.** The bot is expected to run continuously. If
  it was down past `REMINDER_CATCHUP_GRACE_MINUTES`, that day gets no entry and the schedule rolls
  forward, exactly like any other repeat.
- **The nightly ask is an ordinary Reminder marked `system`.** Settings is its single source of
  truth: the Diary time moves it, `off` deletes it, and the Diary instruction is appended to its
  text. A Reminder the model cannot see in `ai_reminders` is a Reminder whose id it cannot name, so
  the mutation tools need no special case.

## Deferred

- A `/diary` browsing screen. A day is reachable only through a citation the subagent wrote.

## Documentation kept in step

[ARCHITECTURE.md](ARCHITECTURE.md) (runtime wiring, feature map, table count, command count),
[STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md), [SUBAGENTS_PLAN.md](SUBAGENTS_PLAN.md),
[MEMORY_HISTORY_USAGE.md](MEMORY_HISTORY_USAGE.md) and [CLAUDE.md](../CLAUDE.md).
