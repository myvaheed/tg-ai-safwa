# Safwa — Diary (plan)

**Not implemented.** This file is the contract to build against: the rules below are what the code
should be written to, and a change to the behaviour is a change to this file first.
Read [ARCHITECTURE.md](ARCHITECTURE.md) for how the existing pieces fit together.

The feature needs four things Safwa does not have yet — a subagent runner, a history window bounded
by tokens instead of a session marker, a Diary entity, and a hidden system Reminder. They are staged
so each phase leaves the bot working on its own.

## Rules

These are the contract. Everything below follows from them.

1. A Diary entry is **one day's text in the owner's own voice**, plus the advisor's remark on the
   screen. One entry per local calendar date. The day is usually today, but the owner may ask for any
   day — "add this to yesterday" — so the date is something to work out, never something to assume.
2. The entry text is written by the **Diary subagent**, never by the advisor. The advisor decides
   *when* to propose and *what to ask*, and it never authors or edits the body.
3. The subagent **reads and reports; it never mutates**. It has no mutation tool at all. It does
   decide the whole change: which day, which entry, and whether that day is written or removed. The
   advisor carries the owner's words in and the stamp out, and nothing else.
4. A subagent report always carries the whole day as it stands now, taking the already saved entry
   into account. Overwriting is the normal path, not an exception.
5. Saving goes through the ordinary proposal path: `propose_diary_update` → `ChangeProposal` → a
   read-only Save/Discard screen → `ProposalService.apply` → the same `domain.py` function a manual
   path would call. Every proposal screen stays exactly Save/Discard.
6. A **stamp** proves a subagent read happened and carries the change it decided on. It is issued
   only together with a change, and it dies at the end of the local day it was *issued* on — not the
   day it describes, or a back-dated entry would be born expired. A discarded proposal can be
   re-offered from it without another subagent run; Save spends it, and with it every other stamp for
   that date, since a settled day makes every draft of that day out of date.
7. The proposal outcome is visible in the conversation. Both Save and Discard post a
   `DIALOGUE_ASSISTANT` receipt, so the model reads back what was saved or refused — including the
   entry text itself.
8. Only the newest interactive screen is live. Any command, any menu navigation, and any new
   dialogue text dismisses the ones above it.
9. `call_subagent` is **synchronous**. The advisor blocks on it and receives the report as a tool
   result in the same turn.

## Decisions taken, with the alternative that was rejected

- **Synchronous subagents.** An async `call_subagent` needs a persisted result queue, a reconcile
  path in `recover_startup`, and a second provider call racing the foreground one against a single
  local model. A finished async subagent whose escalation is refused (`reserve_background` → False)
  has no natural retry the way an unfired Reminder does, so its work is simply lost. Synchronous
  costs one blocked turn and buys none of that. Async is deferred until a second subagent needs it.
- **No `available_subagents()` tool.** The roster is static text in `SYSTEM_PROMPT`, which lives in
  the cacheable prefix. A discovery tool costs a round trip per turn and would hand the advisor the
  subagent's own system prompt, which it neither executes nor should imitate.
- **The whole change lives host-side under the stamp.** `propose_diary_update` takes the stamp and
  nothing else; the date, the target entry, the action, the body and the remark are all read from the
  stamp record. Passing a long body through the advisor costs tokens and invites silent edits, which
  rule 2 forbids — and a date or an id the advisor could restate is a date or an id it could get
  wrong, which rule 3 forbids for the same reason.
- **No special staleness rule for the Diary proposal.** Phase 2 makes any command or navigation
  dismiss the open screen, so the window in which `workspace.revision` could drift under a live
  Diary proposal is closed. The existing `StaleStateError` check stays untouched — no `if` for one
  entity.
- **A stale system Reminder is skipped, not caught up.** The bot is expected to run continuously. If
  it was down past `REMINDER_CATCHUP_GRACE_MINUTES`, that day gets no entry and the schedule rolls
  forward, exactly like any other repeat. The entry date is therefore always the current local day,
  and no "which day is this for" rule is needed.
- **The advisor stays in the edit loop.** A comment could go straight from the host to the subagent,
  saving two advisor turns, but that shortcut only holds while every subagent is synchronous. The
  long chain survives a later move to async.

## Phase 1 — history bounded by tokens · done

Sessions go away and the history window becomes a token budget. Everything later reads history, so
this lands first and alone.

- Delete `/newsession`, `/endsession`, `compress_subsession`, `active_session_start`,
  `HistoryBoundaryMissing`, the `require_boundary` parameter, `MessageKind.SESSION_START`,
  `MessageKind.SUBSESSION_RESULT`, the `subsession_confirm`/`subsession_cancel` callbacks, the
  middleware's `/newsession` exception, and `MemoryMaintenanceResult.BOUNDARY_MISSING`.
  Mark codes **1 and 2 are retired, never reused** — `_KIND_MARK_CODES` is append-only.
