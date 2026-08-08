# Safwa: Personal Agile Telegram Advisor — Revised Implementation Plan

## 1. Locked product behavior

### Mandatory card-draft review

Every card creation uses the same draft-and-review workflow, whether initiated manually or by AI.

- An AI request such as “Create a new Action ‘Push ups 30 times’ and link it to the ‘To be fit’ Goal” creates only a persistent draft.
- The draft does not appear on Backlog, Sprint, Today, analytics, reminders, AI card views, or parent stage calculations.
- Safwa opens the normal card review UI with the AI-inferred fields prefilled.
- The card enters the planning system only after the user presses `Create`.
- The review screen is the approval for AI card creation; Safwa must not show a redundant proposal approval before it.
- Manual card creation uses the same draft review screen for consistent behavior.

The review UI shows:

- title and kind;
- Tags and parent;
- intended stage;
- Note;
- priority and Hard Time;
- effort points;
- repeatability;
- categories and energy types;
- linked Values;
- blockers/dependencies;
- all AI assumptions and unresolved fields.

Quick actions:

- `✏️ Title`
- `🏷 Tags`
- `🌳 Parent`
- `📍 Stage`
- `📝 Note`
- `⚠️ Priority`
- `⏱ Hard Time`
- `🔢 Effort`
- `🔁 Repeat`
- `🏷 Categories`
- `⚡ Energy`
- `💎 Values`
- `🚧 Blockers`
- `✅ Create`
- `🗑 Discard`

For the example:

- Kind: Action
- Title: Push ups 30 times
- Parent: uniquely matched `To be fit`
- Tags: optional direct links, independent of parent
- Stage: Backlog unless the message specifies Sprint or Today
- Priority: Medium
- Effort: unresolved unless confidently supplied or estimated; the user must select/confirm it before creation
- Other optional fields remain empty unless explicitly requested or confidently inferred

If `To be fit` has multiple matches, the draft shows `Parent: unresolved`, offers matching Goals as quick actions, and disables `Create`. If there is no match, the user may search, choose another parent, explicitly make the Action root-level, or discard the draft.

### Persistent memory correction

- `data/memory.md` is the authoritative persistent persona memory, not SQLite.
- It is UTF-8 with one non-empty fact per line.
- SQLite contains only a disposable mirror and synchronization metadata.
- Safwa imports local edits at startup, before every memory-backed prompt, before automatic memory maintenance, and through a five-second hash poll.
- AI memory writes use a temporary file plus atomic `os.replace`.
- A final hash check prevents overwriting a newer local edit.
- Missing file means intentional memory clear; empty file means empty memory.
- Invalid or over-4K files remain untouched, disable memory injection/writing, and produce a deduplicated owner warning.
- Automatic memory maintenance remains limited to approximately 4,000 estimated tokens.

### Other baseline decisions

- Single configured owner in a private Telegram chat.
- Local Windows hosting through long polling.
- Python 3.12 modular monolith under `src/safwa/`.
- Keep `telegram-bot-exampler/tg-ai-dialog` unchanged as a behavioral reference.
- Telegram quick actions and ordinary text are the v1 interfaces; Mini App is deferred.
- One global active Sprint with a shared personal card collection.
- Default Sprint length is 14 calendar days.
- SQLite, SQLAlchemy 2, aiosqlite, Alembic, WAL, foreign keys, busy timeout, and serialized writes.
- LM Studio is the default OpenAI-compatible provider.
- No arbitrary model-generated write SQL.
- No manual ranks. Live cards sort by Hard Time, Critical/Medium/Low priority, and creation time.
- Tags are optional and can be defined at any time.
- Today persists across midnight.

---

## 2. Architecture and data model

### Application boundaries

Implement a modular monolith:

- `domain`: Cards, hierarchy, stages, repeats, Values, dependencies, Sprints, metrics, and events.
- `application`: typed commands/queries, transactions, drafts, AI proposals, UI intents, reminders, and retrospective calculations.
- `infrastructure`: SQLAlchemy, Alembic, SQLite AI views, LM Studio/OpenAI-compatible clients, Telethon, file memory, scheduler, and plotting.
- `telegram`: aiogram routing, screens, callback tokens, forms, pagination, rendering, and generation guard.
- `ai`: context construction, response validation, retrieval, read-only query execution, proposal compilation, persona, summary, and memory extraction.
- `bootstrap`: configuration, migrations, dependency wiring, startup recovery, background loops, and graceful shutdown.

Telegram handlers and AI operations must call the same application services. Neither may mutate SQLAlchemy entities directly.

### Core types

Enums:

- `WorkspaceMode`: Planning, Sprint
- `CardKind`: Goal, Idea, Action
- `CardStage`: Backlog, Sprint, Today, Done, Cancelled
- `Priority`: Critical, Medium, Low
- `Category`: Self, Contribution, Work, Rest
- `EnergyType`: Physical, Cognitive, Social, Values
- `ActorType`: User UI, User Text, AI, System
- `DraftStatus`: Editing, Ready, Reviewed, Committed, Discarded, Expired
- `ProposalStatus`: Pending, Approved, Rejected, Stale, Failed
- Semantic Telegram `MessageKind`
- Typed `UiIntentType`

Use UUIDs for durable entities. Telegram callback data contains only a short opaque token mapped to persisted server-side callback state.

### Database model

Core tables:

- `workspace`
  - mode, active Sprint, Telegram owner, timezone, monotonic revision.
- `user_profile`
  - About Me, Advisor Instructions, schedule, reminder policy, provider configuration, optional capacity.
- `tags`
  - name, description, archive state, version.
- `values`
  - name, description, active-focus flag, archive state, version.
- `cards`
  - parent, kind, title, Note, manual/effective stages, priority, Hard Time, effort, repeat fields, liked feedback, archive and terminal state, optimistic version.
- Junction tables for direct Values, Action categories, and Action energy types.
- `card_dependencies`
  - blocked card, blocker card, repeat-copy flag.
- `sprints`
  - sequence number, planned dates, actual dates, capacity, status, finish reason.
- `sprint_commitments`
  - immutable effort snapshot and initial/added/removed/completed/cancelled classification.
- `card_events`
  - actor, operation, before/after snapshot, correlation ID, Sprint, timestamp.
- `card_draft_bundles`
  - origin, active draft, status, creation/expiry metadata.
- `card_drafts`
  - normalized draft fields, temporary parent-draft reference, existing-parent reference/version, field provenance, validation errors, reviewed timestamp.
- Draft junction tables for Values, categories, energy types, and dependencies.
- `change_proposals` and `proposal_changes`
  - AI changes to already committed entities.
- `agent_runs` and `agent_steps`
  - sanitized internal query/operation audit.
- `telegram_messages`
  - Telegram IDs and semantic classifications without persona text duplication.
- `feedback_queue`
- `summary_state`
- Disposable `memory_fact_cache` and `memory_sync_state`
- `reminder_state`, `scheduled_jobs`, `ui_sessions`, and short callback-token storage.

### Draft isolation

Drafts are persisted so they survive restart, but remain outside the planning domain:

- Draft tables have no effect on Card queries, hierarchy, parent stages, Sprint metrics, reminders, retrospectives, or FTS card search.
- Committed Card IDs are allocated only during final draft commit.
- Existing parent, Tag, Value, and dependency references store expected versions.
- Draft changes do not increment workspace revision.
- Draft commit increments workspace revision and emits normal Card events.
- Drafts are visible through a `Drafts N` navigation action.
- Drafts do not expire silently while actively edited. Inactive drafts expire after a configurable period, default seven days, but remain recoverable until explicitly discarded or cleanup retention passes.
- Ordinary text received while a draft exists does not discard it. Safwa may interpret phrases such as “make it repeatable” as a proposed draft edit when the reference is unambiguous.
- Draft content and review screens are operational UI and excluded from persona history.

### Card-draft validation and commit

A draft cannot become Ready until:

