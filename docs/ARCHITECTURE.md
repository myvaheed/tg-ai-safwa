# Safwa — Architecture

Fast orientation map for a new session. Companion: [STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md)
(module/entity index), [INITIAL_PLAN.md](INITIAL_PLAN.md) + [MEMORY_HISTORY_USAGE.md](MEMORY_HISTORY_USAGE.md)
(product spec).

## What it is

Single-owner personal agile advisor. One private Telegram chat, aiogram 3 long polling, a local
OpenAI-compatible LLM (`SAFWA_AI_PROVIDER`: LM Studio by default, OpenRouter for
`openai/gpt-5.6-luna`), SQLite/SQLAlchemy 2 async. Runs on Windows, `uv`-managed,
Python `>=3.12,<3.13`. No server, no multi-user, no Mini App.

## Runtime wiring

`main.main()` → `upgrade_database` (`create_all`) → `asyncio.run(main.run(settings))`
([main.py:56](../src/safwa/main.py:56)):

1. `Settings` (pydantic-settings, `SAFWA_` prefix, `.env`) — [config.py](../src/safwa/config.py)
2. `Database` — async engine + `PRAGMA foreign_keys/WAL/busy_timeout` ([db.py:37](../src/safwa/db.py:37))
3. one boot session: `bootstrap_workspace` → `recover_startup` → `create_ai_views`
4. `OpenAICompatibleProvider` → `MemoryFileStore.sync()` → `ReadOnlyQueryRunner`
5. `Bot` (`parse_mode=HTML`) → `TelegramHistorySource.from_settings(..., bot_user_id=me.id)` → `.start()`
6. `AIAdvisor` with its routed subagents (`board`, `diary`) — after the history source, which a
   subagent reads through
7. `PersonaContinuity` → `GenerationGuard` → `build_transcriber` (`None` unless
   `SAFWA_ASR_PROVIDER` is set) → `Services` dataclass → `dispatcher["services"]`
8. `OwnerAndWritingMiddleware` on both message and callback outer middleware; `router` included
9. `sync_bot_commands` (17 commands, 16 while the workspace is in Planning)
10. four background tasks, all cancelled in the polling `finally`:
    - `memory.poll(memory_error)` — 5 s `memory.md` hash watcher
    - `run_scheduler(...)` with gate/escalate hooks from `ReminderRuntime` — 30 s Reminder poll,
      `SAFWA_SCHEDULER_ENABLED` (on by default)
    - `run_sprint_expiry(...)` — 300 s poll that closes a Sprint past its end date and says so
    - `run_memory_maintenance(...)` — 60 s daily-memory-sync eligibility loop

Memory maintenance stands down while `guard.active`; the Reminder poll has its own wider gate
(`ReminderRuntime.can_escalate`) that also waits out any open proposal.

## Layering

Flat modules under `src/safwa/` plus two packages (`ai/`, `telegram/`) — one file per responsibility,
not one package per layer:

| Responsibility | Files |
|---|---|
| domain | [domain.py](../src/safwa/domain.py), [enums.py](../src/safwa/enums.py), [models.py](../src/safwa/models.py), [saved_requests.py](../src/safwa/saved_requests.py), [reminders.py](../src/safwa/reminders.py) |
| application | [domain.py](../src/safwa/domain.py) (mutations), [ai/service.py](../src/safwa/ai/service.py) (`ProposalService`), [continuity.py](../src/safwa/continuity.py), [scheduler.py](../src/safwa/scheduler.py), [analytics.py](../src/safwa/analytics.py) |
| infrastructure | [db.py](../src/safwa/db.py), [history.py](../src/safwa/history.py), [memory.py](../src/safwa/memory.py), [ai/provider.py](../src/safwa/ai/provider.py), [ai/sql.py](../src/safwa/ai/sql.py), [backup.py](../src/safwa/backup.py) |
| telegram | [telegram/](../src/safwa/telegram) (16 modules, ~5.5k lines) |
| ai | [ai/](../src/safwa/ai) (board, context, contracts, diary, mini, prepare, provider, reminder_sessions, service, sql, subagents) |
| bootstrap | [main.py](../src/safwa/main.py), [config.py](../src/safwa/config.py), [constants.py](../src/safwa/constants.py), [recovery.py](../src/safwa/recovery.py), [qa.py](../src/safwa/qa.py) |

[constants.py](../src/safwa/constants.py) holds every limit, budget, cap, interval, and the effort
scale. It imports nothing from Safwa, so any module may import it and `config.py` takes its defaults
from there without an import cycle. A new tuning number goes there, not next to its use site.

### `telegram` package — one-way import chain

```
_core.py            Services, router, GenerationGuard, middleware, CallbackContext, choice tables
   ↑
_presentation.py    pure text/labels/markup/paging — no session, no bot
   ↑
_messaging.py       every send/edit/delete + MessageKind registration + token buttons
   ↑
text_input.py       reusable ordinary-text editor: current value, validation, Back
   ↑
cards.py  checks.py  items.py  diary.py    render modules
   ↑
screens.py  proposals.py    what one AI turn shows: cited items, the review screen
   ↑
commands.py  callbacks.py  dialogue.py  the ONLY @router handlers
```

