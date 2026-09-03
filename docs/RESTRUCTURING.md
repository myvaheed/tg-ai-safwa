# Restructuring

**Every point should be short in one line.**

Nothing here is decided and nothing here is scheduled. A candidate is one line: what moves, and
what it costs or breaks. Each is taken apart on its own later. A candidate leaves this file by
becoming a batch, or by being ruled out in place.

Verdicts: **yes** — the move is right as stated. **split** — part of it is right. **first** —
something else has to happen before it can. **no** — ruled out, kept so it is not raised twice.

## Batch 1 — landed

1. **done** — `QUERY_SAFWA_TOOL` and `query_read_tool` sit in `ai/tools.py`; the runner stayed in
   `ai/sql.py`.
3. **done** — `features/home` owns the menu screen and `MENU_LAYOUT`; a screen declares a `title`
   and never a row.
4. **done** — the Backlog, the Sprint and Today are one `stage_list_block`, and the retro is
   `features/retro`.
5. **done** — the subagent is `features/workspace_mutator` and the set it keeps is the workspace.

Candidate 2 did not land. `features/proposals/api.py` dispatches on `Card | Check`, so moving it
under `ai/` would carry two features into the engine and break Rule M. Rule H does not see it: it
reads entity names as strings, and this is `isinstance`. Its verdict is now **first**.

### Benefits

- What the model may call is one screen of code: four schemas in one module, and `ai/mini.py` went
  back to being only the session runner.
- The set the owner keeps had two names — board in the prose, workspace in `workspace.revision` and
  `WorkspaceMode` — and now has one, so a reader never has to ask whether they are the same thing.
- Renaming the subagent moved the prompt-prefix snapshot, which is what makes that vocabulary
  impossible to drift quietly again.
- The row number had no owner: the shell may not list the features and a screen has no business
  naming its row. `features/home` is that owner, so the layout is one tuple and `title` went back to
  meaning what the screen is called.
- A menu label the layout forgets is dropped without a word, so the test asserts the two lists are
  the same list — the failure mode that mattered is the one thing checked.
- Three stage lists were one function plus a hand-composed copy in `render_sprint`; they are one
  block, so the copy is eighteen lines shorter and cannot fall behind.
- The side of a quick-move button is derived from `LIVE_STAGE_PRECEDENCE` by `moves_up`, not stored
  per target, so the Backlog got its button by adding one row of data and a fourth stage would need
  no code at all.
- Planning keeps only what is actually the Sprint's — its metrics, its Finish wording, its Planning
  branch — and Cards keeps every screen that is a list of Cards.
- The retro has somewhere to be built, instead of a stub inside `sprint.py` with a comment saying it
  belongs elsewhere.
- Every stale name in the docs was caught by `tests/test_docs.py` while the batch ran, four times,
  before it reached a commit.

## Batch 2 — landed

`safwa/ai/` and the `features/proposals/` core name nothing of Safwa's, and Rule N in
`scripts/architecture_metrics.py` reads zero only while that holds. What they may reach is
`PORTABLE_FOUNDATION`: `foundation/{clock,errors,models,references,screens}.py`, each free of
any entity. The review flow's `telegram/` and its `module.py` are the port and stay behind.

26. **done** — `Card` and `Check` answer `is_closed_repeat`, `live_instance_query` and
    `series_index_query` for themselves, so `foundation/marks.py` names no feature.
27. **done** — `proposals/api.py` dropped its third copy of the repeat helpers; `ReferenceSpec`
    carries a `refusal` and the wording lives with the feature that owns the link.
14. **done** — `CardKind`, `Priority`, `Category` and `EnergyType` are Cards', `ScheduleKind` is
    Reminders', and `WorkspaceMode` is `foundation/workspace.py`'s beside the row it describes.

Beside those: the marker wording left `ai/sql.py` for `foundation/marks.py`, where `title_marks`
already rendered it; the query caps, the mini-session budgets and the repair rounds went to the
modules whose defaults they are; `Workspace` left `foundation/models.py` for
`foundation/workspace.py`; and `ProposalRegistry` gained a `World` port, so proposals asks the
application for the revision it locks against instead of reading Safwa's row.