- The window in [constants.py](../src/safwa/constants.py) is a Summary of at most
  `SUMMARY_TOKEN_CEILING = 2 000` plus `SUMMARY_TRIGGER_TOKENS = 8 000` of messages, and
  `HISTORY_TOKEN_BUDGET` is simply their sum. Accumulate newest-first and **cut on a message
  boundary**, never mid-message. Replaces `HISTORY_RECENT_LIMIT` and `HISTORY_CONTINUITY_LIMIT`.
- Owner text that survived in the chat is dialogue unconditionally: commands are deleted by the
  middleware and field input by `delete_text_input`, so survival is the evidence the boundary used
  to provide. Keep one cheap floor — do not read past the oldest row in `telegram_messages`.
- `_dialogue_content` ([history.py](../src/safwa/history.py)) stamps a local time on a message when
  the day or the hour changes, not on every one — a per-message stamp costs 5–8 % of the budget.
- `/summarize` posts a Summary immediately. It replaces `/newsession` as the only way to cut context
  deliberately.
- `SUMMARY_PROMPT` ([continuity.py](../src/safwa/continuity.py)): an **absolute** date per section,
  never a relative "(Today)" label — the message outlives the day it was written on. The previous
  day's detailed section collapses into the general part when the next Summary is written, or the
  Summary grows into a copy of the dialogue. State the 2 000-token ceiling in the prompt.
- Summarization reads back to the previous Summary or to the token cap, whichever comes first, and
  **rewrites** that Summary rather than writing beside it — the window keeps only the newest one, so
  anything the older one alone remembered would otherwise be lost with it. The message budget **is**
  `SUMMARY_TRIGGER_TOKENS`, so there is one knob rather than two that can drift: a Summary is written
  exactly when the window is full.
- Memory reads back to **its own cursor**, not to a fixed count. This also fixes a live defect:
  `maintain_memory` currently fetches 500 messages and only then filters by
  `MemorySyncState.processed_message_id`, so anything older than 500 messages since the last sync is
  skipped forever. Store the cursor as a time rather than a message id.
- Calibrate `TOKEN_CHARS_ESTIMATE`. At 3.0 chars/token it under-counts Cyrillic by roughly a third,
  so a 10 000 budget really spends 13–15 k.

**Done when** a fresh chat with no marker at all produces a correct bounded dialogue, a forced
`/summarize` cuts it, and memory picks up every message since its cursor.

## Phase 2 — one live screen, one outcome text · done

- A single outcome formatter, used by all three sites that describe a resolved proposal: the Save
  receipt, the Discard receipt, and the frozen screen `dismiss_prior_ui` leaves behind. Today each
  writes its own text. It takes the fields `_proposal_result_details` already builds, **omits empty
  ones**, caps the number of lines, and escapes with `html.escape`.
- Both receipts are posted as `DIALOGUE_ASSISTANT`, so the model reads back what happened. The
  Discard receipt names what was refused, and for a Diary proposal it carries the draft reference so
  the advisor can re-offer it from the same stamp.
- `dismiss_prior_ui` is called from commands and from `nav:` navigation as well, not only from
  `dialogue.ordinary_text`. Its selector changes from "screens older than this message" to "every
  other interactive screen": a button pressed on an old dashboard must still dismiss a newer
  proposal above it.
- `/cancel` must actually abort. `guard.cancel()` only bumps `dialogue_revision` and releases the
  lease ([_core.py](../src/safwa/telegram/_core.py)); the running coroutine finishes and its result
  is discarded afterwards. Hold the task and cancel it, or a stopped turn keeps calling the provider.

**Done when** a proposal cannot survive a menu tap, `/cancel` stops provider traffic within a
second, and one saved Card leaves one readable line in the conversation.

## Phase 3 — the subagent runner · done

Generalize [ai/mini.py](../src/safwa/ai/mini.py) rather than writing a second engine. It already has
the shape: its own system prompt, its own tool set, a terminal call that *is* the answer, its own
budget, and repairs fed back as tool results.

- Several read tools instead of one; an `AgentRun` trace, which mini-sessions do not keep today; a
  wall-clock deadline (`SUBAGENT_DEADLINE_SECONDS = 300`) enforced with `asyncio.wait_for` so it
  aborts rather than merely going stale. With a real deadline the number of provider calls needs no
  separate cap.
- `call_subagent(name, request)` joins `IMMEDIATE_TOOLS`. As an immediate tool it runs in the turn
  that asked for it, and the existing `mixed_read_and_mutation_tools` rule then forces
  `propose_diary` into the next response — which is the wanted order, read first, then propose.
- A subagent's own tool set never contains `call_subagent`. No recursion.
- The roster lives in `SYSTEM_PROMPT`: name, description, when to call. No discovery tool.
- The Diary subagent: its own prompt, read tools = the day-history reader plus `query_safwa`,
  terminal `diary_report`. Its prompt fixes the entry's language explicitly — it does not inherit
  the advisor's "reply in the user's language" rule.