`__init__.py` imports the three handler modules for their registration side effect — dropping one
silently unregisters its handlers. Leading underscore = module-local; a name used by a sibling has
no underscore (the whole package is private behind `__init__.__all__`).

## Feature map

### Planning domain

- **Cards**: strict tree. Goal root-only; Idea root or under Goal; Action root or under Goal/Idea,
  no children. Stages `backlog|sprint|today|done|cancelled`.
- `manual_stage` = what the user set. `effective_stage` = derived for parents from descendants
  (`aggregate_child_stages` → live max by `LIVE_STAGE_PRECEDENCE`, else all-cancelled → Cancelled,
  else Done) and propagated up by `propagate_ancestors`. Dashboards/queries read `effective_stage`.
- Action-only fields: `effort_points` (`constants.EFFORT_POINTS = {1,2,3,5,8,13}`, required), `repeatable`,
  categories, energy types, `liked`. Stripped for Goal/Idea at both the AI and domain boundaries.
- `blocked` is warning-only, requires non-empty `blocked_description`. No Card-to-Card dependency graph.
- Priority `critical|medium|low`; `hard_time` independent boolean.
- **Card links**: a Card owns exactly three, all the same shape — Values, Tags, Checks. Each has a
  `ReferenceSpec` in `CARD_REFERENCE_SPECS`, a `toggle_card_*` command, and a `card_*` junction table;
  `ReferenceSpec.name_attr` is the only difference (a Check is named by `title`). Values have `active`
  (AI focus); Tags do not. Neither classifies a Check.
- **Checks**: a state observation ("did this hold?"), not planned work — no effort, never in a Sprint.
  A Card is its only relationship, held on the Card side in `card_checks`: one Check may hang on many
  Cards (one answer then satisfies all of them) or on none. A Check carries only a title and
  `repeatable`. `outcome` is `passed|missed`;
  **Pending is derived** (`outcome IS NULL`) and never stored, so there is no reset path. `resolved_at`
  keeps the *first* resolution — re-answering overwrites the outcome and the previous one is not
  retained. `series_id`/`source_instance_id` mirror the Card repeat lineage. A repeatable Check spawns a
  Pending successor **only on the Pending → resolved transition**, linked to its live Cards alone; if
  every linked Card is terminal or archived, the series ends there.
- **Done-gate**: `finish_action` refuses `Done` while a Card has Pending Checks and names their ids and
  titles; `Cancelled` is not gated. Resolving through the gate suppresses the spawn, which is what stops
  a repeatable Check from blocking its own Card forever. `_copy_repeat_successor` clones one Pending
  copy per Check series (grouping matters — an in-cycle spawn leaves two rows of one series on the Card).
- **Repeatable Actions**: `finish_action` → `_copy_repeat_successor` clones parent, text, priority,
  hard_time, blocked, effort, and all four link sets into a successor at the prior live stage.
- **Sprints**: `start_sprint` (Planning only) needs Success criteria, snapshots every non-archived
  Action in Sprint/Today as `scope_kind="initial"` commitments, and schedules two one-shot Reminders
  at the start clock — the day before the end date and on it. Planned length is
  `profile.sprint_length_days` (default `SPRINT_LENGTH_DAYS = 14`, allowed 2–60) calendar days.
  `finish_sprint` = Finish Early, no pause, and deletes those Reminders; `expire_due_sprint` closes an
  unfinished Sprint at the local midnight after its end date, leaving every Action's stage alone.
  `_sync_commitment_for_stage` records later add/remove.
- **Archive/delete**: `archive_subtree` (reversible, keeps events), `delete_subtree` (needs a second
  destructive confirmation), `archive_tag`/`archive_value` drop links atomically and clear focus.
- **Optimistic concurrency**: every entity has `version`; `workspace.revision` bumps on mutation and
  is what a pending proposal is checked against before it applies. Expected failure: `StaleStateError`.
- **Audit**: `card_events` (actor, operation, before/after snapshot, correlation, sprint).

### Telegram UI

- Dashboards `/today` `/backlog` `/sprint` list **Actions only**; Goals/Ideas reachable via hierarchy,
  `Children`, search, Requests, item navigation.
- Today belongs to a running Sprint ([telegram/sprint.py](../src/safwa/telegram/sprint.py)): in
  Planning the menu drops its button, `sync_bot_commands` drops the command, and `/today` answers that
  a Sprint has to be planned first. Each row of those two dashboards carries a one-tap stage move —
  `🏃` leading on Today, `☀️` trailing on Sprint. Starting a Sprint is Success criteria → the plan
  (Sprint **and** Today Actions, the Today ones marked) → `✅ Confirm plan: Start`, and an empty plan
  offers no Start button.