Candidate 2 is settled as **no**. The proposals core imports `ai` and is meant to; moving the
package under it would have reversed three arrows — `proposals/telegram` reads `shell`,
`module.py` reads `bootstrap`, and `ai/tools.py` already declares `MutationCatalogue` rather than
importing the registry. Travelling together is what was actually wanted, and Rule N says it.

### Benefits

- The engine's dependency on Safwa was invisible because it ran through modules, not names:
  `ai/sql.py` never imported a Card, it imported `constants.py`, which mentions Sprints. Rule N
  reads paths, so the next such import fails instead of being argued about.
- Three copies of `live_repeat_instance_id` — `cards/api.py`, `checks/api.py` and
  `proposals/api.py` — are one, and the `isinstance` ladder under `foundation/marks.py` is gone
  with them.
- The layer under everything stopped importing two features, so a repeat rule now changes in one
  place: the entity that repeats.
- A refusal's wording lives with the feature that owns the link, and generic code only raises what
  it is handed, so `validate_named_references` no longer spells `Check`.
- `enums.py` is what no single feature owns; a Card's four words are in `cards/model.py`, next to
  the column each one types.
- A limit is where its default is: the query caps are `ReadOnlyQueryRunner`'s, the mini-session
  budgets are `ai/mini.py`'s, and `constants.py` is back to cross-feature tuning alone.
- `Workspace` is not a schema primitive and no longer sits with `Base`; `foundation/workspace.py`
  holds the row, its two modes, and the two functions that read and move it.
- Proposals locks against a revision it is told about rather than a row it knows, so what the
  optimistic lock actually needs is one dataclass wide and stated in one place.
- Two scenarios said Card and Check where they meant a mechanism; they say the mechanism, and
  `cards.feature` says which of Cards' changes is the destructive one.

## tg-agent-shell — the plan

The engine, the review flow, the shell, the turn lease and the cues are one reusable thing:
`llm_gateway <- agent_runtime <- tg_agent_shell <- safwa`. Phases 1 and 2 are done; phases 3
and 4 are not started.

tg-agent-shell is the distribution and tg_agent_shell the package, because an import name
cannot carry a hyphen. Neither is backticked below: the name is planned, and a backtick here
means a name the code already carries. It is a shell rather than a harness — a harness drives
a model, which is `agent_runtime` one layer down, and this is where a person reaches the agent.

### What the move actually is

`src/` holds four packages. Three of them — `llm_gateway`, `agent_runtime`, `telegram_llm` —
are already self-contained, and Rule F is what keeps them so. The goal is a fourth of the
same kind.

Its code is written already, inside `safwa/`: `ai/`, `shell/`, `turn/`, `cues/`, `adapters/`,
`features/proposals/`, and five files of `foundation/`. Fifty-five modules.

They cannot simply be moved, because they made **43 imports out of the rest of Safwa**. Move
the directories and those 43 become `ImportError`. So the whole job is taking them to zero,
after which the move itself is `git mv` plus import paths. Sixteen are left, and Rule Q
names each one so a seventeenth cannot arrive unnoticed.

`ai/` and `turn/` are already clean, as are the five `foundation/` files and every
`features/proposals/` module but `module.py`. Everything left is in `adapters/`, `shell/`
and `cues/`.

### Two kinds of import, and only one of them is work

**Moves.** The imported thing already belongs to the shell and only sits in the wrong file.
`MessageKind` names the kinds of bot message — dialogue, cue, summary, dashboard, screen —
which is the vocabulary of whatever sends and marks them. It lives in `enums.py` for no
reason but history, so twelve shell modules reach outside for it. Cut, paste, rewrite the
import lines. Nothing is decided along the way.

