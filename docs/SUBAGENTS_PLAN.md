# Safwa — Subagents (plan)

**Implemented, all seven phases.** This file stays the contract: a change to the behaviour is a
change to this file first. Read [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together
today, and [DIARY_PLAN.md](DIARY_PLAN.md) for the one feature that is owned outright by a subagent.

## The problem

One model answers the owner, and one model cannot hold every rule Safwa has. Splitting it needs two
things that a plain function call cannot give.

**A request can touch two domains.** "Rename the action to *Приготовить пиццу* and write yesterday's
day" is one sentence about the board and about the Diary. Whoever handles the first half has to hand
control back, or the second half is silently dropped.

**A change waits for a human.** Every mutation opens a Save/Discard screen, and the owner may press
it in ten seconds or in two hours — or type something else entirely instead. So a specialist is not
a call that returns in one breath: it is a **session** that suspends on its own screen, keeps
everything it needs on its own row, and resumes when the owner decides.

Both fall out of one shape: the Advisor and every subagent are the same kind of session, and
`route(name)` is an ordinary call between them that may take a very long time to return.

## Rules

These are the contract. Everything below follows from them.

1. **One session shape.** The Advisor is the root session; a subagent is a session of the same kind.
   A session has its own system prompt, its own tools, its own transcript, and its own budget. Its
   loop ends the same way for everyone: a provider turn with no tool calls is the answer.
2. **`route(name)` is a call that returns.** The subagent reads the same bounded history under its
   own prompt and its own tools, and hands back a receipt: what it did, what it wrote, or how it
   failed. The turn ends only when the Advisor itself answers, so a request that spans two domains
   is two routes and one message.
3. **`route(name)` takes no request.** The subagent reads the conversation as it stands and treats
   the last owner message as addressed to it. A request string would be a paraphrase, and a
   paraphrase is exactly what a correction has to survive.
4. **`route(name)` restores that subagent's saved session, or starts a new one.** The model never
   chooses between the two; the runtime decides from what exists.
5. **A routed subagent cannot route.** No recursion.
6. **Only the Advisor speaks.** A subagent's prose comes back inside its receipt as `text`, carrying
   its citations and their ids, and the Advisor composes the one message the owner sees. A proposal
   screen is still built from the subagent's own fields — a screen is a tool call, not speech, and
   the owner reads the whole proposal there whether or not any of it reaches the conversation.
7. **The persona is one constant.** Voice, the owner's language, and the citation format are
   composed into every routed subagent's prompt from a single block. Three copies would be three
   dialects.
8. **The Advisor's session spans its routes.** It is suspended while a subagent runs, not finished,
   so what it read before routing is still there when the receipt comes back — and that transcript
   is what tells it whether any part of the request is still unaddressed. What it read is not handed
   down: a subagent sees the conversation and the receipts, never the Advisor's transcript.
9. **Whoever issued an `AgentChange` receives its outcome.** Approved and Discarded restore that
   session directly — there are no words to carry, so no one is in between. Interrupted freezes the
   screen, **saves the session**, and the owner's words go to the Advisor, which answers them or
   routes back.
10. **A saved session is restorable for exactly one Advisor turn.** It is not waiting: `route(name)`
    on the very next turn restores it with its tool calls intact, and anything else that turn does —
    its own answer, a `route` somewhere else — ends it for good. The owner's words either belong to
    that draft now or they never will, and a session revived three turns later would answer the
    wrong question. A session whose screen is still live is the exception: Save resumes it without
    the Advisor at all. The grace belongs to the subagent alone — a caller interrupted mid-route is
    over, because the words that interrupted it are what the next Advisor turn is for.
11. **A session is claimed, not flagged.** Resuming starts with an atomic claim, the way a
    `CallbackToken` is claimed. A crash releases the claim; it never closes the work.
12. **One budget, carried across suspensions.** `MAX_TOOL_CALLS` belongs to the session, and `route`
    spends from it like any tool call. It bounds the one loop that can spin without a human: an
    autoapproved change resolves with no screen, so a session could otherwise propose forever, and a
    turn that routes repeatedly could otherwise never stop. A human-gated loop needs no cap — the
    human is the clock.
13. **Two clocks, two different facts.** A proposal expires when its screen is no longer actionable;
    a session expires when nobody ever came back to it. The session clock is strictly the longer one
    and it is only the floor: rule 10 ends almost every session long before it, and the sweep is
    left with the one case rule 10 never sees — a screen the owner neither answered nor talked past.
14. **Preparation runs where the change was authored,** so the session that filled the fields is the
    session that repairs them.
15. **The owner never gets emptiness.** A session ends in words: a turn that stops with no content
    is told so and run again, bounded by the repair rounds. Behind that, the one participant that
    writes to the chat still guarantees a message — the receipts when there are receipts, one `⚠️`
    line when there is nothing at all.
16. **The Advisor reads everything and writes nothing.** Every `ai_*` view is its own, `ai_diary`
    included: it cites `[16.08.2026](diary:12)` and the whole day opens behind the link. Owning a
    feature is owning its *writes*, so a mutation tool belongs to its subagent and the Advisor holds
    none at all.
17. **Everything a model reads is written for a 4B–12B model.** Short, imperative, concrete; each
    rule stated once, in the prompt that uses it.

## Decisions taken, with the alternative that was rejected

- **`route(name)` rather than `call_subagent(name, request)`.** A request string is a paraphrase, and
  the correction that matters most — "the same, but capitalise the name" — is a correction to a draft
  the Advisor never saw. Letting the subagent read the conversation itself removes the paraphrase
  instead of tightening it.
- **A receipt, not a transcript.** `route` could melt the subagent's own tool calls into the
  Advisor's message list, as if the Advisor had made them. Three things break: the Advisor sees
  `tool_calls` naming tools its own `tools` array does not hold, which a small model reconciles by
  calling them; the `pov` of a Diary day rides inside those arguments into every later hop; and each
  route carries every earlier one, which is the opposite of what `history_messages` scopes. The
  receipt says what happened, in the shape the owner already reads.
- **The subagent's prose returns instead of reaching the chat.** Two narrators split one turn into
  two half-answers, and the second one cannot see the first. Returning `text` costs a relay, and the
  relay is worth it: the Advisor composes once, with the subagent's ids in hand.
- **A turn ends only through the Advisor.** The alternative was a static queue —
  `route(["board", "diary"])` named up front. It still needs an owner above the subagent to start the
  second route after the first screen resolves, so it costs the same suspended caller while
  requiring the Advisor to foresee both domains from the first message.
- **`route` is the only tool call in its response.** A suspended response cannot carry results for
  its siblings: the transcript would resume with an assistant message whose tool calls are half
  answered. Mixing is a retryable `route_is_not_shared` instead.
- **Interrupted goes through the Advisor; Approved and Discarded do not.** Interrupted carries owner
  words that may not belong to the open work at all, and only the Advisor addresses the owner
  directly. The round trip costs one fast `route`. The button decisions carry no words, so routing
  them through anyone would be pure latency.
- **`route` restores rather than restarts.** A restart re-reads the day and rewrites the draft, so
  "capitalise the name" would silently produce a different text. Restoring keeps the work that was
  already approved in spirit.
- **One turn of grace, not an open wait.** Rule 10 could let a saved session live until its session
  clock ran out, but then "write today's entry" a day later would silently continue a draft the
  owner had already talked past. Closing it as soon as the Advisor does anything else keeps the
  correction case and drops the stale one; the sweep in `recover_startup` is left for the session
  nobody ever answered.
- **An atomic claim rather than a `resuming` status.** A status marked before calling the provider
  survives a crash as a row that `_pending_batch_for_target` still finds and that can never finish,
  and recovery then has to discard the owner's request at startup. The claim inverts that: a crash
  costs one repeated button press, and recovery is one `UPDATE`.
- **No suspension-round limit.** Each round needs a human decision, so the loop runs at human pace
  and a counter would only interrupt someone who is still working. Rule 12 covers the one path that
  resolves without a human.
- **The Advisor keeps `query_safwa`.** Reading is most of ordinary advice, and a question about a
  Diary day or a Sprint metric should not cost a hand-over. If measurement shows it reading *before*
  a `route`, the answer is an optional `potential_ids` list of validated integers on `route` —
  numbers the host filters, never prose, so there is nothing to garble.
- **The Advisor keeps no mutation tool.** Every change then has exactly one producer per turn, which
  is what lets a session materialise its own changes, and a change it cannot make is a change it
  cannot describe instead of routing.
- **The screen's fields come from the subagent.** A Diary proposal is `pov` (the day in the owner's
  voice) and `ai_comment` (Safwa's one line about it), rendered as the subagent wrote them. Asking
  the Advisor to restate either would put a paraphrase on the screen the owner approves.
- **`allow_silence` is not a session flag.** Whether an empty answer is a failure is a question about
  the chat, not about a session; threading a boolean through three constructors to answer it puts it
  in the wrong place. Rule 15 puts it where the chat is.
- **`mini.py` survives, narrowed.** A terminal-tool one-shot is the right shape for a session that
  genuinely cannot propose and cannot continue: `autoapproval`, `reminder_sessions`. Making those
  resumable would generalise a mechanism they never use.

## Phase 1 — one session runner · done

Pure refactor, no behaviour change. Preparation moves first, because a session that authors a change
has to be able to validate it where it wrote it (rule 14).

- `ChangePreparer.prepare(session, change)` ([ai/prepare.py](../src/safwa/ai/prepare.py)) holds
  everything that runs before a proposal is written: parent-kind rules,
  `_validate_named_references` over `CARD_REFERENCE_SPECS`, `_guard_pending_checks`,
  `_prepare_reminder_values`, `normalize_request_sql`, the Action-only field stripping, and the
  target lookup that yields `expected_version`. It raises `ToolPreparationError`, which is
  model-visible and retryable. `_create_proposal` keeps only persistence: `ChangeProposal`,
  `ProposalChange`.
- `AgentSession` ([ai/service.py](../src/safwa/ai/service.py)) is the session itself: its run, its
  tools, its read tools, its dialogue, its transcript, and the budget it carries across an approval.
  `_run_agent_loop`, `_provider_turn`, the three tool executors and `_materialize` all take it
  instead of loose arguments, so a second session differs only by the row it is built from.
  `AgentLoopResult` is one turn's words, its changes, and nothing else.
- `run_mini_session` is left alone. `autoapproval` and `reminder_sessions` pass terminal tools and no
  read tools at all: what they need is a structured answer, not a session that can suspend.

**Done when** the suite is green and no diff touches behaviour.

## Phase 2 — sessions persist, resume and nest · done

The one schema change in the plan. The database is rebuilt from `models.py`; back up first:
`uv run safwa-backup`.

- `agent_runs` carries the session: `kind` (`advisor` or a subagent name), `state_json` (dialogue,
  transcript, budget, receipts already shown, and the `route` call still waiting for its answer),
  `claimed_at`, `parent_run_id`, and `updated_at`. `AgentSession` writes it with `state()` and comes
  back from it with `restore()`.
- `parent_run_id` is the session that routed here. It is what makes a chain resumable: the receipt of
  a finished session goes back up this link until a session with no parent answers.
- The approval batch is only its screens — `queue`, `tool_calls`, `repair_exhausted`, `status`. It is
  closed in the same commit that claims the session, so a crash during the continuation leaves no
  half-open batch to route a later press into.
- Resuming starts with `UPDATE … WHERE claimed_at IS NULL RETURNING`. `held_run_id` is how a turn
  that already owns the session — an autoapproval resolving the screen it just opened — continues
  inside its own claim instead of deadlocking against it. `_finish_run` releases the claim on every
  ending, and `recover_startup` releases the claims of sessions interrupted mid-resume.
- `MAX_TOOL_CALLS` is read from and written back to the session row.
- The session clock: `_close_abandoned_sessions` closes `awaiting_approval` sessions untouched for
  `SESSION_IDLE_DAYS`, cancelling their open batch with them. A proposal expires within a day, so two
  days is past every screen the owner could still answer — no separate check of whether the screen is
  still the newest chat message is needed.

**Done when** a crash at each point of the suspend/resume cycle costs one repeated button press and
nothing else, and an autoapproved chain stops at the tool-call budget. — covered by
`test_startup_releases_the_claim_of_an_interrupted_session`,
`test_startup_closes_a_session_left_waiting_past_its_screens`,
`test_the_tool_call_budget_is_carried_across_an_approval` and
`test_a_session_can_only_be_claimed_once`.

## Phase 3 — `route` · done

The call itself, with no subagent to route to yet. `RoutedSubagent`
([ai/subagents.py](../src/safwa/ai/subagents.py)) is what a subagent *is*: a name, a one-line
purpose for the routing rules, its instructions, its read tools, the mutation tools it owns, how much
conversation it declares, and its clock. `PERSONA` is the block composed into every routed prompt.

- `route(name)` joins `IMMEDIATE_TOOLS`. Its executor starts or restores the named session, runs it,
  and returns its receipt as the `route` tool result. It is offered only when the roster is non-empty.
- The receipt:
  `{"subagent": "board", "outcome": "done", "did": [...], "text": "…", "error": "…"}`.
  `did` carries the Saved/Discarded/Failed lines the owner reads, unchanged in shape. `text` is
  present only when the subagent wrote something, and carries its citations with real ids. `error` is
  present on a failure or a `SUBAGENT_DEADLINE_SECONDS` timeout, and `outcome` is `error` with it.
  A `did` line also joins the caller's own summaries, so the interface still prints each receipt once.
- Suspension: when the subagent opens a screen, the whole chain waits. The caller stores its
  unanswered `route` call in `state_json["awaiting_route"]` and its status becomes
  `awaiting_approval`; Save resolves the batch and resumes the subagent; `_deliver_to_parent` walks
  `parent_run_id` upwards, appending each receipt to the caller's transcript and running it on.
- `SUBAGENT_DEADLINE_SECONDS` bounds one **active stretch**, never a suspension: `asyncio.wait_for`
  cannot span a human decision, and a saved session is not a slow session.
- `route` must be the only tool call in its response; a mixed response is a retryable
  `route_is_not_shared`.
- Interrupted: owner text while a screen is open freezes the screen, saves the subagent's session as
  `awaiting_approval` with its own results folded into its transcript, and cancels every caller
  waiting above it. The words go to the Advisor as an ordinary turn.
- `_close_lapsed_sessions` runs once at the end of a turn that did not suspend, and abandons every
  saved subagent session that turn never routed into — the one-turn window of rule 10. A session
  whose batch is still `pending` is left alone: its screen is live and Save resumes it.
- `_run_agent_loop` asks again for words rather than returning silence, and no session decides what
  an answer that stays empty means: `_materialize` composes the receipts into the root's message and
  falls back to one `⚠️` line when there is nothing; a subagent's empty answer is handed to its
  caller untouched.
- The `# Routing` section of `SYSTEM_PROMPT` is the whole roster: one line per subagent, plus the
  loop — route, read the receipt, route again for a domain still unaddressed, answer once. There is
  no discovery tool, so a subagent missing from that section is never routed to.

**Done when** a `route` reaches a session under its own prompt with only its own tools, its words come
back as a receipt, and a request naming two domains produces two routes and one answer. — covered by
`test_a_routed_subagent_hands_its_words_back_and_the_advisor_speaks`,
`test_a_routed_subagent_is_offered_only_its_own_tools`,
`test_route_is_not_offered_without_a_roster`,
`test_an_unknown_route_target_is_repaired_in_the_next_response`,
`test_two_domains_in_one_request_are_both_finished` and
`test_a_failed_subagent_comes_back_as_an_error_the_advisor_reports`.

## Phase 4 — the Diary onto `route` · done

The first subagent, and the one that proves suspension: a Diary day is long, it is the owner's own
voice, and it is exactly the thing a correction lands on.

- The `diary` subagent ([ai/diary.py](../src/safwa/ai/diary.py)) owns one mutation tool. `read_day`
  (`day_read_tool` → `TelegramHistorySource.day_transcript`) gives it that day's conversation, which
  no view holds, because work done from the buttons never reaches the chat and how a day felt never
  reaches the database. `diary_clock` is its one volatile line: which day "today" is.
- The `diary` contract carries `mode` (`update`/`delete`), `date`, `pov`, `ai_comment` and
  `feeling_score`. `pov` is the day in the owner's voice; `ai_comment` is Safwa's one line to them,
  and the review screen is exactly those two.
- Whether that day already exists is a fact about the data, so `_resolve_diary_change` reads
  `ai_diary` and settles `update` into create or update, and refuses a `delete` of a day that was
  never written — retryably, with the sentence to say.
- `declared context`: `history_messages = DIARY_HISTORY_MESSAGES`, no planning state. Not *none* — a
  resumed session has to see "capitalise the name".
- The day never travels through a tool result: the receipt carries the date, the score and a
  character count. A receipt stays in the chat and would be re-read on every later turn, while the
  draft is already held by the session that wrote it.

**Done when** a day is written, refused with words instead of a button, corrected by "capitalise the
name" into a second proposal from the *same* session, and saved — with the body never appearing in
the conversation. — covered by `test_a_routed_day_travels_from_the_subagent_to_a_saved_entry`,
`test_a_correction_reaches_the_session_that_wrote_the_refused_day`,
`test_a_refused_day_is_over_once_the_advisor_answers_something_else`,
`test_a_screen_still_open_keeps_its_session_restorable`,
`test_words_over_a_screen_end_the_caller_but_not_the_draft`,
`test_a_resolved_diary_change_hands_back_the_day_shape_and_not_its_text` and
`test_removing_a_day_that_was_never_written_is_refused_and_retryable`.

## Phase 5 — board, and the line between reading and writing · done

- `card`, `check`, `value`, `tag`, `request`, `reminder` and `remove` belong to a `board` subagent
  ([ai/board.py](../src/safwa/ai/board.py)), together with the rules that only serve them: the Card
  tree and its fields, Checks, Reminder timing, and how to fill a proposal in. It declares
  `planning_state=True` and the whole dialogue window, because judging *which* change to propose is
  the work.
- `_guard_pending_checks` therefore fires inside `board`, which cites the Checks itself.
- The Advisor keeps every `ai_*` view, `ai_diary` included, and no mutation tool at all (rule 16).
  Reading a day is `query_safwa` and a `[16.08.2026](diary:12)` citation whose link opens the whole
  entry; writing one is `route("diary")`. Owning a feature is owning its writes.
- Each subagent's view list is its own: `board` sees what a writer needs and not `ai_card_events` or
  `ai_current_sprint_metrics`, `diary` sees `ai_diary`, `ai_card_events`, `ai_checks` and `ai_cards`.
  A prompt is what scopes a reader, so a view left out of one is a view that reader cannot use.
- The e2e `ScriptedProvider` routes on its own when the next scripted response asks for a tool the
  current session was not offered, and answers the closing Advisor turn in the subagent's own words —
  the same two things the model has to do. Every existing mutation test therefore measures the
  system rather than the wording of a hand-off.

**Done when** every mutation e2e passes through `board`, the Advisor's tool list is exactly
`query_safwa` + `route`, and a Diary question is answered without routing. — covered by
`test_the_board_owns_every_mutation_tool`,
`test_each_subagent_sees_only_the_conversation_it_declared`,
`test_the_advisor_reads_a_day_itself_and_cites_it`,
`test_both_readers_are_told_about_the_diary_view` and
`test_the_diary_is_written_only_by_its_subagent`.

## Phase 6 — receipts reach the next subagent · done

Rule 8 sends the receipt up; this sends it down, so the second subagent of a turn is not working
blind against what the first one already changed.

- A subagent's context gains one host-built block, placed after the dialogue, through `_system_note`:

  ```
  [System]: Already saved in this request:
  ✅ Saved — Edit Action “Приготовить пиццу” (Title: Приготовить еду → Приготовить пиццу)
  ```

  The lines are the ones the owner reads, byte for byte and unprefixed: naming the subagent that
  produced each one would be a second receipt format for one reader. The block sits outside the
  dialogue, so a narrow `history_messages` window cannot trim it, and it lives on the session row, so
  a resume rebuilds it.
- It carries facts only. What is still *unaddressed* is the Advisor's judgment and stays in the
  Advisor's transcript; the subagent reads the owner's own message for that.

**Done when** the second subagent of a turn can name what the first one changed, and a single-route
turn adds no block at all. — covered by `test_the_second_subagent_reads_what_the_first_one_saved`.

## Phase 7 — sized for the model · done

The owner runs a 4B–12B model (rule 17). Prompts and receipts are the two surfaces it reads, and both
are written for a reader that infers less than a large model does.

- `SYSTEM_PROMPT` is 47 lines: what Safwa is, every `ai_*` view, the one rule that it reads and never
  writes, the routing loop, and how to cite. The Card tree, Checks, Reminder timing and the
  proposal-filling rules live in `BOARD_PROMPT`, which is where they are used.
- `BOARD_PROMPT` is 48 lines: the field rules a proposal has to satisfy, its own view list, and one
  closing instruction — say in one short sentence what was proposed, and nothing else, because the
  interface prints the Saved/Discarded/Failed receipt itself.
- `DIARY_PROMPT` is 42 lines, and its `feeling_score` rubric keeps all ten anchors. The three bands
  give the two-step choice; the anchor is what makes one *number* choosable rather than a band, and a
  small model has nothing else to choose it with.
- A receipt for a new Card names only what was **chosen**: `backlog` and `medium` are the defaults a
  Card lands on and say nothing, while stage, priority, effort, categories, energy, the flags, and
  the linked Values, Tags and Checks are what the owner checks the proposal by.

**Done when** each rule appears once, in the prompt that uses it, and a receipt shows every field the
owner picked and none they did not. — covered by
`test_a_new_card_receipt_names_every_field_that_was_chosen`,
`test_a_backlog_card_receipt_says_nothing_about_its_stage`,
`test_the_routing_rules_name_every_subagent_that_can_be_routed_to`,
`test_a_routed_prompt_carries_the_one_persona_block` and
`test_the_scale_is_stated_once_and_the_model_never_reaches_for_zero`.

## Deferred

- `call_helper(name, request)`: a clean-context call whose subagent reads none of the conversation.
  `route` covers every user there is.
- `potential_ids` on `route`. Measure first.
- Asynchronous sessions. A routed subagent runs in the turn it was handed; nothing needs more.

## Documentation kept in step

[ARCHITECTURE.md](ARCHITECTURE.md) (runtime wiring, AI advisor, Subagents, table count),
[STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md), [DIARY_PLAN.md](DIARY_PLAN.md),
[CLAUDE.md](../CLAUDE.md) (the session rules above),
[diagrams/03-session-contexts.md](diagrams/03-session-contexts.md) and
[diagrams/06-proposals.md](diagrams/06-proposals.md).
