# Refactoring findings — cards, tags, values, dashboards

Scope of this review: the parts treated as complete (Cards, Tags, Values, dashboards, the
AI proposal path that mutates them). Memory, continuity, reminders/scheduler and retrospectives
were **not** reviewed.

Reviewed: `domain.py`, `telegram.py`, `ai/service.py`, `ai/contracts.py`, `ai/sql.py`,
`saved_requests.py`, `models.py`, `enums.py`, `recovery.py`.

Status legend: **[fixed]** done in this pass · **[open]** left deliberately, reason given.

Current state: `ruff check .` clean, `pytest` **130 passed, 2 skipped**
(the suite was 5 failed / 103 passed before any of this work).

Regression tests added for every behavioral fix below:
`test_reopening_a_finished_action_clears_its_sprint_result`,
`test_returning_to_sprint_scope_cancels_the_earlier_removal`,
`test_an_action_cannot_reach_a_terminal_stage_through_move`,
`test_card_move_tool_rejects_a_terminal_stage`,
`test_card_field_updates_reject_an_unknown_priority`,
`test_startup_releases_an_interrupted_agent_continuation`,
`test_moving_a_blocked_card_shows_its_warning_on_the_card_screen`,
`test_ai_goal_proposal_reports_a_parent_instead_of_dropping_it`,
`test_ai_stage_update_to_done_keeps_completion_accounting`, plus an index assertion in
`test_alembic_bootstraps_new_database`; and for the structural pass
`test_every_inline_button_action_has_a_registered_handler`,
`test_no_individually_registered_handler_is_unreachable`,
`test_every_card_relationship_is_wired_to_both_selector_surfaces`,
`test_dashboard_paging_walks_between_pages`.

---

## 0. Baseline and contradictions

### Test suite was red before any change

`uv run pytest -q` → **5 failed, 103 passed, 2 skipped**. All five failures share one cause:
commit `288a684 v1.12 added emojis` changed the Action kind emoji to `⭐️`
(`_KIND_EMOJIS`, `telegram.py`) but `tests/test_telegram_item_ui.py` still asserts `✅ Action`.

Resolution: the code is right, the tests were stale. `✅` is already the Save/Create/focus-on
marker and the selected-item prefix, so reusing it for the Action kind produces `✓ ✅ Action`
in choosers. Tests updated to `⭐️`. **[fixed]**

### Contradictions between tests, docs and code

1. **Emoji vocabulary is specified nowhere.** Neither `INITIAL_PLAN.md` nor
   `MEMORY_HISTORY_USAGE.md` mentions kind/category/energy emojis, so the tests were the only
   specification and they drifted silently. Worth one line in `INITIAL_PLAN.md` under "Card UI".
2. **`tests/test_domain.py` carries a dead API contract.** Its local `create_card` helper does
   `payload.pop("root_confirmed")` and `payload.pop("expected_parent_version")`, and callers still
   pass both. `domain.create_card` has never had those parameters in this tree — the helper exists
   only to swallow them. Vestigial from a removed explicit-parent-confirmation API. **[open]** —
   removing them touches assertions unrelated to this review; flagged for a test cleanup pass.
3. **`MEMORY_HISTORY_USAGE.md:22` promises a consolidated result list.** "After the last item, the
   same message shows a consolidated Saved/Discarded/Failed result list." What is implemented is
   `display_result_summaries` prepended to the *model's* next answer, not a dedicated final screen.
   Close enough in effect, but the doc describes a UI element that does not exist as such. **[open]**
4. **`MEMORY_HISTORY_USAGE.md:18` says AI proposal screens show "the complete Card, Tag, or Value
   overview".** Requests (`entity == "request"`) get no item-style screen — they fall through to the
   raw `proposal_change_summary` dump (`Create Request: name='…', query_sql='SELECT …'`).
   Either the doc should include Requests or `render_proposal` should. **[open]**
5. **`INITIAL_PLAN.md:64-68` describes `application`/`infrastructure`/`bootstrap` layers that do not
   exist** as modules; the code is flat (`domain.py`, `telegram.py`, `ai/*`, `main.py`). Harmless,
   but it misleads anyone using the doc as a map. **[open]**