**Seams.** The shell genuinely needs something of Safwa's, and moving it is not an option
because it *is* Safwa. `shell/chat.py` calls `record_summary`. Summary is a feature — another
project may want none, or a different one. But the shell does need to say "this conversation
grew long, here is where I cut it", so the dependency inverts instead: the shell declares
what it needs, and the composition root binds Safwa's feature to it. The shell never learns
that what it got is a summary.

Two facts make the seams cheaper than they look. `ai/messages.py` already declares `Memory`
as a protocol and takes `workspace_state` as a callable, so *told, not importing* is the
house style. And `maybe_summarize` already takes `send_summary` as a callback, so the history
seam is half inverted already.

### Phase 1 — the moves — **done**, 43 imports down to 16

Twenty-seven of the 43, and nothing to decide in any of them.

- `MessageKind` leaves `enums.py` for `adapters/kinds.py`, and `MARKS` is built there out of
  it. Not `shell/`: `shell/services.py` imports `adapters/`, so the reverse edge would be a
  cycle. Twelve imports. `TelegramHistorySource` is handed the table rather than importing
  it, which is what a second project needs in order to bring its own kinds.
- Fifteen constants have exactly one reader each and go to it — ten ASR and faster-whisper
  settings to `adapters/asr.py`, `SUMMARY_CONTEXT_MESSAGE_LIMIT` to
  `adapters/telegram_history.py`, `TOAST_SECONDS` to `shell/chat.py`, `PAGE_SIZE` to
  `shell/layout.py`, and the two ASR limits to `turn/dialogue.py`.

Two constants that look like the others are not moves and stay: `SUMMARY_TRIGGER_TOKENS` and
`SCHEDULER_POLL_SECONDS` each have a reader on both sides of the boundary, which makes them
cross-feature tuning and so `constants.py`'s. The shell takes them as parameters, in phase 3.

### Phase 2 — the ratchet — **done**

Rule Q: the 55 modules import nothing outside themselves, with the sixteen as named
exceptions the way `RULE_H_EXCEPTION` is. After phase 1 rather than before, because a rule
with sixteen exceptions can be read and one with forty-three cannot.

`RULE_Q_EXCEPTIONS` is the list, in `scripts/architecture_metrics.py`, blocked by the group
that closes each — and those blocks are the order phase 3 is written in. It is not copied
here, because two copies of a shrinking list is one copy too many.

The rule refuses a seventeenth import, and equally an exception whose import is already
gone. Without the second half the list stops shrinking and starts growing.

### Phase 3 — the seams

Each group closed deletes its own block from `RULE_Q_EXCEPTIONS`. A and B are still
carrying rather than inverting; C, D, E and F are the real work.

- **A — `Settings` becomes the fields each adapter reads** (1, 2, 3). First, because
  `ASRProvider` cannot join `asr.py` while `asr.py` still imports `config`: that would be a
  cycle, which is why phase 1 left it behind.
- **B — the plug contract travels** (14, 15, 16). `bootstrap/module_manifest.py` declares what
  a feature plugs into rather than which features exist, so it belongs to the shell. Its own
  `Settings` becomes the four fields it reads.
- **C — the window** (4, 5, 6, 11). `Services.continuity` is `turn/dialogue.py` calling one
  method; tg_agent_shell declares that method and Safwa binds it. The Summary's header, its
  token budget and the token estimate arrive the same way. Summary stays a feature —
  tg_agent_shell manages the window, it does not decide what goes in it.
- **D — the Summary's cut** (7). `shell/chat.py` calls `record_summary`; the callback returns
  the message id instead and `PersonaContinuity` records its own cut.
- **E — the Advisor's session** (9). `features/advisor/session.py` is shell code: eight `ai/`
  imports, eight `proposals/` imports, and two Safwa names — `MemoryFileStore`, which the
  `Memory` protocol already covers, and `workspace_context`, which the composition root can
  hand over. The package keeps `agent.py`, which is what its own `__init__` already says it is.
- **F — `command_status`** (8, 10, 12). It is the only reason `Services.memory` and `Workspace`
  are in the shell at all.
- **G — `SCHEDULER_POLL_SECONDS`** (13). Read by `cues/` and by `features/reminders/`, so it is
  cross-feature tuning and stays in `constants.py`; the shell takes it as a parameter.

