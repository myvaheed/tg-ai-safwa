# Safwa — Current Architecture and Product Plan

## Product rules

- Safwa is a single-owner personal agile advisor running locally on Windows through Telegram long polling.
- Telegram quick actions and ordinary text are the v1 interfaces. A Mini App is deferred.
- Workspace modes are Planning and Sprint. Only one Sprint can be active, and a Sprint can start only from Planning.
- The default Sprint length is 14 calendar days. Today persists across midnight.
- Cards form a strict tree: Goal is root-only; Idea may be root or a child of Goal; Action may be root or a child of Goal or Idea; Action cannot have children.
- Cards have Backlog, Sprint, Today, Done, and Cancelled stages.
- Populated Goal and Idea stages are derived from descendants: Today, then Sprint, then Backlog; all-terminal descendants produce Done when at least one is Done, otherwise Cancelled.
- Action effort uses 1, 2, 3, 5, 8, or 13. Goal and Idea effort is derived recursively from descendant Actions.
- Priority is Critical, Medium, or Low. Hard Time is an independent boolean.
- An Action may be marked Blocked only with a non-empty description. Safwa does not maintain a Card-to-Card dependency graph.
- Categories are Self, Contribution, Work, and Rest. Energy types are Physical, Cognitive, Social, and Values. Both can overlap and apply only to Actions.
- A Card has three links of one shape: Values, Tags, and Checks. All three are attached and detached from the Card, never from the other side.
- Values and Tags are many-to-many Card classifications. Active Values are the current AI focus; Tags have no focus state. Neither classifies a Check.
- A Check records a state observation, not planned work. It has no effort and never enters a Sprint.
- A Card is a Check's only relationship: a Card lists its Checks, and the same Check may be linked to several Cards or to none. One answer resolves it on every Card it hangs on.
- Check status is Pending, Passed, or Missed. Pending is derived from an unanswered Check and is never stored.
- A Check carries a title and a repeatable flag, nothing else. Creating one, renaming it, and linking or unlinking it from a Card happen only through an AI proposal; the manual screens answer it and toggle repeat.
- A repeatable Check produces a fresh Pending successor as soon as it is answered, linked to whichever of its Cards are still live. Repeat successor Cards carry one Pending copy of each of their Card's Check series.
- A Card cannot be completed while it has Pending Checks. Cancelling is not gated, because abandoning work with unanswered Checks is legitimate.
- A resolved Check may be re-answered; the previous outcome is overwritten and not retained.
- Repeatable Actions create a successor on completion or cancellation, copying the prior live stage and reusable planning fields.
- Today, Backlog, and Sprint dashboards list Actions only. Goal and Idea remain available through hierarchy, search, Requests, and item navigation.
- A Saved Request is an AI-created, verified, read-only SQL query over allowlisted AI views.

## Item creation and proposal UX

Manual Card creation is transient:

- `Add` opens the shared Card editor in create mode.
- Field selection and boolean changes edit the same Telegram message.
- Text fields replace that message with a focused prompt; the typed reply is deleted and the item screen is restored.
- The Card is inserted only when the user presses `Save`.
- `Discard`, navigation away, restart, or a new ordinary message leaves no pending Card record.

AI Card creation uses the normal proposal queue:

- `card(mode="create", ...)` creates one persisted change proposal, not a Card.
- The proposal opens the same read-only Card overview used for committed Cards.
- Proposal screens expose exactly `Save` and `Discard`; fields cannot be edited inside AI review. When the decision is the user's rather than the model's, the model cites the item instead of proposing: it writes `[Milk](check:14)` in its reply, and pressing that link shows the item's own manual screen with its normal controls.
- `Save` revalidates versions and domain rules, then inserts the Card in one short transaction.
- `Discard` creates no planning entity and the static result message records every proposed field.
- Multiple mutation tool calls become independent proposal screens in their original order.
- Mutation calls are prepared independently against committed data. Valid calls keep their original proposal order; unresolved calls return structured tool errors without cancelling valid siblings. After the current queue is resolved, the model retries only unfinished operations using IDs returned by saved proposals, for at most five repair rounds.
- After each Save, Discard, or failure, the same Telegram message advances to the next proposal.
- The model resumes only after the whole queue is resolved and receives all mutation and read-tool results.

Manual and AI flows call the same application/domain functions. Telegram handlers and model tools never mutate ORM objects directly.

## Card UI

The shared Card overview displays fields that are valid for its kind.