- title is non-empty;
- kind is valid;
- linked Tags exist and are active;
- parent relationship is valid or explicitly root;
- Action effort is one of `1, 2, 3, 5, 8, 13`;
- Goal/Idea do not have Action-only effort, repeatability, categories, energy, or liked feedback;
- stage is valid for current workspace mode;
- dependencies and parent references are acyclic;
- referenced Values and dependencies still exist;
- no required AI-resolved reference remains ambiguous.

When the user presses `Create`:

1. Acquire a short write transaction.
2. Re-read the draft and every referenced committed entity.
3. Compare stored versions and workspace state.
4. If stale, keep the draft and return to review with highlighted fields.
5. Re-run all domain validation.
6. Insert the Card and junction rows.
7. Apply stage propagation and active-Sprint scope accounting.
8. Write domain events and audit correlation.
9. Mark the draft Committed and store the new Card ID.
10. Commit and open the resulting Card detail.

For one Card, `Create` is both review confirmation and AI-write approval.

### Multi-card AI creation

When AI suggests multiple Cards:

- Create a persistent draft bundle with temporary draft IDs.
- Allow draft-to-draft parent links, such as a new Goal with new child Actions.
- Present `Card 1/N`, with `Previous`, `Next`, `Discard this`, and field actions.
- Each draft must be opened and marked Reviewed.
- Only after every remaining draft is valid and reviewed does `Create all N` become available.
- Commit the complete bundle atomically so parent-child references cannot partially fail.
- The user may remove unwanted drafts before final commit.
- A batch commit uses the same domain validation, stage propagation, Sprint accounting, and audit behavior as individual creation.

### Card hierarchy

- Cards may link to any number of defined Tags.
- A Card has at most one parent.
- Goal is root-only.
- Idea may be root or directly under Goal.
- Action may be root or directly under Goal or Idea.
- Action cannot have children.
- Moving a subtree does not alter its Tags.
- Parent and dependency cycles are prohibited.
- Dependencies are warning-only.
- Done satisfies a dependency; Cancelled does not.
- Values may link directly to any Card.
- A parent’s effective Values are the union of its own and its descendants’ direct links.
- Categories, energy types, effort, liked feedback, and repeatability belong only to Actions.
- Action effort is mandatory.
- Priority defaults to Medium; Hard Time is an independent boolean.

### Stage aggregation

Store a retained `manual_stage` and materialized `effective_stage`.

- Actions and childless Goal/Idea Cards use manual stage.
- Populated Goal/Idea Cards derive effective stage from descendants.
- Live precedence: Today, then Sprint, then Backlog.
- A parent becomes Done when all descendants are terminal and at least one is Done.
- It becomes Cancelled only when all descendants are Cancelled.
- Adding or reopening a live descendant reopens and recalculates ancestors.
- If a parent becomes childless, its retained manual stage becomes effective again.
- Moving a populated Goal/Idea means moving its subtree.
- All propagation occurs within the originating transaction.

### Repeatable Actions

Completing or cancelling a repeatable Action:

1. Leaves the current instance terminal.
2. Creates a successor in the same transaction.
3. Uses the prior live stage for the successor.
4. Copies title, Note, parent, priority, Hard Time, effort, categories, energy types, Values, Tags, and repeat series.
5. Clears liked feedback and terminal timestamps.
6. Copies only dependencies marked reusable for repetition.
7. Counts the successor as added Sprint scope when appropriate.
8. Recalculates ancestors once from the final state.

Done Actions enter the feedback queue. Cancelled Actions do not.

### Sprint accounting

- Only one Sprint may be active.
- Sprint starts only from Planning.
- Start snapshots all non-archived Actions in Sprint or Today.
- Goal/Idea Cards do not contribute their own effort.
- Later additions are scope added.
- Moving work out records scope removed.
- Cancellation is distinct from completion.
- Finish Early closes the Sprint; there is no pause.
- Unfinished Sprint/Today Cards remain preselected during the next Planning phase.
- A later Sprint receives new immutable snapshots.
- Metrics distinguish initial commitment, additions, removals, cancellations, completion, and remaining work.
- Capacity and blockers create warnings only.

