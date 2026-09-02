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

## Candidates — from the owner

2. **first** — the review flow is engine, but `features/proposals/api.py` dispatches on
   `Card | Check`, and 26 and 27 have to land before anything moves under `ai/`.
6. **first** — `AgentSpec` splits into a session spec and a routed spec built on it, and the
   Advisor is the first; a `routable` flag would be the special case under another name.
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
12. **yes** — `ai/subagents.py` `RoutedSubagent` and `module_manifest.py` `AgentSpec` are one
    concept declared twice, one converted into the other; 6 removes one.
13. **yes** — `heavy_analyzer` has no `module.py` and `bootstrap/modules.py` imports its agent
    directly, which is the second exception to the registry after the Advisor.
14. **yes** — `enums.py` still keeps `CardKind`, `Priority`, `Category`, `EnergyType` and
    `ScheduleKind`, which one feature each owns.
15. **yes** — `constants.py` still keeps `SPRINT_LENGTH_DAYS`, `ARCHIVE_AFTER_SPRINTS`,
    `DIARY_TIME_DEFAULT` and `SUMMARY_TRIGGER_TOKENS`, which one feature each reads.
16. **yes** — `adapters/` is two unrelated boundaries, voice input and Telethon history, sharing a
    folder and nothing else.
17. **check** — `backup.py`, `qa.py` and `recovery.py` sit at the package root; `recovery.py` is
    lifecycle and belongs under `bootstrap/`.
18. **check** — `Services` names `advisor`, `memory` and `continuity` as fields, which is the
    fan-out over feature names Rule H forbids everywhere else.
19. **check** — `features/workspace_mutator/state.py` builds one block out of every entity, the
    other place a single module knows the whole roster.
20. **yes** — the package profile, the file profile_settings.feature and the "⚙️ Settings" button
    are three names for one screen.
21. **check** — agents.feature, screens.feature and telegram_history.feature have no package,
    and advisor, workspace_mutator, home and retro have no scenario file, against one package
    per scenario file.
22. **check** — `Services` is the whole application's container but lives in `shell/`, so every
    feature imports the shell to reach the database.
23. **check** — `foundation/screens.py` carries `ScreenCommand`, `ScreenSpec` and
    `TextInputFlow`, which is Telegram vocabulary in the layer under the domain.
24. **check** — `features/cards/telegram` is eleven modules; the stage lists may want a package
    of their own.
25. **no** — values and tags are the same nine modules twice, but each keeps its own rules, and
    `RecordToolInput` is already the whole of what they share.
26. **yes** — `foundation/marks.py` imports `features/cards/model.py` and
    `features/checks/model.py` and branches on `isinstance`, so the layer under everything
    names two features; the entity should answer `is_closed_repeat` for itself.
27. **yes** — `features/proposals/api.py` keeps its own copy of `marks.py`'s
    `is_closed_repeat` and `live_repeat_instance_id` under aliased imports, and
    `validate_named_references` names `Check` where `ReferenceSpec` could carry the refusal.
