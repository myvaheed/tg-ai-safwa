# Safwa — Subagents (plan)

**Implemented, all four phases.** This file stays the contract: a change to the behaviour is a
change to this file first. Read [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together today, and
[DIARY_PLAN.md](DIARY_PLAN.md) for the feature this replaces the plumbing of.

It began as a diagnosis. A subagent was a one-shot reader that could not suspend, so four mechanisms
existed to work around that one missing property: `diary_stamps` held the body between calls,
`observe_stamp` read it back, `propose_diary_update` smuggled the result into a session that *could*
suspend, and an `unspent_stamp` block in `_run_agent_loop` fabricated that call. Giving a subagent a
session that suspends and resumes removed all four together.

## Rules

These are the contract. Everything below follows from them.

1. **One session shape.** The Advisor is the root session; a subagent is a session of the same kind.
   A session has its own system prompt, its own tools, its own transcript, and its own budget. Its
   loop ends the same way for everyone: a provider turn with no tool calls is the answer.
2. **Two kinds of subagent, two verbs.** `route(name)` **hands the turn over**: the subagent reads
   the same bounded history, writes into the chat itself, and proposes for itself. `call_helper(name,
   request)` is an ordinary call with a clean context that reports back to its caller. Only `route`
   is built now.
3. **Whoever issued an `AgentChange` receives its outcome.** Approved and Discarded restore that
   session directly — there are no words to carry, so no one is in between. Interrupted freezes the
   screen, **saves the session**, and the owner's words go to the Advisor, which answers them or
   routes back.
4. **A saved session is restorable for exactly one Advisor turn.** It is not waiting: `route(name)`
   on the very next turn restores it, and anything else that turn does — its own answer, a `route`
   somewhere else — ends it for good. The owner's words either belong to that draft now or they
   never will, and a session revived three turns later would answer the wrong question. A session
   whose screen is still live is the exception: Save resumes it without the Advisor at all.
5. **`route(name)` restores that subagent's saved session, or starts a new one.** The model never
   chooses between the two; the runtime decides from what exists. `route` takes no request: the
   subagent reads the conversation as it stands and treats the last owner message as addressed to it.
6. **A routed subagent speaks for itself.** Its prose is the chat message, its citations are its own,
   and its proposal screen is built from its own fields. Nothing is relayed, paraphrased, or
   re-worded on the way out.
7. **The persona is one constant.** Voice, the user's language, and the citation format are composed
   into every routed subagent's prompt from a single block. Three copies would be three dialects.
8. **A routed subagent cannot route.** No `route`, no `call_helper`, no recursion.
9. **The Advisor does not read for someone else's task.** It decides, then answers or routes. A read
   done before a `route` is lost — the subagent sees history, not the Advisor's transcript.
10. **A session is claimed, not flagged.** Resuming starts with an atomic claim, the way a
    `CallbackToken` is claimed. A crash releases the claim; it never closes the work.
11. **One budget, carried across suspensions.** `MAX_TOOL_CALLS` already survives an approval for the
    Advisor and now belongs to the session. It is what bounds the one loop that can spin without a
    human: an autoapproved change resolves with no screen, so the session could otherwise propose
    forever. A human-gated loop needs no cap — the human is the clock.
12. **Two clocks, two different facts.** A proposal expires when its screen is no longer actionable;
    a session expires when nobody ever came back to it. The session clock is strictly the longer one
    and it is only the floor: rule 4 ends almost every session long before it, and the sweep is left
    with the one case rule 4 never sees — a screen the owner neither answered nor talked past.
13. **Preparation runs where the change was authored,** so the session that filled the fields is the
    session that repairs them.

## Decisions taken, with the alternative that was rejected

- **`route(name)` rather than `call_subagent(name, request)`.** A request string is a paraphrase, and
  the one correction that matters most — "the same, but capitalise the name" — is a correction to a
  body the Advisor never saw and, for the Diary, must never see. Handing over the turn removes the
  paraphrase instead of tightening it.
- **Interrupted goes through the Advisor; Approved and Discarded do not.** Interrupted carries owner
  words that may not belong to the open work at all, and only the Advisor addresses the owner
  directly. The round trip costs one fast `route`. The button decisions carry no words, so routing
  them through anyone would be pure latency.
- **`route` restores rather than restarts.** A restart re-reads the day and rewrites the draft, so
  "capitalise the name" would silently produce a different text. Restoring keeps the work that was
  already approved-in-spirit.
- **An atomic claim rather than a `resuming` status.** `resolve_approval` used to mark a batch
  `resuming` and commit before calling the provider, so a crash in between left a row that
  `_pending_batch_for_target` still found and that could never finish;
  `_close_interrupted_approval_batches` then had to discard the owner's request at startup. The claim
  inverts that: a crash costs one repeated button press, and recovery is one `UPDATE`.
- **The screen's fields come from the subagent.** A Diary proposal is always `pov` (the day in the
  owner's voice) and `ai_comment` (Safwa's one line about it). The subagent used to write `remark`,
  the Advisor was told to say it "as your own words", and that rewritten copy became the screen text
  — a paraphrase with no purpose.