- Root Cards hide Parent. A non-root Card shows Parent as navigation to the parent Card.
- Goal and Idea show `Children`, which opens a paginated list of direct children.
- Changing parent is available through an AI proposal, not a manual selector.
- Action screens show effort, repeatability, categories, energy, and Blocked state.
- Goal and Idea hide all Action-only controls.
- Goal and Idea show recursively completed effort / total effort.
- Goal and Idea show direct Done children / total direct children.
- Cancelled descendant Action effort remains in total effort but is not completed effort.
- A direct child counts as completed only when its effective stage is Done.

## Architecture

Safwa is a Python 3.12 modular monolith under `src/safwa/`. It is organized as flat modules plus two packages, not as one package per layer. Responsibilities:

- Domain — Card invariants, hierarchy, stages, repeats, Values, Tags, Sprints, metrics, events, and Saved Requests: `domain.py`, `enums.py`, `models.py`, `saved_requests.py`.
- Application — transactions, AI proposals, reminders, and retrospective calculations: `domain.py` mutations, `ai/service.py` (`ProposalService`), `continuity.py`, `scheduler.py`, `analytics.py`.
- Infrastructure — SQLAlchemy, SQLite, provider clients, Telethon history, file memory, and plotting: `db.py`, `history.py`, `memory.py`, `ai/provider.py`, `ai/sql.py`, `backup.py`.
- `telegram/` — aiogram routing, item screens, callbacks, forms, pagination, semantic message classification, and generation synchronization. Layered internally: `_core` ← `_presentation` ← `_messaging` ← render modules ← handler modules.
- `ai/` — prompt/context construction, tool contracts, safe retrieval, and proposal compilation.
- Bootstrap — configuration, dependency wiring, startup recovery, background loops, and graceful shutdown: `main.py`, `config.py`, `constants.py`, `recovery.py`.

Every tuning constant — limits, budgets, caps, intervals, the effort scale — lives in `constants.py`, which imports nothing from Safwa. `config.py` takes its defaults from there and exposes the environment-overridable subset as `SAFWA_*` settings.

Use SQLite with SQLAlchemy 2, aiosqlite, WAL, foreign keys, a busy timeout, and optimistic entity versions. There is no write-serializing lock: concurrent writes rely on WAL plus the busy timeout, and correctness on the `version` / `workspace.revision` checks and the single foreground generation lease. Durable entities use incrementing integer primary keys. All persisted items use timezone-aware `created_at` and `updated_at` timestamps.

## Core persisted data

- `workspace`: mode, active Sprint, owner, timezone, and monotonic revision.
- `user_profile`: About Me, advisor instructions, schedule, reminders, provider settings, and optional capacity.
- `cards`: parent, kind, title, Note, manual/effective stage, priority, Hard Time, effort, repeat data, feedback, Blocked state/description, archive/terminal state, version, and timestamps.
- `values`, `tags`, and their Card junction tables.
- `checks` plus the `card_checks` junction table. A Check stores title, repeatability, outcome, first-resolution timestamp and actor, and its series lineage; its Cards live in the junction table, not in a column on the Check. Pending is `outcome IS NULL`, so no Pending state is written.
- Action category and energy junction tables.
- `sprints` and immutable `sprint_commitments`.
- `card_events` with actor, operation, snapshots, correlation, Sprint, and timestamp.
- `change_proposals` and `proposal_changes` for uncommitted AI mutations.
- `saved_requests` containing verified read-only SQL.
- `agent_runs` and `agent_steps` containing sanitized operational audit.
- `telegram_messages` containing Telegram IDs and semantic classifications, not duplicated persona text.
- feedback, summary, memory synchronization, reminder, job, UI session, and callback-token state.

There are no persistent unsaved-Card tables and no Card dependency table.

## Domain behavior

### Creation and updates

Every mutation runs in a short transaction and revalidates current state. Card creation requires:

- non-empty title and valid kind/stage;
- a valid parent relationship or root placement;
- parent and child consistency;
- Action effort from the allowed sequence;
- no Action-only fields on Goal or Idea;
- non-empty Blocked description when Blocked is true;
- existing referenced Values and Tags;
- no hierarchy cycle.

Invalid Action-only fields supplied for Goal or Idea are removed at the AI boundary and again at the domain boundary. Unknown stages map to Backlog during AI normalization; domain commands still reject invalid direct input.

### Hierarchy and progress

- Each Card has at most one parent.
- A subtree cannot cross an invalid kind relationship or create a cycle.
- Moving a populated Goal or Idea moves its subtree atomically.
- Stage propagation and ancestor recalculation occur in the originating transaction.
- Adding or reopening a live descendant reopens affected ancestors.
- When a parent becomes childless, its retained manual stage becomes effective again.
- Progress calculation loads the live tree once, finds recursive descendant Actions, and derives effort without storing duplicate totals.

### Sprint accounting