### Archive and deletion

- Archive is reversible and preserves analytics and events.
- Parent archive includes its subtree.
- Permanent deletion requires a second destructive confirmation.
- It removes the subtree and associated historical contribution, then recalculates affected metrics and ancestors.
- AI deletion remains a normal proposal plus strong destructive confirmation.

---

## 3. AI advisor and operations

### Provider abstraction

Expose chat completion with:

- configurable OpenAI-compatible base URL;
- model and API key;
- timeouts and output limits;
- structured-output capability flags;
- normalized usage/errors;
- conservative retry policy.

LM Studio is the default. Native tool calling is optional rather than required.

### Model response envelope

Use one shallow schema:

```json
{
  "kind": "answer | query | clarification | proposal",
  "message": "natural user-facing response",
  "sql": "optional read-only SELECT",
  "changes": [
    {
      "entity": "card | tag | value | sprint | settings",
      "action": "create | update | move | complete | cancel | reopen | archive | delete | link | unlink | start | finish",
      "id": "optional ID",
      "values": {}
    }
  ]
}
```

- Answer: no SQL or changes.
- Query: one read-only query.
- Clarification: no operation.
- Proposal: normalized changes.
- Unknown identifiers or fields fail closed.
- Allow one JSON repair attempt.
- Permit at most two read-query iterations.

### AI-created Card routing

`entity=card, action=create` is special:

- Translate it into `CardDraft` data, never directly into a domain Card command.
- Resolve Tags, parents, Values, and dependencies by ID or candidate matching.
- Store confidence/provenance for each inferred field.
- Open the card review UI.
- Do not create a general AI proposal requiring separate approval.
- The final `Create`/`Create all` action is the mandatory approval and commit.
- If AI mixes creation with updates to existing Cards, split the experience:
  - create draft bundle for new Cards;
  - create a separate approval proposal for existing-entity mutations;
  - clearly show that neither operation implies approval of the other.

Other AI writes retain the proposal flow:

1. Normalize without mutation.
2. Store expected workspace/entity versions.
3. Show a human-readable preview.
4. Offer Approve/Edit/Cancel.
5. Re-read versions in a short transaction.
6. Apply through domain services.
7. Validate, audit, and commit.
8. Render results without SQL or JSON.

### Retrieval and analytics

Context includes:

- current local time;
- profile and Advisor Instructions;
- active Values;
- `memory.md`;
- visible summary;
- recent canonical dialogue;
- all Today Cards with IDs;
- condensed Sprint state;
- FTS-selected candidate Cards;
- pending draft context when relevant.

Expose safe AI views:

- `ai_cards`
- `ai_boards`
- `ai_values`
- `ai_current_sprint`
- `ai_current_sprint_metrics`
- `ai_card_events`

Run model SQL using a dedicated read-only SQLite connection:

- one `SELECT` or `WITH ... SELECT`;
- only allowlisted views/functions;
- no base tables, DML, DDL, PRAGMA, ATTACH, extensions, or multiple statements;
- strict timeout, row, column, and payload limits;
- results remain ephemeral to the current AI run.

---

## 4. Persona, summaries, and file-backed memory

### Canonical Telegram history

Use Telethon to reread the private bot conversation.

Include:

- ordinary user dialogue;
- final natural Safwa responses;
- persona-style reminders;
- visible summaries.

Exclude:

- commands and callbacks;
- card drafts and review screens;
- form answers and selection messages;
- dashboards, settings, approvals, and operation receipts;
- SQL, JSON, query results, and agent traces;
- errors/retries/status;
- retrospective PNGs.

SQLite stores message IDs and semantic classifications, not duplicated persona message bodies.

### Summarization

- Trigger after approximately 10,000 unsummarized dialogue tokens.
- Target 1,500–2,000 tokens.
- Preserve personal reflections, decisions, intentions, reasons, emotions, advice, and unresolved conversational topics.
- Exclude current Card/Sprint state and operations.
- Send the summary as a visible `📜 Summary` message.
- Store its Telegram boundary metadata.
- If summary generation fails, retain safely truncated recent dialogue and retry later.