- **No suspension-round limit.** Each round needs a human decision, so the loop runs at human pace
  and a counter would only interrupt someone who is still working. Rule 10 covers the one path that
  resolves without a human.
- **The Advisor keeps `query_safwa`.** Reading is most of ordinary advice. If measurement shows it
  reading before a `route`, the answer is an optional `potential_ids` list of validated integers on
  `route` — numbers the host filters, never prose, so there is nothing to garble.
- **The Advisor keeps no mutation tool.** Every change then has exactly one producer per turn, which
  is what lets a session materialise its own changes, and a change it cannot make is a change it
  cannot describe instead of routing.
- **One turn of grace, not an open wait.** Rule 4 could have let a saved session live until its
  session clock ran out, but then "write today's entry" a day later would silently continue a draft
  the owner had already talked past. Closing it as soon as the Advisor does anything else keeps the
  correction case and drops the stale one; the sweep in `recover_startup` is left for the session
  nobody ever answered. The objection — that judging *which* change to
  propose is the advisory work and a specialist sees only a summary — is answered by rule 4: a routed
  subagent sees the same conversation the Advisor saw.
- **`mini.py` survives, narrowed.** A terminal-tool one-shot is the right shape for a session that
  genuinely cannot propose and cannot continue: `autoapproval`, `reminder_sessions`. Making those
  resumable would generalise a mechanism they never use.

## Phase 1 — one session runner · done

Pure refactor, no behaviour change. Preparation moved first because a session that authors a change
has to be able to validate it.

- `ChangePreparer.prepare(session, change)` ([ai/prepare.py](../src/safwa/ai/prepare.py)) holds what
  `AIAdvisor._create_proposal` used to do before it wrote anything: parent-kind rules,
  `_validate_named_references` over `CARD_REFERENCE_SPECS`, `_guard_pending_checks`,
  `_prepare_reminder_values`, `normalize_request_sql`, the Action-only field stripping, and the
  target lookup that yields `expected_version`. It raises the same `ToolPreparationError`.
  `_create_proposal` keeps only persistence: `ChangeProposal`, `ProposalChange`.
- `AgentSession` ([ai/service.py](../src/safwa/ai/service.py)) is the session itself: its run, its
  tools, its transcript, and the budget it carries across an approval. `_run_agent_loop`,
  `_provider_turn`, the three tool executors and `_materialize` all take it instead of loose
  arguments, so a second session differs only by the row it is built from. `AgentLoopResult` is now
  one turn's words and changes, nothing more.
- `run_mini_session` stays as it is. Merging it in was the plan and was wrong: `autoapproval` and
  `reminder_sessions` pass terminal tools and no read tools at all, so what they need is a
  structured answer, not a session. The Diary is the one caller that needs a real session, and
  phase 3 gives it one rather than generalising theirs.

