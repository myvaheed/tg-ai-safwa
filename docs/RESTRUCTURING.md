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

## tg-agent-shell — a plan, not a schedule

The engine, the review flow, the shell, the turn lease and the cues are one reusable thing:
`llm_gateway <- agent_runtime <- tg_agent_shell <- safwa`. Nothing below is started. It is
written down because the measurement is the expensive part and it is already done.

tg-agent-shell is the distribution and tg_agent_shell the package, because an import name
cannot carry a hyphen. Neither is backticked below: the name is planned, and a backtick here
means a name the code already carries. It is a shell rather than a harness — a harness drives
a model, which is `agent_runtime` one layer down, and this is where a person reaches the agent.

The candidate set is 49 modules — `ai/` 11, `features/proposals/` 15, `shell/` 10, `cues/` 6,
`turn/` 4, `adapters/` 3 — plus `foundation/{clock,errors,models,references,screens}.py`. It
names Safwa in eight places. Two facts make the rest cheap: `ai/messages.py` already declares
`Memory` as a protocol and takes `workspace_state` as a callable, so *told, not importing* is
already the house style; and `maybe_summarize` already takes `send_summary` as a callback, so
the history seam is half inverted already.

Mechanical, and none of it moves anything:

- `MessageKind` is the dialogue store's vocabulary and `AIProvider`/`ASRProvider` are
  tg_agent_shell's; they leave `enums.py`.
- `constants` reaches `shell`, `turn` and `cues`; each constant goes where it is read.
- `adapters/telegram_history.py` imports `SUMMARY_HEADER`; the header becomes a parameter.
- `bootstrap/module_manifest.py` is the plug contract, not the roster; its `Settings` becomes the
  four fields tg_agent_shell actually reads.
- `shell/` becomes `tg_agent_shell/telegram/`, because a package named for the shell cannot hold
  a directory of the same name, and what is in there is the aiogram surface rather than the idea.
  `features/proposals/telegram/` lands beside it as the second adapter of the one transport.
- `command_status` is the only reason `Services.memory` and `Workspace` are in the shell.

Three seams, and they are the real work:

- **The Advisor's session.** `features/advisor/session.py` is shell code: eight `ai/` imports,
  eight `proposals/` imports, and two Safwa names — `MemoryFileStore`, which the `Memory`
  protocol already covers, and `workspace_context`, which the composition root can hand over.
  The package keeps `agent.py`, which is what its own `__init__` already says it is.
- **The Summary's cut.** `shell/chat.py` calls `record_summary`; the callback returns the message
  id instead and `PersonaContinuity` records its own cut.
- **The window.** `Services.continuity` is `turn/dialogue.py` calling one method;
  tg_agent_shell declares that method and Safwa binds it. Summary stays a feature —
  tg_agent_shell manages the window, it does not decide what goes in it.

Then the move, and Rule F covers the package: Rule N is deleted rather than extended. Candidates 6,
12, 16, 18 and 22 are answered by the three seams, so none is worth doing on its own.

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
15. **yes** — `constants.py` still keeps `SPRINT_LENGTH_DAYS`, `ARCHIVE_AFTER_SPRINTS`,
    `DIARY_TIME_DEFAULT` and `SUMMARY_TRIGGER_TOKENS`, which one feature each reads.
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