6. **`INITIAL_PLAN.md:85` lists "job" state as persisted data.** `ScheduledJob` exists and is only
   ever touched by `recover_startup`, which resets rows nothing creates. Kept because the doc
   claims it. **[open]**

---

## 1. Bugs

### High — lifecycle bypass corrupts completion and Sprint accounting

- **AI `move` could complete a Card without any completion bookkeeping.** `contracts.py` rejected
  terminal stages for `mode="edit"` but not for `mode="move"`, whose `stage` Literal still included
  `done`/`cancelled`. `ProposalService.apply` then sent `move` straight to `move_card`, which sets
  `effective_stage` without `completed_at`, without `FeedbackQueue`, without
  `SprintCommitment.result` and without a repeat successor. The `update` branch already routed
  terminal stages to `finish_action`; `move` was the outlier. **[fixed]** — `move` and `reopen` now
  reject terminal stages at the contract, and all three apply paths (`move`, `reopen`, `update`)
  share one `_apply_stage_change` helper that routes terminal → `finish_action`.
- **`move_card` accepted terminal stages for Actions at all.** Two functions could put an Action in
  `done`, only one did the accounting. **[fixed]** — `move_card` now rejects terminal stages for
  Actions and names `finish_action` in the error. Childless Goal/Idea terminal moves are unchanged.
- **Reopening a completed Action left it counted as completed.**
  `_sync_commitment_for_stage(previous=DONE, current=BACKLOG)` matched neither branch
  (`in_scope` False, `was_scope` False because `DONE ∉ {SPRINT, TODAY}`), so
  `SprintCommitment.result` stayed `"done"` and `sprint_metrics` kept counting it.
  **[fixed]** — leaving a terminal stage clears `result`.
- **A Card removed from Sprint scope and put back stayed counted as removed.** Same function:
  `removed_at` was never cleared, so the effort appeared in both `removed` and the live selection.
  **[fixed]** — re-entering `SPRINT`/`TODAY` clears `removed_at`.

### High — agent approval queue durability

- **`_pending_batch_for_target` loaded every `approval_batch` row ever written**, unbounded and
  unindexed, then filtered in Python — up to three times per proposal callback
  (`has_pending_approval`, `resolve_approval`, `cancel_approval_for_target`). **[fixed]** — the
  status filter moved into SQL via `json_extract`, bounded with a `LIMIT`, and `agent_steps.kind`
  is now indexed (migration `0002`).
- **A crash mid-continuation wedged a proposal forever.** `metadata["status"]` is set to
  `"resuming"` before the provider call; `recover_startup` resets `AgentRun` and stale proposals but
  never touched `AgentStep`, so a batch stuck in `resuming` kept intercepting that proposal's
  callbacks after every restart. **[fixed]** — startup recovery closes `resuming` batches as
  `completed` with a `continuation_error` marker, so Save/Discard behaves normally again.
- **`AgentStep.position` collided.** `_materialize` used `max(existing, default=0)` with no `+ 1`,
  so the `approval_batch` step always reused the last `read_query` step's position. **[fixed]**

### High — an interrupted request told the model something untrue

- **Continuing the conversation mid-queue erased the record of what had already been saved.**
  With several proposals queued in one request, saving the first few and then typing a new message
  froze the open screen as *"Proposal discarded — You continued the conversation without saving
  it"*, listing only the last proposal's fields. That frozen screen is registered as
  `DIALOGUE_ASSISTANT`, so it becomes canonical assistant history: the model was told the request
  was discarded while Cards from the same request existed in the database. Reported from use.
  **[fixed]** — `cancel_approval_for_target` now returns the consolidated result of the interrupted
  request (the same summary the resume path builds), and `dismiss_prior_ui` freezes the screen as
  *"Request interrupted"* followed by the per-item Saved/Discarded/Failed list. The old text remains
  as the fallback for a proposal that belongs to no suspended batch.
  Covered by `test_new_message_discarding_a_queue_reports_what_was_already_saved`; the
  Discard-button path was verified already correct by
  `test_discarding_the_last_queued_proposal_still_reports_saved_siblings`.

### Medium — UI