**Done when** the whole suite is green, the Diary still works through the stamp path, and no diff
touches behaviour. — 432 passed, ruff clean.

## Phase 2 — sessions persist and resume · done

Schema change. The database is rebuilt from `models.py`; back up first: `uv run safwa-backup`.

- `agent_runs` carries the session: `kind` (`advisor` / subagent name), `state_json` (dialogue,
  transcript, budget, the receipts already shown), `claimed_at`, and `updated_at`. `AgentSession`
  writes it with `state()` and comes back from it with `restore()`.
- The batch is only its screens now — `queue`, `tool_calls`, `repair_exhausted`, `status`. It is
  closed in the same commit that claims the session, so a crash during the continuation leaves no
  half-open batch and no `resuming` state to repair; `_close_interrupted_approval_batches` is gone.
- Resuming starts with `UPDATE … WHERE claimed_at IS NULL RETURNING`. `held_run_id` is how a turn
  that already owns the session — an autoapproval resolving the screen it just opened — continues
  inside its own claim instead of deadlocking against it. `_finish_run` releases the claim on every
  ending, and `recover_startup` releases the claims of sessions interrupted mid-resume.
- `MAX_TOOL_CALLS` is read from and written back to the session row.
- The session clock: `_close_abandoned_sessions` closes `awaiting_approval` sessions untouched for
  `SESSION_IDLE_DAYS`, cancelling their open batch with them. A proposal expires within a day, so
  two days is past every screen the owner could still answer — no separate check of whether the
  screen is still the newest chat message is needed.

**Done when** a crash at each point of the suspend/resume cycle costs one repeated button press and
nothing else, and an autoapproved chain stops at the tool-call budget. — covered by
`test_startup_releases_the_claim_of_an_interrupted_session`,
`test_startup_closes_a_session_left_waiting_past_its_screens`,
`test_the_tool_call_budget_is_carried_across_an_approval` and
`test_a_session_can_only_be_claimed_once`.

## Phase 3 — `route`, and the Diary onto it · done

`route` and the Diary land together: the Diary is the only subagent there is, so it is what proves
the handover works, and its plumbing is what the handover deletes.

- `route(name)` replaces `call_subagent` in `IMMEDIATE_TOOLS`. It ends the Advisor's turn: control,
  the chat message and the proposal screen all belong to the subagent from that point. `RoutedSubagent`
  ([ai/subagents.py](../src/safwa/ai/subagents.py)) is what a subagent now *is*: a prompt, read tools,
  the mutation tools it owns, and its clock. `SubagentRunner` and `SubagentOutcome` are gone with the
  mini-session the Diary used to run in.
- `PERSONA` is composed into each routed prompt.
- `SUBAGENT_DEADLINE_SECONDS` bounds one **active segment**, not a whole session; `asyncio.wait_for`
  cannot span a suspension.
- Interrupted: the owner's text while a screen is open freezes the screen, saves a subagent's session
  as `awaiting_approval` with its own results folded into its transcript, and goes to the Advisor as
  an ordinary turn. The Advisor's own session is cancelled by it, as before.
- `_close_lapsed_sessions` closes every saved subagent session the Advisor's turn did not route back
  into, so the restore window is exactly one turn. A session whose batch is still `pending` is left
  alone — its screen is live and Save still resumes it.
- The `# Subagents` roster in `SYSTEM_PROMPT` is rewritten as `# Routing` rules — one line per case,
  imperative, naming the subagent. It is the only thing standing between a request and a change, so
  a missed route is a silent no-op.
- The Diary subagent gets an ordinary mutation contract carrying `mode`, `date`, `pov`, `ai_comment`
  and `feeling_score`. `DIARY_REPORT`, `DiaryReportInput`, `_report`, `_issue_stamp` and
  `_observe_stamp` are gone; `read_day` survives as `day_read_tool`, and the date line as
  `diary_clock`.