- The report has two shapes: **a draft** (the day's text, the already saved entry if there is one
  with its id, and a stamp) and **nothing to write** (no stamp, and a question to put to the owner).
  A stamp is never issued without a draft.
- The subagent reads both sources, because neither alone is the day: the conversation for what was
  said and felt, `ai_card_events` and `ai_checks.resolved_at` for what was actually done. Manual UI
  work leaves no trace in the dialogue at all.
- The `diary_stamps` row lands here rather than in Phase 4: a draft report carries a stamp, so the
  record that holds the body has to exist before `call_subagent` can return one. Phase 4 adds the
  entry id to it alongside `diary_entries`. Until then the advisor is told a draft is ready and can
  voice the remark, but there is no tool that saves it.

**Done when** a Diary subagent invoked by hand returns a usable draft for a day of mixed
conversation and manual UI work, and a five-minute hang is cut off.

## Phase 4 — the Diary entity · done

- `diary_entries` in [models.py](../src/safwa/models.py): `entry_date` **UNIQUE**, body, `version`,
  timestamps. The advisor's remark is screen-only and is not stored — the entry keeps the owner's
  voice alone. Schema changes mean rebuilding the database; run `uv run safwa-backup` first.
- The stamp record holds the entry date, the target `entry_id`, the `action`, the draft body and the
  advisor remark. Reuse the `CallbackToken` pattern — a row, a TTL, an atomic claim — rather than a
  second nonce mechanism. It expires at the end of the local day it was issued on.
- `propose_diary_update(stamp)`: every other field comes from the stamp, including the action, so one
  tool covers create, edit and remove without the advisor choosing between them. A stamp that is
  missing or expired returns a retryable `ToolPreparationError` telling the model to call the
  subagent first. Discard does **not** consume the stamp — a discarded proposal can be re-offered
  from it the same day — while Save clears every stamp for that date.
- `diary_report` therefore carries `date` plus exactly one of `entry` (with `remark`), `remove`, or
  `question`. The host resolves `date` to an entry itself and derives the action from what it finds,
  so a create over a day that already has an entry cannot reach the UNIQUE constraint at Save, and a
  removal of a day with nothing saved comes back as a report the advisor can simply relay.
- The subagent reads `ai_diary` as well: it is the only way to see what a day already says, and it
  has `query_safwa`, so the view has to be named in **its** prompt too, not only the advisor's.
- `read_day` takes a date, and `recent` gains an `until` bound. A period open at the newest end
  would fold today's conversation into yesterday's entry.
- `AgentChange.entity` gains `diary`; `MUTATION_TOOL_MODELS`, `_ENTITY_MODELS`, the `apply` branch,
  and a render branch in `render_proposal` follow. Without the render branch the screen falls into
  the generic path and shows a summary line instead of the text.
- `ai_diary` view in `create_ai_views`, added to `ALLOWED_VIEWS` **and** to the view list inside
  `SYSTEM_PROMPT` — missing the second makes the view invisible to the model.

**Done when** a full round trip works: subagent → proposal → Save → the entry is in the table and
its text is in the conversation; and a second run the same day offers an overwrite.

## Phase 5 — the system Reminder and Settings

- A `system` column on `reminders`. Filter it out of the `ai_reminders` view, out of the
  `/reminders` query, and guard update/delete in `domain.py`. Nothing else: a Reminder the model
  cannot see in `ai_reminders` is a Reminder whose id it cannot name, so the mutation tools need no
  special case.
- Settings gains Diary on/off, the end-of-day time (default `DIARY_TIME_DEFAULT = "22:00"` in
  constants), and the extra instruction passed to the subagent. Follow the `/setmemtime HH:MM|off`
  shape — `off` is the switch, so there is no second flag to keep in sync.
- Settings is the single source of truth: writing it updates the hidden Reminder row through
  `domain.py`, and `reconcile_reminders` rebuilds its wall clock after a timezone move, as it does
  for every other row.
- Remove `/snooze` and `reminders_enabled`, along with `reminders_paused`
  ([scheduler.py](../src/safwa/scheduler.py)) and the two profile columns. Quiet windows on interval
  schedules stay the only mute, by decision.

**Done when** the Diary fires from Settings at 22:00, the Reminder behind it appears nowhere in the
UI or in `ai_reminders`, and changing the time in Settings moves it.

## Deferred

- Asynchronous `call_subagent`, with the result queue and startup reconcile it requires.
- A second subagent. The roster format should not be generalized before there is one.
- A `diary` citation type and a `/diary` browsing screen. Five openable types is a documented
  invariant; a sixth is its own decision, not a side effect of this feature.

## Documentation to update when the phases land

[ARCHITECTURE.md](ARCHITECTURE.md) (runtime wiring, feature map, table count, command count),
[STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md), [CLAUDE.md](../CLAUDE.md) (the history and proposal
sections both describe session boundaries), and
[diagrams/09-doc-code-inconsistencies.md](diagrams/09-doc-code-inconsistencies.md) for anything left
unresolved.