- Checks are item-shaped, not Card-shaped ([telegram/checks.py](../src/safwa/telegram/checks.py)) and
  are reached from the Card screen, which shows `☑️ Checks (pending/total)` **only when at least one
  Check hangs on the Card**. `render_check` also stands alone — an advisor link reaches a Check that
  hangs on no Card, and `card_id` then only decides where Back goes.
  Pressing `Done` on a gated Card opens the resolution screen instead of finishing it: each Check
  offers `✅ Passed` / `❌ Missed`, `Save` appears once an answer is set and finishes the Card in one
  transaction, and `Back` leaves it live. No answer is prefilled.
  A Check linked to no Card is reachable only through `ai_checks`; there is no `/checks` yet.
- Every inline button is a single-use `CallbackToken` row rendered as `cb:<token>` (24 h expiry),
  claimed atomically by `UPDATE … RETURNING` in `callback_token_handler`. Menu buttons use `nav:<action>`.
- `CALLBACK_ACTIONS` ([callbacks.py:814](../src/safwa/telegram/callbacks.py:814)) is the action→handler registry.
- `UiSession` holds transient editor state. Every ordinary-text editor uses the shared `text_input`
  kind: it replaces its originating screen in place, shows the copyable current value and `↩️ Back`,
  and records the source message plus its return action. A rejected field value redraws that same
  editor with its validation error; Back and a successful value both restore the preceding screen in
  the same message. Manual Card creation persists **nothing** until Save.
- The typed reply is deleted as `UI_INPUT`; its flow renderer restores the previous screen through
  `delete_text_input` and `edit_registered_message`.
- `dismiss_prior_ui` leaves exactly one interaction screen live: it removes **every other**
  `DASHBOARD`/`CARD_EDITOR`/`APPROVAL` screen, not just the older ones, so a button pressed on a
  dashboard below an open proposal still answers that proposal. It runs from `ordinary_text`, from
  `nav:` navigation, and from a `router.message` middleware for every slash command.
- All bot text is HTML — escape user/model text with `html.escape`.

### Voice input

Why it is shaped this way, including the rejected alternatives: [ASR_PLAN.md](ASR_PLAN.md).

`SAFWA_ASR_PROVIDER` is `off` by default and `Services.transcriber` is then `None`, which is what
keeps the bot text-only. `openai`, `groq` and `local` all speak the same OpenAI-compatible
`/audio/transcriptions` API, so one `OpenAITranscriber` ([asr.py](../src/safwa/asr.py)) covers a
metered endpoint and a self-hosted whisper server alike — they differ by base URL.

`faster_whisper` is the offline engine behind the `asr-local` extra: CTranslate2 in this process, no
server and no API key. Its model is built once at startup and decoded in `asyncio.to_thread`, so
polling is never blocked, and the decode carries no timeout — a local run cannot hang on a network,
and abandoning the wait would leave the thread running anyway. A missing extra fails at startup with
the install command.

`SAFWA_ASR_DEVICE` resolves against what actually loads. CTranslate2 loads cuBLAS/cuDNN with its
first kernel rather than when the model is built, so a missing DLL raises either at startup or a
whole decode later; both fall back to `cpu`, log the reason once, and the mid-decode one rebuilds
and retries that same recording, dropping a CUDA-only compute type on the way. The `asr-cuda` extra
supplies those DLLs as wheels, which land in `site-packages/nvidia/*/bin` where nothing looks, so
`_register_cuda_runtime` registers them before `faster_whisper` is imported — through
`add_dll_directory` **and** `PATH`, since the lazy load goes through the ordinary search order.

It is also the only engine that reports progress: it yields segments while decoding, so
`transcribe(progress=…)` edits one `STATUS` message, throttled and only past
`ASR_PROGRESS_MIN_AUDIO_SECONDS`, which `_TranscriptionProgress` deletes when the decode ends.

`voice_message` ([telegram/dialogue.py](../src/safwa/telegram/dialogue.py)) guards duration and
size, downloads the file, transcribes it, posts the transcript with `send_owner_turn`, then hands
the same `run_dialogue_turn` the text path uses. A voice message arriving mid-generation cannot be
queued by the middleware, which has no text to queue, so it reaches the handler and its transcript
is queued instead (`queue_owner_text`). The lease is therefore settled after the decode rather than
before it: a decode runs long enough for the lease to have changed hands, so the handler drops a
background generation and cancels a foreground one that cannot queue, exactly as the middleware
does for text. `SAFWA_ASR_LANGUAGE` pins the language; left empty the engine detects one per
message, which misfires on short notes.

### AI advisor

Path: ordinary text → `dialogue.ordinary_text` → `guard.acquire` → `history.dialogue()` →
`AIAdvisor.handle` → agent loop → proposals or a final message.

- The Advisor's tools are `query_safwa(sql)` and `route(name)`, both immediate
  (`IMMEDIATE_TOOLS`, [ai/service.py](../src/safwa/ai/service.py)). It has **no mutation tool at
  all**: every one belongs to the subagent that owns that feature, so a change it describes instead
  of routing is a change that never happens. `route` is offered only when a roster is wired.