### Phase 4 — the move

`git mv`, the import paths, and an entry in `pyproject.toml`. `shell/` becomes
`tg_agent_shell/telegram/`, because a package named for the shell cannot hold a directory of
the same name, and what is in there is the aiogram surface rather than the idea;
`features/proposals/telegram/` lands beside it as the second adapter of the one transport.

Rule F then covers the package, and Rules N and Q are deleted rather than extended: both
existed only to make this move possible.

Candidates 6, 12, 16, 18 and 22 are answered by the phases above, so none is worth doing on
its own. Candidate 8 is not, and blocks nothing: its only tie to the move is that a helper
offered by the shape of a SQL query is a wart the first other project would inherit.

## Candidates — from the owner

2. **no** — the review flow travels with the engine rather than into it; Rule N is what says
   so, and moving the package would reverse `shell`, `bootstrap` and `MutationCatalogue`.
6. **plan** — the split to make is not spec-from-spec: `AIAdvisor` is shell code and
   `features/advisor` keeps its prompt, which is the tg-agent-shell plan's first seam.
7. **yes** — continuity splits into summary and memory once continuity.feature does; the two share
   only `persona.py`.
8. **first** — the trigger is hardcoded in `ai/tools.py` as `agent.kind` plus `is_complex_read`, so
   a helper needs its own spec with a predicate — not a hook framework for one subscriber.
9. **yes** — `turn/` and `cues/` are the runtime, not features, and belong beside the engine;
   `turn/dialogue.py` is a Telegram handler and belongs in `shell/`.
10. **yes** — `remove` is the vocabulary of the subagent that calls it, so it moves to workspace_mutator;
    proposals keeps the flow and publishes no tool of its own.

## Candidates — found in the same pass

11. **yes** — `ai/contracts.py` declares `CardToolInput`, `CheckToolInput`, `ValueToolInput`,
    `TagToolInput`, `RequestToolInput` and `ReminderToolInput`: every feature's contract sits in
    the engine that owns no feature.
12. **plan** — `RoutedSubagent` and `AgentSpec` are one concept declared twice; both land on
    the shell side of the plan, which is where the duplicate is decided.
13. **yes** — `heavy_analyzer` has no `module.py` and `bootstrap/modules.py` imports its agent
    directly, which is the second exception to the registry after the Advisor.
15. **yes** — `constants.py` still keeps `SPRINT_LENGTH_DAYS`, `ARCHIVE_AFTER_SPRINTS` and
    `DIARY_TIME_DEFAULT`, which one feature each reads.
16. **plan** — `adapters/` is two unrelated boundaries, voice input and Telethon history, and
    both are the shell's rather than a feature's.
17. **check** — `backup.py`, `qa.py` and `recovery.py` sit at the package root; `recovery.py` is
    lifecycle and belongs under `bootstrap/`.
18. **plan** — `Services` names `advisor`, `memory` and `continuity` as fields; each stops
    being a feature name for a different reason, all three in the plan.
19. **check** — `features/workspace_mutator/state.py` builds one block out of every entity, the
    other place a single module knows the whole roster.
20. **yes** — the package profile, the file profile_settings.feature and the "⚙️ Settings" button
    are three names for one screen.
21. **check** — agents.feature, screens.feature and telegram_history.feature have no package,
    and advisor, workspace_mutator, home and retro have no scenario file, against one package
    per scenario file.
22. **plan** — `Services` is the whole application's container but lives in `shell/`; the shell
    moves, so the container moves with it and the import is right after the move.
23. **check** — `foundation/screens.py` carries `ScreenCommand`, `ScreenSpec` and
    `TextInputFlow`, which is Telegram vocabulary in the layer under the domain.
24. **check** — `features/cards/telegram` is eleven modules; the stage lists may want a package
    of their own.
25. **no** — values and tags are the same nine modules twice, but each keeps its own rules, and
    `RecordToolInput` is already the whole of what they share.