### `memory.md`

Memory maintenance:

1. Read new canonical dialogue since the last processed boundary.
2. Split into roughly 2K-token chunks with 500-token overlap.
3. Retell durable observations.
4. Generate small insert/replace/delete/ignore operations.
5. Keep preferences, routines, constraints, motivations, recurring difficulties, relationships, energy patterns, and planning lessons.
6. Reject transient Card/Sprint state, internal operations, and deadlines.
7. Lock memory, synchronize the current file, and calculate the new fact list.
8. Recheck file hash.
9. Keep automatic results within 4K.
10. Atomically replace `data/memory.md`.
11. Reimport its lines into the disposable mirror.
12. Advance dialogue processing only after successful file and mirror synchronization.

Structured profile settings and Values always override inferred memory.

### Context budget for a 35K model

Approximate allocation:

- persona/runtime: 3K;
- profile and active Values: 2K;
- memory: 4K;
- summary: 2K;
- recent dialogue: 10K;
- planning state and candidates: 5K;
- temporary query results: 3K;
- output: 5K;
- safety margin: 1K.

Trim query rows, older dialogue, then low-confidence candidates. Never trim current input, system rules, pending draft IDs, proposal versions, or required entity identifiers.

### Generation synchronization

- Acquire a foreground generation lease before context construction.
- Use typing status but no streaming.
- While foreground generation is active, delete and ignore new ordinary messages and reject callbacks except Cancel.
- Before sending, verify generation ID, source message, dialogue revision, and workspace revision.
- Discard stale output.
- If deletion fails, cancel the old generation and reconcile the surviving input.
- Background memory/reminder work is preempted rather than deleting new input.
- A pending Card draft is not an active generation and remains available while the user continues chatting.

---

## 5. Telegram UX, reminders, and retrospectives

### Navigation

Primary actions:

- Today
- Sprint / Planning
- Backlog
- Add
- Drafts
- Values
- Advisor
- Retrospective
- Settings

Use inline keyboards and commands to prevent navigation from becoming persona dialogue.

- Five Cards per page.
- Card rows show kind, title, priority, Hard Time, effort, blockers, and repeat state.
- Card detail supports move, complete/cancel, edit, parent, Values, dependencies, archive, and delete.
- Add opens manual draft creation.
- AI-generated drafts automatically open the same review UI.
- Draft review remains resumable after navigation or restart.
- Planning displays selected effort, capacity, and warnings.
- Sprint displays initial/additional scope, completion, cancellation, and remaining effort.
- Today exposes `Feedback N` when feedback is pending.

### UI-hook registry

Prioritize:

1. destructive confirmation and approval;
2. draft review and required clarification;
3. post-operation feedback;
4. warnings;
5. reminders.

Hooks include:

- Card draft created, draft incomplete, ambiguous reference, draft stale, draft ready, draft committed, draft discarded, and batch review progress.
- AI update approval, destructive action, stale proposal, dependency/capacity warning.
- Multiple/no Card match and parent/Tag/Value/dependency selectors.
- Completion feedback, repeat successor, ancestor changes, parent reopen, archive undo, operation error.
- Sprint start/finish, early finish, carryover, Planning required, midpoint/end.
- Morning/evening check-in, stale Today work, pending feedback, capacity risk, neglected Value, repeat drift, inactivity, and incomplete Planning.
- Provider failure, invalid model response, database conflict, memory error, and Telegram delivery failure.

### Completion feedback

- Batch completion commits first.
- Queue every applicable Done Action.
- Show `Feedback 1/N` with strict Yes/No.
- Callbacks are idempotent.
- Queue survives restart.
- Ignoring feedback blocks nothing.
- Cancelled Actions and derived parent completion produce no feedback.

### Reminders

Configurable:

- timezone, default Europe/Istanbul;
- wake/bed and quiet hours;
- morning/evening windows;
- reminder types;
- daily proactive limit;
- cooldowns;
- weekend behavior;
- snooze;
- capacity;
- About Me;
- Advisor Instructions.