- Offering an item is not a tool. The model cites it in its own prose as `[Milk](check:14)`. The six
  openable types are Card, Check, Tag, Value, Saved Request and Diary day; `render_citations`
  ([telegram/screens.py](../src/safwa/telegram/screens.py)) rewrites each citation of the escaped
  reply into `<a href="https://t.me/<bot>?start=check-14">`. The href is built from a validated id and
  `SAFWA_TELEGRAM_BOT_USERNAME`, never from the model, and the payload separator is `-` because
  `?start=` accepts only `[A-Za-z0-9_-]`. An id that is missing or archived keeps its words and loses its link: the reply is
  a `DIALOGUE_ASSISTANT` message that stays in the chat, so a dead link would stay with it.
- A `diary:` citation is the one the advisor never authors — it cannot read the Diary and only copies
  what its `diary` subagent returned. Its **label** is rebuilt from the entry as well (`diary_label`
  → `04.03.2026 [6 🙂]`), so the date and score on the link are always the saved ones; that bracketed
  score is why `CITATION_PATTERN` permits one level of nesting inside a label.
- Tapping one sends `/start check-14`. The middleware deletes that command and `command_start`
  (`start_payload` → `open_citation` → `open_item_screen`) sends the item's manual screen as a new
  message, leaving the reply above it intact. A vanished item answers `⚠️ Error while opening: …`.
  `diary-12` opens [telegram/diary.py](../src/safwa/telegram/diary.py) `render_diary`: the whole day,
  read-only and without a single button, because the Diary is written through proposals alone.
- The `card` tool writes **every** Card link — `value_*`, `tag_*`, `check_*`, one relationship group per
  `link`/`unlink` call. The `check` tool only creates, edits and answers a Check; it never attaches one.
  Since the UI cannot create, rename or (un)link a Check, those paths exist only here.
  `check_query` resolves an exact Check title, so a Check can be attached without knowing its id.
- `_guard_pending_checks` ([ai/prepare.py](../src/safwa/ai/prepare.py)) refuses to *prepare* a
  completion while Pending Checks exist, returning a
  retryable `ToolPreparationError` that carries their ids **and titles** so the model does not spend a
  `query_safwa` round finding them. The model then proposes `check(mode="complete"|"cancel")` for an
  answer the owner already gave, or cites the Check so they answer it themselves.
- Every mutation tool takes a `mode`, and a `mode` **is** an `AgentChange.action` spelled the same:
  `create`, `update`, `move`, `complete`, `cancel`, `reopen`, `link`, `unlink`, `archive`, `delete`.
  `mutation_change_from_tool` therefore translates no names; `remove` is the one tool that names its
  entity, because it archives or deletes any of them.