- **Blocked warnings were never visible.** In a callback, `send_registered` edits the bot's message
  in place, so `card_move`/`card_finish` sent `⚠️ Blocked: …` and then immediately overwrote it with
  the next render in the same handler. **[fixed]** — `render_card` and `render_feedback` take a
  `notice`, and the warning is rendered as part of the destination screen.
- **Dead-end screens.** `card_restore`, `card_delete_confirm`, `sprint_start` and `sprint_finish`
  replaced the current screen with a receipt carrying no buttons at all. **[fixed]** — all four get
  `menu_row()`.
- **`render_card` returned silently when the Card was gone**, so a stale button did nothing;
  `render_children` raises `DomainError` for the same case. **[fixed]** — now raises, and the
  callback error handler reports it.
- **The text-prompt Back button lost navigation context.** `card_edit_text` passed `{"id": …}`
  without `back_state`; the blocked-description prompt 60 lines later did it correctly. **[fixed]**
- **`card_choose_tags` used `✅` as its selected marker** where every other selector uses `✓`.
  **[fixed]**
- **`render_saved_request` mis-reported truncation.** `cards[:25]` then `if len(cards) == 25`
  claimed "showing first 25" for exactly 25 matches. **[fixed]**
- **Archive diffs mislabelled an already-archived item as `Active`.** **[fixed]**

### Medium — layering and integrity