- Starting a Sprint snapshots all non-archived Actions in Sprint or Today.
- Goal and Idea never contribute their own effort.
- Later additions and removals are recorded separately from initial commitment.
- Cancellation is distinct from completion.
- Finish Early closes the Sprint; there is no pause.
- Unfinished Sprint/Today Actions remain preselected in Planning.
- Metrics distinguish initial, added, removed, completed, and cancelled effort. Remaining effort is not stored or reported; it is whatever a reader derives from those five figures.

### Archive and deletion

- Archive is reversible and preserves analytics/events.
- Archiving a parent includes its subtree.
- Archiving a Tag or Value removes current Card links atomically; archiving a Value also disables focus.
- Permanent deletion requires a second destructive confirmation.
- AI deletion is always a proposal plus destructive confirmation.

## AI advisor

LM Studio is the default OpenAI-compatible provider. Native function calling is preferred, with shallow tools:

- `query_safwa(sql)` for immediate read-only retrieval;
- `card(...)`, `check(...)`, `value(...)`, `tag(...)`, and `request(...)` for reviewed mutations;
- removal/archive operations through typed mutation tools.

The model never writes SQL for mutation. Mutation tools normalize into typed proposal changes. Read SQL is accepted only when it is one `SELECT` or `WITH ... SELECT` over allowlisted AI views, with no base tables, DML, DDL, PRAGMA, ATTACH, extensions, or multiple statements, and with strict time/row/column/payload limits.

AI context is one system message followed by the canonical dialogue turns. The system message contains:

- concise Safwa rules, the allowlisted view list, and the tool/approval protocol;
- current local time and workspace mode;
- About Me, advisor instructions, active Values, and available Tags, each with its short integer ID;
- all Today Actions with their short integer IDs;
- authoritative `memory.md`.

The system message deliberately carries no Sprint metrics and no precomputed Card candidates: the model reaches those through `query_safwa` over `ai_current_sprint_metrics` and `ai_cards`, so context stays small and never goes stale. The dialogue turns that follow already carry the nearest Summary or `/newsession` boundary applied by the history source. Tool schemas are supplied through the provider's native function-calling parameter, not inlined in the prompt.

## History and memory

The private Telegram conversation is canonical persona history. Telethon rereads it for each advisor turn. Include ordinary user dialogue, final generated Safwa replies, visible summaries, `/newsession`, and subsession results. Exclude menus, forms, proposal UIs, receipts, callbacks, SQL, tool traces, status/errors, reminders classified as operational, and retrospective PNGs.

- `/newsession <initial request>` or the nearest visible `📜 Summary` is required as the history boundary.
- A Summary boundary is followed by up to 20 older canonical messages with short timestamps, then newer dialogue.
- Summarization triggers around 10K unsummarized dialogue tokens.
- `data/memory.md` is authoritative, line-oriented persona memory and is limited to approximately 4K tokens.
- File edits synchronize at startup, before memory-backed prompts/maintenance, and through a five-second hash watcher.
- `/syncmem`, `/mem`, `/memory`, `/forget`, and `/setmemtime` provide explicit memory control.

Foreground generation holds a lease. New ordinary input deletes/invalidates the active interaction UI, cancels stale generation, and prevents an answer against obsolete dialogue or workspace state.

## Reminders and retrospectives

Reminder eligibility is deterministic before AI composition. Settings include timezone, wake/bed and quiet hours, proactive limits, cooldowns, weekends, snooze, capacity, About Me, and advisor instructions. Morning and evening check-in times are stored separately from wake/bed but have no command yet; until one exists, wake and bed times are the effective check-in windows. Today Actions never move automatically.

Retrospective PNGs use Matplotlib `Agg` and show three panels plus text:

- sprint effort bars: initial, added, removed, completed, and cancelled;
- committed versus completed effort by overlapping category;
- committed versus completed effort by overlapping energy type;
- written recommendations based on capacity, scope churn, Hard Time, Blocked work, active Values, and liked feedback.

## Fresh-schema delivery

Development currently assumes a fresh database. There are no migrations: startup creates the schema straight from the current SQLAlchemy metadata, so a fresh database always matches `models.py`. No compatibility migration is maintained for the removed unsaved-Card or dependency structures.

Verification covers:

- hierarchy, stage aggregation, recursive effort, direct-child progress, and ancestor reopening;
- Action-only dashboard filtering;
- transient manual Card creation and queued AI Card proposals;
- Blocked validation and repeat copying;
- Sprint accounting and retrospective PNGs;
- deterministic reminder candidates, their dedupe keys, and scheduler-loop survival;
- safe SQL and proposal version checks;
- canonical Telegram history, summaries, and generation synchronization;
- authoritative file memory and scheduled synchronization;
- live Safwa-QA Telegram interaction without the production bot.