- **The model never mutates and never writes mutation SQL.** Tool call → Pydantic model in
  [ai/contracts.py](../src/safwa/ai/contracts.py) → `AgentChange` → `ChangePreparer.prepare` against
  live data ([ai/prepare.py](../src/safwa/ai/prepare.py)) → `ChangeProposal` + `ProposalChange`
  rows → request-only autoapproval for an allowlisted operation, or a read-only review screen with
  only **Save**/**Discard** → `ProposalService.apply` calls the *same* `domain.py` functions the manual
  UI calls. The reviewer never mutates; its terminal decision is applied by the ordinary service.
- Multiple mutation calls in one turn become independent queued proposal screens in call order; the
  queue lives in an `AgentStep` row with `kind="approval_batch"`. The model resumes only after the last
  item resolves (`resolve_approval` → `continue_agent_approval`) and receives all mutation and read results.
- The session itself is the `agent_runs` row: `state_json` holds the dialogue, the transcript, the
  tool-call budget and the receipts already shown, so a suspended turn resumes from its own record
  rather than from the screen that suspended it. Resuming takes `claimed_at` first, and the batch is
  closed in the same commit, so a crash costs one repeated press and nothing else.
- [ai/autoapproval.py](../src/safwa/ai/autoapproval.py) reviews only the active queue head. Its
  declarative `(entity, action)` registry optionally restricts changed fields and is the single place
  to enable another operation; creation is not in it, so every new item takes the review screen. An
  auto-save says so in its own tool result, so the model reports it as done rather than as the
  owner's decision. The mini-session receives the final owner request and fresh normalized
  diff, never dialogue history. An ineligible operation, `require_review`, or reviewer/apply failure
  leaves the original pending proposal untouched; approved auto-saves use the same transaction that
  resolves the batch item and are recorded as `⚡ Auto-saved`.
- If one provider response mixes an immediate tool and mutation tools, reads run immediately but each
  mutation gets a short retryable `mixed_read_and_mutation_tools` result. The model retries mutations
  in its next response, after it has seen the read data.
- The batch also stores the request's `dialogue` and its `transcript` — every assistant/tool message the
  run produced after the context prefix. A resume rebuilds only the prefix (system prompt, planning state,
  memory, clock) and replays that transcript, so a request keeps its own intermediate steps across each
  approval instead of re-planning from the last tool call. It never re-reads Telegram for this.
- An empty provider turn is read by kind ([ai/provider.py](../src/safwa/ai/provider.py) `_read_turn`).
  No `choices` at all, or a choice cut off (`finish_reason` other than `stop`), is an upstream
  failure: retried once (`AI_EMPTY_RESPONSE_ATTEMPTS`), then raised carrying the provider's own
  reason. A `stop` with no content is the model deliberately adding nothing and is a valid turn —
  `_run_agent_loop(allow_silence=True)` accepts it after an approval queue, where the receipts are
  the answer; elsewhere it still raises `The advisor finished without a response`.
- Failed preparations return structured `ToolPreparationError` results and are retried for at most
  `MAX_REPAIR_ROUNDS = 5`; `MAX_TOOL_CALLS = 64`.
- A resolved queue item comes back as its own tool result carrying `status`, `entity`, `action`,
  `summary`, the resolved `fields`, and a `next` instruction; the summaries the owner sees never enter
  canonical history, summaries, or memory. `_assistant_content_with_request_progress` now only serves
  batches suspended before transcripts were persisted.
- Context has four positions: static `SYSTEM_PROMPT`; a block with planning state and
  `memory.text`; canonical bounded dialogue; then a trailing block with the local clock. Only the
  first is a system message; the other two are user messages prefixed `[System]: `.
  Planning state carries workspace mode, About Me, advisor instructions, active Values, available
  Tags, the Sprint with its Success criteria (or the Planning notice and the draft criteria), up to
  `CONTEXT_CRITICAL_CARD_LIMIT = 10` critical Cards with those carrying an active Value first, and —
  only while a Sprint runs — Today Actions. Every item is written as its citation, `[name](kind:id)`,
  ready to reuse in a reply. Deliberately **no** Sprint metrics and **no** precomputed Card
  candidates; the model reaches those through `query_safwa`.

### Subagents

[ai/subagents.py](../src/safwa/ai/subagents.py) defines a routed subagent: a session of the same
shape the Advisor runs on — its own prompt, its own tools, its own transcript, its own row in
`agent_runs` — reading the same conversation.

- `route(name)` **hands the turn over**. The Advisor writes nothing after it: the subagent's prose is
  the chat message and its proposal is the review screen, with nothing relayed in between. It takes
  no request, because the subagent reads the conversation as it stands and treats the last owner
  message as addressed to it; a read the Advisor did first is not carried over.
- A routed subagent has no `route`, so there is no recursion, and it declares its own tool list —
  read tools plus the mutation tools it owns.
- `PERSONA` is one block composed into every routed prompt: voice, the owner's language, the citation
  format. Three copies would drift into three dialects.
- Each subagent declares how much context it needs: `history_messages` (all of it, or that many of
  the newest messages) and `planning_state`. The Diary reads its day with `read_day`, so it takes
  only `DIARY_HISTORY_MESSAGES` — enough to be told what to change about what it just proposed.
- The routing rules are prose in `SYSTEM_PROMPT` (`# Routing`) — a static block inside the cacheable
  prefix. There is no discovery tool, so **a subagent missing from that section is never routed to**.
- `route` resumes that subagent's saved session if it left one, and starts a new one otherwise;
  the model never chooses between the two. `asyncio.wait_for(SUBAGENT_DEADLINE_SECONDS = 300)` bounds
  one **active stretch**, never a suspension — a saved session is not a slow session.
- Approve and Discard resume the session directly. Words typed over an open screen do not: the screen
  freezes, the subagent's session is saved as `awaiting_approval` with its own results folded into its
  transcript, and the words go to the Advisor, which answers them or routes back. That is what lets
  "the same, but capitalise the name" reach the session that wrote the refused proposal.
- **A saved session is restorable for exactly one Advisor turn.** `_close_lapsed_sessions` runs at the
  end of every Advisor turn and abandons each saved subagent session that turn did not route into, so
  words about something else end the draft instead of leaving it to be revived by a later `route`. A
  session whose batch is still `pending` is left alone: its screen is live and Save resumes it.
- **board** ([ai/board.py](../src/safwa/ai/board.py)): owns `card`, `check`, `value`, `tag`,
  `request`, `reminder` and `remove`, plus the prompt sections that only serve them — the full
  Planning structure, Checks, the Reminder timing rules, and how to fill a proposal in. It declares
  `planning_state=True`, so the board's current state is in its context, and takes the whole
  dialogue window. `_guard_pending_checks` therefore fires inside board, which cites the Checks
  itself.
- **Diary** ([ai/diary.py](../src/safwa/ai/diary.py)): owns the Diary outright. `ai_diary` is absent
  from the Advisor's `SYSTEM_PROMPT` and the `diary` mutation tool is absent from its tool list, so
  reading a day, writing one, rewriting one and removing one all arrive here. It works the date out
  from the owner's words, so the Advisor never has to. It reads that day's conversation
  (`read_day` → `TelegramHistorySource.day_transcript`) *and* `ai_diary`, `ai_card_events`,
  `ai_checks`, because work done from the buttons never reaches the conversation and what the day
  felt like never reaches the database. It has `query_safwa`, so its own prompt lists those views —
  one it is not told about is one it cannot use.
- The `diary` tool carries `mode` (`update`/`delete`), `date`, `pov`, `ai_comment` and
  `feeling_score`. `pov` is the day in the owner's voice and `ai_comment` is Safwa's one line about
  it; the review screen is exactly those two. `update` is the only way to write a day: whether that
  day already exists is a fact about the data, so preparation reads it and settles the action on
  create or update — and refuses a `delete` of a day that was never written, retryably.
- Reading the Diary or asking about it is prose, not a tool: the subagent answers the owner itself,
  citing each day as `[dd.mm.yyyy](diary:<id>)`.
- `feeling_score` is 0–10 and nullable, stored on `diary_entries`. `FEELING_SCORE_EMOJI`
  ([constants.py](../src/safwa/constants.py)) is the whole scale; the `# Feeling score` block of
  `DIARY_PROMPT` is the whole rubric. 5 is an ordinary day, and **0 is never the model's choice** —
  only the owner's own word.
- A `diary_entries` row is one local date — `entry_date` is UNIQUE, so a second write for a day
  updates it. `ai_comment` is screen-only and is not stored.
- **No receipt carries the day.** `_diary_detail_lines` gives the date, the score and a character
  count; the entry itself appears only on the review screen and on `render_diary`. A receipt stays in
  the conversation and would be re-read on every later turn, while the draft is already held by the
  session that wrote it.
- The nightly ask is an ordinary Reminder marked `system`, derived from Settings by
  `sync_diary_reminder` and rebuilt at startup: the Settings screen's Diary time moves it or, on
  `off`, deletes it, and the Diary instruction is appended to its text. It is hidden from `/reminders`
  and from `ai_reminders`, and the three edit paths in `domain.py` refuse it.

### Read-only SQL — triple guard

[ai/sql.py](../src/safwa/ai/sql.py): one `SELECT`/`WITH … SELECT` over `ai_*` views only.

1. `validate_read_sql` — single statement, `FORBIDDEN` keyword regex, FROM/JOIN name allowlist
   (`ALLOWED_VIEWS`) with CTE names subtracted.
2. `ReadOnlyQueryRunner` — separate `sqlite3` `mode=ro` connection with a `set_authorizer` allowlist.
3. Caps (all in [constants.py](../src/safwa/constants.py)) — `DEFAULT_ROW_LIMIT=50`,
   `DEFAULT_CHAR_BUDGET=12_000`, `DEFAULT_COLUMN_LIMIT=20`, `DEFAULT_CELL_LIMIT=2_000`,
   `QUERY_TIMEOUT_SECONDS=2.0`; a capped result returns a `notice`.

`ai_checks` exposes `status` as `COALESCE(outcome, 'pending')`, so a query never has to know that
Pending is a null column. It is also the only route to a Check linked to no Card. The other direction
needs no join view: `ai_cards` carries `direct_checks` (titles) and `pending_checks` (count).

Views are dropped and rebuilt by `create_ai_views` **on every startup** — change view shape there,
never with a migration. A new view must be added to `ALLOWED_VIEWS` *and* to the view list of every
prompt that should reach it, or that reader cannot use it. `ALLOWED_VIEWS` is shared by every reader,
so the prompt is what scopes one: `ai_diary` is allowed, and only `DIARY_PROMPT` names it — which is
what keeps the advisor out of the Diary. The same function drops the `card_search` FTS5 table and its
triggers from older databases.

Saved Requests reuse the same validator plus two extra rules (`normalize_request_sql`): the query must
mention `ai_cards` and return a column named `id`.

### History — Telegram is the canonical dialogue store

[history.py](../src/safwa/history.py) rereads the real private chat through Telethon on **every** advisor
turn. `telegram_messages` stores only event metadata, including `(chat_id, message_id, direction,
kind, related_id, event_id)` — never persona text.

- Every bot message must be registered with a `MessageKind` (`send_registered` / `register_message`).
  Unregistered outgoing = invisible to the LLM; wrongly-kinded = UI noise leaks into persona history.
- `mark_message` writes both the `MessageKind` and an immutable 128-bit event UUID into invisible
  Telegram text; `read_message_mark` recovers them. SQLite stores the same UUID, so outgoing history uses direct event lookup and visible-text edits preserve identity. A rebuilt database still recovers classification from Telegram. Every bot send site must mark its text. An unmarked bot message is excluded; v1 has no legacy fallback. Owner messages cannot be marked, so owner text that is still in the chat is treated as dialogue.
- Item citations ride in the text the same way. Telethon returns plain text, so `restore_citations`rewrites each `?start=<type>-<id>` link entity back into the `[Milk](check:14)` the model wrote; otherwise the model rereads its own citations as bare words and unlearns the format. Entity offsets are UTF-16 units, so the slicing happens in surrogate space.
- Only `DIALOGUE_USER`, `DIALOGUE_ASSISTANT`, `REMINDER`, and `SUMMARY` become dialogue. Everything
  else (`COMMAND`, `UI_INPUT`, `DASHBOARD`, `CARD_EDITOR`, `APPROVAL`, `RECEIPT`, `STATUS`,
  `RETROSPECTIVE_PNG`, `ERROR`) is excluded.
- **Bounded by tokens**: `recent` walks backwards and stops at the first of
  `SUMMARY_TRIGGER_TOKENS = 8 000` spent, the newest `📜 Summary`, or the oldest row in
  `telegram_messages`. The budget is checked before an entry is taken, so the cut lands between
  messages. A Summary boundary is followed by up to `SUMMARY_CONTEXT_MESSAGE_LIMIT = 20` older
  messages, and `HISTORY_SCAN_LIMIT` caps the walk itself. `HISTORY_TOKEN_BUDGET` is derived —
  the trigger plus `SUMMARY_TOKEN_CEILING = 2 000` — so the message window and the summarization
  trigger cannot drift apart.
- `day_transcript` reads a period instead of a window: `since` and `until` close it at both ends and
  `stop_at_summary=False` walks through Summaries, because a Summary written at noon must not cut
  that day in half and today's conversation must not leak into yesterday's.
- The middleware deletes every slash command, which is what makes surviving owner text dialogue.
- A voice message carries no text, so Telethon reads nothing back for it and it is skipped. Its
  transcript is the only trace of what was said and reaches the chat as a `DIALOGUE_USER` bot
  message (`send_owner_turn`), the way queued owner text does. A monologue past
  `TELEGRAM_TEXT_LIMIT` becomes several such messages and is answered once, after the last.
  `owner_display_name` heads the first of them `User <Telegram display name>`, or just `User` where
  there is no owner message to read a name from.
- Bot API and Telethon use different message-ID spaces in a private chat. Outgoing messages correlate
  by event UUID. Only owner source-message de-duplication retains the narrow ID/time heuristic because
  a bot cannot attach a marker to incoming owner text.
- `dialogue()` merges consecutive human/user-side items into one `user` turn with `[User]` and
  `[Summary]` tags; Safwa replies use the `assistant` role. A local timestamp is prepended once per
  hour of conversation, not once per message.

### Summaries and memory

- `PersonaContinuity.maybe_summarize` fires after an ordinary exchange once unsummarized dialogue
  reaches `summary_trigger_tokens` (8 000), and `/summarize` forces it. The previous Summary is fed
  back in and rewritten rather than dropped, because the window keeps only the newest one. Before
  posting a `📜 Summary` it verifies the generation lease and rereads the history snapshot; stale
  output is discarded.
- **`data/memory.md` is authoritative**: UTF-8, one non-empty fact per line, ~4 000-token budget.
  `memory_fact_cache` is a disposable mirror. AI writes go through `replace_facts`, which re-checks the
  file hash **before and after** writing a temp file, then `os.replace`s — a concurrent local edit is
  preserved, not overwritten. An invalid/oversized file disables injection instead of failing the turn;
  a missing file intentionally clears memory.
- `maintain_memory` reads back to its own cursor (`MemorySyncState.processed_until`, a time), retells
  ~2K-token chunks (500-token overlap), reconciles the fact list, writes atomically, and only then
  advances the cursor. Invalid provider JSON/schema or a stale lease stops the run without changing
  either the file or the cursor.
- The Memory sync time in Settings (`HH:MM`, or `off`) gates one automatic run per local calendar day
  (`run_due_memory_maintenance`, checked once a minute, skipped while foreground is busy).

### Reminders

Safwa sends a proactive message **only** because a Reminder fired — one the owner set, or the one
Settings derives for the Diary. There are no computed nudge kinds. A Reminder is instruction text plus
a schedule — see [REMINDERS_PLAN.md](REMINDERS_PLAN.md) for the full contract.

- The 30 s poll *is* the alarm clock. `Reminder.next_fire_at` is the only column it reads, and it is
  advanced **only after an escalation succeeds** — which is why a cancelled or crashed turn loses
  nothing: the row is still overdue, so the next tick retries it.
- There is no global mute. A `system` Reminder fires like any other; quiet windows on an interval
  schedule are the only way to silence one.
- The system's only output is an **escalation**: the instruction text is handed to the main advisor as
  a synthetic final user turn after the canonical `history.dialogue(owner_id)`. The same bounded
  window and the same tools apply as for an ordinary request. The reminder system itself never
  composes the answer or renders an item.
- One write-free setup mini-session ([ai/mini.py](../src/safwa/ai/mini.py),
  [ai/reminder_sessions.py](../src/safwa/ai/reminder_sessions.py)) resolves free-text timing before the
  proposal row exists, so the review screen shows a real schedule. At fire time there is no preflight
  LLM session: the main advisor receives the Reminder directly and uses `query_safwa` first when its
  text names Safwa items.
- Schedule arithmetic is one pure module ([reminders.py](../src/safwa/reminders.py)): `resolve`,
  `next_fire`, `roll_forward`, `describe`. `date` is always a start date; `time` is a fire clock when
  weekdays are given and a start clock otherwise.
- The owner always wins. `GenerationGuard` records whether the holder is the owner or a background
  escalation; an owner event cancels a background one rather than being deleted.

### Retrospectives

- Retrospective PNG: Matplotlib `Agg`, 3 axes (sprint effort bars — initial/added/removed/done/
  cancelled; committed-vs-completed by overlapping category; same by overlapping energy) plus
  `retrospective_recommendations` text derived from capacity, scope churn, Hard Time slippage, liked
  feedback, blocked work, and active Values. No "remaining" figure is stored or plotted.

### Concurrency and safety

- `GenerationGuard` — one foreground/background lease. While an ordinary foreground answer runs,
  callbacks are rejected and later owner texts are deleted, represented as queued `UI_INPUT`
  placeholders, restored as one `DIALOGUE_USER` turn, and processed next. `/cancel` bypasses the lease
  and restores the queue; a foreground holder registers its own task, so cancelling aborts the provider
  traffic rather than only marking the answer stale. A background holder registers none — its task is a
  long-lived loop — and still stops through the revision check. Summary, reminder, and memory tasks
  reserve background leases.
- `run_dialogue_turn` captures `dialogue_revision` before generating and discards the answer if it
  changed. Only the owner bumps it, through `cancel`; the workspace revision does not gate the answer,
  because an autoapproved change bumps it from inside the very turn being rendered.
- `OwnerAndWritingMiddleware` drops anything that is not the owner in a private chat.
- `recover_startup` reconciles interrupted `agent_runs` and their claims, sessions abandoned for
  `SESSION_IDLE_DAYS` together with their open batch, expired proposals,
  callback tokens, UI sessions, and Reminder schedules on every boot. `reconcile_reminders` rolls a
  repeat forward only past `REMINDER_CATCHUP_GRACE_MINUTES` — an in-grace overdue row is left alone
  because the first poll firing it *is* the catch-up — and rebuilds wall clocks after a timezone move.

## Schema

`models.py` is the **only** schema source. Startup calls `upgrade_database` = `Base.metadata.create_all`.
`create_all` adds missing tables/indexes and **never alters an existing one**, so a fresh database always
matches `models.py` while a changed column will *not* touch an existing `data/safwa.db`. A schema change
means editing `models.py` and rebuilding the database (`uv run safwa-backup` first).

**Do not add Alembic or write migrations before the first release.** The owner recreates the
pre-release database. Migration support begins after v1.

28 tables: `workspace`, `user_profile`, `values`, `cards`, `card_values`, `tags`, `card_tags`,
`checks`, `card_checks`,
`saved_requests`, `card_categories`, `card_energy_types`, `sprints`, `sprint_commitments`, `card_events`,
`change_proposals`, `proposal_changes`, `agent_runs`, `agent_steps`, `telegram_messages`,
`feedback_queue`, `summary_state`, `memory_fact_cache`, `memory_sync_state`, `reminders`,
`ui_sessions`, `callback_tokens`, `diary_entries`.

Enums are `StrEnum` but columns store plain strings — always compare/assign `.value`.

SQLite has no time zone type, so `DateTime(timezone=True)` accepts an aware value and returns a naive
one. `UtcDateTime` ([models.py](../src/safwa/models.py)) is a `TypeDecorator` that always reads back
tz-aware UTC; the Reminder datetime columns use it because that table is nothing but datetime
arithmetic. It emits identical DDL, so it is not a schema change.

## Commands

```powershell
uv sync --extra dev
uv sync --extra asr-local    # offline voice input (faster-whisper), optional
uv sync --extra asr-cuda     # the same plus the CUDA runtime wheels (~1.3 GB)
uv run safwa                 # run the bot (long polling)
uv run safwa-auth            # one-time Telethon user-session login
uv run safwa-qa-auth         # separate QA Telethon session
uv run safwa-backup          # zip: safwa.db + memory.md + manifest.json
uv run safwa-restore <zip> --yes
uv run pytest -q
uv run pytest tests\e2e -q
uv run pytest tests\e2e\live --live-telegram -q   # opt-in, needs SAFWA_QA_*
uv run ruff check .
```

`asyncio_mode = "auto"` — async tests need no marker. E2E tests use the real SQLite schema and real
services, replacing only Telegram and the provider at their network boundaries (`ScriptedProvider` in
`tests/e2e/conftest.py`). `resolve_qa_config` ([qa.py](../src/safwa/qa.py)) hard-fails on a reused bot
token or Telethon session path so QA can never touch production state.

## Conventions

- ruff `select = ["E","F","I","UP","B"]`, line length 100, `E501` ignored, target py312.
  Every module starts with `from __future__ import annotations`.
- Comments only for non-obvious *why* (Telegram/Telethon quirks, ordering constraints). Match that density.
- User-facing strings are complete sentences; "Card", "Sprint", "Value", "Tag", "Request" are capitalized
  domain nouns.
- Commit subjects: `vX.Y <short summary>`.
- `telegram-bot-exampler/` is an untracked local reference project excluded from ruff — never edit it.