A deterministic policy selects eligible candidates before AI. AI may compose one concise message or send nothing. New user input preempts background generation. Today Cards never move automatically.

### Retrospective PNGs

Generate in memory using Matplotlib `Agg`:

- initial commitment versus completed effort;
- added/removed/cancelled/remaining scope;
- effort by category;
- effort by energy type;
- written recommendations using capacity, scope churn, Hard Time, blockers, active Values, and liked feedback.

Multi-category/energy Cards contribute their complete effort to every selected dimension, with charts labeled as overlapping. PNG messages remain outside persona history.

---

## 6. Implementation order and acceptance

### Delivery phases

1. Scaffold Python project, configuration, logging, database, migrations, startup recovery, and default Inbox.
2. Implement domain invariants, hierarchy, stages, repeats, Values, dependencies, archive/delete, and Sprint accounting.
3. Implement persistent Card drafts, shared review UI contract, validation, single/batch commit, and stale-reference recovery.
4. Implement Telegram dashboards, card forms, draft review, callbacks, feedback, Values, Planning, and Sprint.
5. Implement LM Studio provider, context builder, FTS candidates, safe SQL, AI draft generation, AI update proposals, and audit.
6. Implement Telethon canonical history, semantic filtering, summaries, context budgeting, and generation guard.
7. Implement authoritative `memory.md`, external-edit synchronization, atomic updates, 4K limit, and manual memory UI.
8. Implement reminders, retrospective PNGs, diagnostics, backup/restore, and Windows documentation.
9. Harden concurrency, restart behavior, security, provider failure handling, and full end-to-end tests.

### Required draft tests

- Manual Card creation always reaches review before insertion.
- AI Card creation creates a draft and no Card row.
- Draft is absent from dashboards, stage aggregation, search, metrics, reminders, and retrospectives.
- Example “Push ups 30 times” resolves the unique `To be fit` Goal and opens review.
- Multiple matching parents disable Create and show selectors.
- Missing parent does not silently create a root Action.
- Action effort is confirmed before commit.
- Quick-action edits update only the draft.
- Restart restores the review screen and draft data.
- New ordinary dialogue does not discard a draft.
- Existing parent/version changes cause stale review instead of incorrect commit.
- Single Create atomically inserts and propagates the new Card.
- Multi-card bundle requires every draft to be reviewed.
- Draft-to-draft parent relationships resolve correctly at atomic batch commit.
- Discarding one batch draft correctly repairs dependent draft validation.
- No duplicate approval is shown for AI creation.
- AI creation mixed with existing-Card updates produces separate draft review and mutation approval.
- Discarded/expired drafts never produce Card events or analytics.

### Broader verification

Test:

- all hierarchy and cycle rules;
- stage precedence and terminal aggregation;
- ancestor reopening;
- repeat cloning and feedback;
- Sprint snapshots, carryover, additions, removals, cancellations, and finish early;
- safe SQL rejection and timeouts;
- proposal approval and stale versions;
- message-history inclusion/exclusion;
- 10K summaries;
- foreground input deletion and stale-output prevention;
- external `memory.md` edits and crash recovery;
- reminder anti-spam behavior;
- overlapping retrospective categories/energy types;
- backup and restore on Windows.

### Final acceptance criteria

Safwa v1 is ready when:

- every Card creation, especially AI creation, is a non-domain draft until explicitly reviewed and confirmed;
- the AI can prefill a complete, editable review screen without bypassing user judgment;
- UI and AI use identical domain validation;
- ordinary text can search, analyze, and propose operations without model-generated write SQL;
- all existing-entity AI writes are approved and version-checked;
- Telegram is the canonical persona history;
- summaries trigger at 10K;
- `memory.md` is authoritative, automatically synchronized, and limited to 4K;
- foreground responses cannot be sent against stale dialogue or task state;
- reminder and retrospective behavior is deterministic, restart-safe, and excluded from persona history where appropriate;
- the complete application runs locally on Windows without a public domain or Mini App.