- **The `settings` proposal branch bypassed the domain boundary**, doing raw `setattr` on
  `UserProfile` (dodging `update_profile`'s allowlist), dereferencing a possibly-`None` profile, and
  appending the string `"settings"` into `affected: list[int]`. It was also unreachable — no tool
  emits that entity. **[fixed]** — deleted together with the equally unreachable `sprint` branch.
- **A Goal proposal silently dropped `parent_id`/`parent_query`** instead of erroring, so the review
  screen showed no parent change and the model was never told. **[fixed]** — now a
  `ToolPreparationError` the model can act on.
- **`update_card_fields` never validated `priority`** against the enum, unlike `create_card`. Safe
  only because every caller happened to be validated upstream, while
  `render_dashboard`'s `priority_order[c.priority]` is a bare `KeyError` if that ever stops holding.
  **[fixed]**
- **`edit_card_text` could store a `blocked_description` on an unblocked Card**, where
  `update_card_fields` clears it. Two write paths, two rules. **[fixed]** — both clear now.
- **`Card.source_instance_id` had no `ondelete`**, so permanently deleting a repeat predecessor
  whose successor lives outside the subtree hits an FK violation under `PRAGMA foreign_keys=ON`.
  **[fixed]** — `ondelete="SET NULL"` (fresh databases; see the `0002` migration note).
- **`start_sprint` derived the number from `count(Sprint)`**, colliding with the unique constraint
  after any Sprint deletion. **[fixed]** — uses `max(number) + 1`.
- **Saved Request SQL runs with weaker protection than `query_safwa`.** `request_cards` executes the
  stored statement on the read-write async session: regex validation only, no `set_authorizer`, no
  `mode=ro` connection, no row or time cap — unlike `ReadOnlyQueryRunner`, which has all four for
  the same grammar. **[open]** — fixing it properly means routing Request execution through the
  query runner, which changes `request_cards`' signature and its tests. Next pass.

### Low — performance and types

- **`card_progress` loads every non-archived Card in the database, per call** — once per Goal/Idea
  render and again per proposal display. A recursive CTE would fix it. **[open]** — fine at
  single-owner scale; the change deserves its own test.
- **`effective_value_ids`, `move_card` and `archive_subtree` recurse with one query per node.**
  **[open]**, same reasoning.
- **`propagate_ancestors` and `archive_subtree` annotated `list[str]` while appending `int`.**
  **[fixed]**

---

## 2. Dead code

Verified unreachable, not merely unused-looking.

- **Proposal editing, ~50 lines. [fixed — deleted]** `render_proposal` emits only Save and Discard,
  so nothing could ever produce the `proposal_edit` action. `render_proposal_edits`, the
  `proposal_edit` handler and `proposal_remove_change` were all orphaned by
  `2137853 v1.9 AI proposals shows only Save/Discard` — the removal was not finished. Deleting them
  is what `INITIAL_PLAN.md:35` already requires ("fields cannot be edited inside AI review").
- **`value_toggle` callback handler. [fixed — deleted]** No `token_button` emits it; the Values list
  uses `item_view` and focus goes through `item_toggle_focus`.
- **`ProposalService` `sprint` and `settings` branches. [fixed — deleted]** `MUTATION_TOOL_MODELS`
  is card/value/tag/request/remove, so no tool can emit those entities. `AgentChange`'s `entity` and
  `action` Literals narrowed to match (`"sprint"`, `"settings"`, `"start"`, `"finish"` removed).
- **`UiIntentType`. [fixed — deleted]** A 9-member enum with zero references outside its definition.
- **`ActorType.USER_TEXT`. [fixed — deleted]** AI writes use `AI`, UI writes use `USER_UI`.
- **`"clarification"` in `render_ai_outcome`. [fixed]** `AIOutcome.kind` is only ever `"answer"` or
  `"proposal"`, and a `proposal` outcome always carries a `proposal_id`, so both the
  `"clarification"` case and the trailing fallback were unreachable.
- **No-op branch in `dismiss_prior_ui`. [fixed — deleted]**
  `elif screen.related_id in resolved_proposals: replacement = None`, where `replacement` is
  already `None`.
- **Unused local in the `feedback` handler. [fixed]** `card = await set_feedback(...)`. Ruff's F841
  cannot see it because `card` is bound in dozens of other branches of the same 850-line function —
  a concrete cost of the monolithic handler.
- **`_reference_items` and `_items` were the same function in the same file. [fixed]** Merged.
- **Garbled docstring in `render_card_choices`. [fixed]** "route every mutation through the domain
  layer while remaining mutation through the domain layer."
- **`Card.source_instance_id` is written and never read.** **[open]** — kept because
  `INITIAL_PLAN.md:76` lists "repeat data" as persisted; decide whether it is audit data to keep or
  a column to drop.
- **`effective_value_ids` has no production caller** — only `tests/test_domain.py`. **[open]** —
  deleting it would delete tested domain behavior; either surface it in the Card UI or drop both.
- **`OperationResult.card_ids` is never read; `ancestor_ids` is assigned and never read.** **[open]**
  — only `warnings` (and `successor_ids`, in tests) are consumed.

---

## 3. Overengineering

### Done in this pass

- **One-proposal-per-tool-call had full multi-change plumbing. [fixed]** `_materialize` always calls
  `_create_proposal` with a single tool, so every proposal holds exactly one `ProposalChange` at
  `position=0`. That made three things dead by construction: the enumerate/`prepared_changes` loop,
  and the `created_tag_ids`/`created_value_ids` dicts threaded through all of
  `ProposalService.apply` — a cross-change mechanism that can never fire inside a single-change
  proposal. `_create_proposal` now takes one `PendingTool`; the `created_*` plumbing is gone.
  Cross-*proposal* references still work, because `_named_ids` resolves names from the database
  after the earlier proposal has been applied — which is what
  `test_query_then_link_continuation_can_suspend_for_a_second_queue` exercises.
  This does not weaken `INITIAL_PLAN.md:38`: multiple tool calls still become multiple independent
  proposals in their original order.
- **Three stage-change code paths collapsed into `_apply_stage_change`. [fixed]** (see Bugs.)

### Second pass — structural work

1. **`callback_token_handler` was one ~850-line `if`/`elif` chain. [fixed]** Every branch is now a
   named `_on_*(context)` handler behind a `CALLBACK_ACTIONS` registry, with a `CallbackContext`
   carrying the callback, services, action and payload. The dispatcher only claims the token,
   looks up the handler and owns the three error policies, which now live in two named helpers
   (`_report_callback_failure`, `_resume_failed_approval`) instead of being inlined three times.
   An unknown action used to do nothing at all; it now logs and tells the owner to reopen the screen.

   Two guard tests came out of this and immediately paid for themselves:
   `test_every_inline_button_action_has_a_registered_handler` (every literal `token_button` action
   resolves) and `test_no_individually_registered_handler_is_unreachable` (every hand-written
   registry key is referenced by a button). The second one caught **`proposal_view`**, which lost its
   only emitter when `render_proposal_edits` was deleted in the first pass — now removed too.
2. **The two choice-screen builders are unified. [fixed]** `_choice_options` is the single option
   catalogue for all eight selectors and `_choice_rows` applies the `✓` marker once; the draft and
   committed screens keep only their own selection source and callback payloads. A `_RelationChoice`
   table describes each overlapping relationship once (payload key, link table, toggle command,
   parser), which also collapsed the four near-identical `_on_card_toggle_*` handlers into one and
   made the draft/committed action names derive from the same constants.
   `test_every_card_relationship_is_wired_to_both_selector_surfaces` locks that down.
   The draft effort selector now labels options `N EP` like the committed one, and both screens
   carry per-field titles.
3. **The paged list renderers share one implementation. [fixed]** `_live_card_order`, `_paginate`
   and `_paging_row` replace the duplicated ordering, clamping and ◀/▶ button construction in
   `render_dashboard` and `render_children`. `test_dashboard_paging_walks_between_pages` covers
   paging past page 1, which nothing did before.
4. **Name resolution is one function. [fixed]** `domain.resolve_references` resolves a
   relationship's IDs and exact names against committed data and reports `unknown_ids`, `missing`,
   `ambiguous` and `blank` separately. The three call sites now only choose how to report:
   preparation raises the model-facing `ToolPreparationError` codes, approval raises `DomainError`,
   and the review screen lists unresolved names. `ReferenceSpec` (with `VALUE_REFERENCE` /
   `TAG_REFERENCE`) replaces the `singular_key`/`plural_key`/`query_key`/`model`/`label` argument
   quintuple that was threaded through six call sites, and also let `_replace_card_sets` and
   `_apply_card_links` drop their duplicated Value/Tag halves.
5. **Draft validation now calls the domain rules. [fixed]** `_card_creation_errors` runs
   `validate_action_fields` and `validate_blocked_fields` instead of restating them, so the Save
   button and `create_card` cannot disagree. Only the title check stays local (it is a UI prompt,
   not a rule). The effort scale is down to `EFFORT_POINTS` plus the `contracts.py` Literal, which
   must stay literal for the JSON schema.
6. **`_typed_expression` no longer parses its own output. [fixed]** It formats a set of Categories
   or Energy types; the `" → "` in a diff line is added by the diff builder, which was always the
   only thing producing it.
7. **Tag/Value item screens are spec-driven. [fixed]** `_ITEM_REFERENCES` reuses the same
   `ReferenceSpec` objects, so `render_item_editor` and the archive prompt no longer rebuild
   `Tag if entity == "tag" else Value` alongside its `link_model`/`link_field` twin, and the linked
   Card count is one helper.
8. **`set_value_focus` delegates to `update_value_fields`. [fixed]** One write path for `active`.

### Still open

- **Approval state is a JSON blob** in `AgentStep.metadata_json`: `queue`, `tool_calls`,
  `result_summaries`, `display_result_summaries`, `tool_count`, `repair_rounds`,
  `repair_exhausted`, `assistant_content`, `continuation_error`. Two of those lists differ only by
  `include_preparation_errors`. A typed table, or at least a dataclass with one
  serialize/deserialize pair, would remove a lot of `dict(...)`-shuffling and the class of bug fixed
  in the first pass. Deliberately not started: it is a schema change plus a rewrite of the resume
  path, and it wants its own pass with its own tests.
- **Four overlapping value formatters** — `_result_value`, `_detail_value` (service),
  `_display_diff_value`, `_proposal_diff_value` (telegram) — and **three card-snapshot shapes**:
  `card_snapshot` (domain), `_card_detail_snapshot` (service), `_proposal_item_state`'s `current`
  (telegram). Left alone on purpose: two of the formatters feed model-facing tool results and two
  feed user-facing HTML, so merging them would churn the payload the model reads for no behavioural
  gain. Worth revisiting only alongside the snapshot consolidation.
- **Saved Request SQL still runs on the read-write session** (see Bugs) and **`card_progress` still
  loads every Card per call** (see Bugs).

### query_safwa result caps — sized for a local model **[fixed]**

The caps were hard-coded at 100 rows and a 50,000-character payload, so one over-broad
`SELECT` could return ~16,700 tokens — unusable against a 30B-class model. Worse, the trimming
was silent: the model received a short result with no hint it was incomplete and could reason as
though it had seen everything.

`ReadOnlyQueryRunner` now takes `row_limit`, `char_budget`, `column_limit` and `cell_limit`, and
returns a `QueryOutcome(rows, notice)`. When any cap is hit the notice names which one and says to
narrow the query; `as_tool_result()` appends it as a trailing `{"notice": …}` row so an uncapped
result stays byte-identical to before and only a capped one costs extra. `SYSTEM_PROMPT` gained one
line (+35 tokens) telling the model a `notice` row is an instruction, not data.

Defaults are now 50 rows / 12,000 characters (~4,000 tokens), overridable with
`SAFWA_AI_QUERY_ROW_LIMIT` and `SAFWA_AI_QUERY_CHAR_BUDGET`. Worst case from one query drops
~16,700 → ~4,000 tokens.

Covered by `test_query_result_reports_the_row_cap_and_asks_to_narrow`,
`test_query_result_reports_the_character_budget`,
`test_query_result_reports_shortened_text_values` and `test_uncapped_query_carries_no_notice`.

### SYSTEM_PROMPT: static rules moved into tool results **[fixed — needs live validation]**

`# Tools and approvals` was 686 of the prompt's 1,424 tokens and roughly 400 of those restated
things the model already receives elsewhere. Three bullets were near-verbatim copies of text the
code already emits at the moment it applies:

| prompt bullet | already delivered by |
|---|---|
| unresolved-reference retry rules | `reference_hint` in `_validate_named_references` |
| `[Current request progress]` explainer | the block's own header line |
| "never claim complete before approval" (twice) | the `next` field on a prepared mutation |
| `parent_query` usage | the `parent_query` field `description` in the tool schema |
| `card(mode="edit"\|"move"\|…)` list | the `mode` Literal in the tool schema |
| Request SQL rules | the `sql` field `description` plus the `unsafe_query` hint |
| `remove` permanent-delete rule | the tool description plus `RemoveToolInput`'s validator |

All seven removed. One contract line replaces them: *"Tool results are authoritative and carry
their own instructions. Obey the `hint` on an error, the `next` on a prepared or resolved call, and
the `notice` on a capped query."* Kept in the prompt: the effort-scale semantics and the `ai_*`
column listing, neither of which exists anywhere else.

One real gap had to be filled, since sibling semantics were only ever stated statically:
`_with_queued_siblings` now attaches `next` to a **failed** call's result naming how many valid
sibling calls are queued and that they were not cancelled — charged only on requests that actually
fail. Covered by `test_failed_call_result_states_that_its_siblings_are_still_queued`.

```
SYSTEM_PROMPT   1424 -> 958 tok   (-465 per turn)
fixed floor     3669 -> 3204 tok
```

**No test can validate this.** `ScriptedProvider` replays fixed responses and never reads the
prompt, so all 130 tests stay green whether the cut helps or hurts. It needs an A/B against the
real local model. To restore the old prompt: `git checkout HEAD -- src/safwa/ai/context.py` (the
`_with_queued_siblings` addition is independent and worth keeping either way).

### Rejected: trimming saved-item details from the progress summary

Measured first, then reverted. Dropping the `• field: value` lines for already-saved items looked
like a free 73% cut of the summary that enters history (83 → 22 tokens per item), but two tests
proved the lines are not redundant: they carry what Safwa *resolved* rather than what the model
sent — `• Parent ID: 1` from a `parent_query`, and `• Note: Before dinner → After dinner` from an
edit. Removing them invites repeated or wrong retries, and one extra repair round costs a full turn
(~3,700-token floor plus tool results) against ~80 tokens saved. A comment in
`_approval_results_summary` records this so it is not re-attempted.
- ~~**`.limit(30)` on the Value/Tag selectors silently truncates**~~ **[fixed]** — both the draft and
  the committed selector now page through Values and Tags ten at a time via the same
  `_paginate`/`_paging_row` helpers as the dashboards, and `choice_screen` renders the page label
  and buttons. The fixed enumerations (kind, stage, priority, effort, categories, energy) still
  render on one screen. Covered by `test_tag_selector_pages_instead_of_truncating`.