- Deleted: `diary_stamps` and `DiaryStamp`, `DiaryProposalInput`, the `propose_diary_update` branch in
  `mutation_change_from_tool`, `_stamp_debt`, `SUBAGENT_PROPOSAL_TOOLS`, the `unspent_stamp` block,
  the `MUTATION_TOOL_DESCRIPTIONS` filter, the `Draft:` line in `_diary_detail_lines`, the `DiaryStamp`
  sweep in `recover_startup`, and the `trace` hook `mini.py` kept for the Diary alone. 28 tables.
- `_resolve_diary_change` stays, inverted: instead of reading a change back out of a stamp it settles
  `write` into create or update from `ai_diary`, and refuses a removal of a day that was never written.
- `DIARY_PROMPT` scopes its voice rules to `pov` alone — `ai_comment` addresses the owner.
- `render_proposal`'s Diary branch renders the two fields.
- `DIARY_PLAN.md` keeps only what is specific to the Diary; the stamp rules and the
  synchronous-subagent decisions are gone.

**Done when** a routed answer reaches the chat with its own citations; an off-topic message over an
open screen is answered by the Advisor with the suspended session still resumable; and a day is
written, refused, corrected by "capitalise the name" into a second proposal from the same session,
and saved — with the body never appearing in the conversation and no stamp anywhere. — covered by
`test_a_routed_subagent_answers_the_owner_in_its_own_words`,
`test_a_routed_subagent_proposes_for_itself`,
`test_a_correction_reaches_the_session_that_wrote_the_refused_day`,
`test_a_refused_day_is_over_once_the_advisor_answers_something_else` and
`test_a_screen_still_open_keeps_its_session_restorable`.

## Phase 4 — board · done

- `card`, `check`, `value`, `tag`, `request`, `reminder`, `remove` moved to a `board` subagent
  ([ai/board.py](../src/safwa/ai/board.py)), with the `SYSTEM_PROMPT` sections that only serve them:
  the full Planning structure, Checks, the Reminder timing rules, and the `# Proposing` rules — prefill,
  the placeholder-id rule, read-before-mutate. `SYSTEM_PROMPT` keeps what a reader needs: enough
  structure to read the data, the Sprint, `query_safwa`, routing, and how to answer.
- Each routed subagent declares its context: `history_messages` and `planning_state`. `board` takes
  the whole window and the board state; `diary` takes `DIARY_HISTORY_MESSAGES` and no state — it has
  `read_day`. Not *none*: a resumed Diary session has to see "capitalise the name".
- `_guard_pending_checks` now fires inside `board`, which cites the Checks itself.
- The e2e `ScriptedProvider` routes on its own when the next scripted response asks for a tool the
  current session was not offered — the same thing the model has to do, and it leaves every existing
  mutation test measuring the system rather than the wording of a hand-off.

**Done when** every mutation e2e passes through `board`, an e2e test proves a change request produces
a `route` and not prose, and the Advisor's tool list is `query_safwa` + `route`. — the Advisor has no
mutation tool at all, so prose instead of a route is not a thing it can do; covered by
`test_the_board_owns_every_mutation_tool` and
`test_each_subagent_sees_only_the_conversation_it_declared`.

## Deferred

- `call_helper(name, request)`: the clean-context call that returns to its caller. No user yet.
- `potential_ids` on `route`. Measure first.
- Asynchronous sessions. A routed subagent runs in the turn it was handed; nothing needs more.

## Documentation kept in step

[ARCHITECTURE.md](ARCHITECTURE.md) (runtime wiring, AI advisor, Subagents, table count),
[STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md), [DIARY_PLAN.md](DIARY_PLAN.md),
[CLAUDE.md](../CLAUDE.md) — "A subagent reads and reports; it never mutates" becomes the session
rules above — and [diagrams/06-proposals.md](diagrams/06-proposals.md).
