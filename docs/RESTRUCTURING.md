# Restructuring

**Every point should be short in one line.**

Nothing here is decided and nothing here is scheduled. A candidate is one line: what moves, and
what it costs or breaks. Each is taken apart on its own later. A candidate leaves this file by
becoming a batch, or by being ruled out in place.

Verdicts: **yes** — the move is right as stated. **split** — part of it is right. **first** —
something else has to happen before it can. **no** — ruled out, kept so it is not raised twice.

## Batch 1 — landed

1. **done** — `QUERY_TOOL` and `query_read_tool` sit in `ai/tools.py`; the runner stayed in
   `ai/sql.py`.
3. **done** — `features/home` owns the menu screen and `MENU_LAYOUT`; a screen declares a `title`
   and never a row.
4. **done** — the Backlog, the Sprint and Today are one `stage_list_block`, and the retro is
   `features/retro`.
5. **done** — the subagent is `features/workspace_mutator` and the set it keeps is the workspace.

Candidate 2 did not land. `proposals/api.py` dispatches on `Card | Check`, so moving it
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

`tg_agent_shell/ai/` and the review flow name nothing of Safwa's, and the rule that read zero
while that held was Rule N. What they could reach was
`foundation/{clock,errors,models,references,screens}.py`, each free of any entity — the five that
travelled with them in phase 4, where Rule F took over.

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
importing the registry. Travelling together is what was actually wanted, and one package is it.

### Benefits

- The engine's dependency on Safwa was invisible because it ran through modules, not names:
  `ai/sql.py` never imported a Card, it imported `constants.py`, which mentions Sprints. The rule
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

## tg-agent-shell — landed

The engine, the review flow, the root session, the shell, the turn lease and the cues are one
reusable thing: `llm_gateway <- agent_runtime <- tg_agent_shell <- safwa`. All four phases are
done.

tg-agent-shell is the distribution and `tg_agent_shell` the package, because an import name
cannot carry a hyphen. It is a shell rather than a harness — a harness drives a model, which is
`agent_runtime` one layer down, and this is where a person reaches the agent.

### What the move actually was

`src/` held four packages. Three of them — `llm_gateway`, `agent_runtime`, `telegram_llm` —
were already self-contained, and Rule F is what keeps them so. The goal was a fourth of the
same kind.

Its code was written already, inside `safwa/`: `ai/`, `shell/`, `turn/`, `cues/`, `adapters/`,
`features/proposals/`, and five files of `foundation/`. Fifty-seven modules.

They could not simply be moved, because they made **43 imports out of the rest of Safwa**. Move
the directories and those 43 become `ImportError`, so the whole job was taking them to zero —
after which the move itself was `git mv` plus import paths.

### Two kinds of import, and only one of them is work

**Moves.** The imported thing already belongs to the shell and only sits in the wrong file.
`MessageKind` names the kinds of bot message — dialogue, cue, summary, dashboard, screen —
which is the vocabulary of whatever sends and marks them. It lives in `enums.py` for no
reason but history, so twelve shell modules reach outside for it. Cut, paste, rewrite the
import lines. Nothing is decided along the way.

**Seams.** The shell genuinely needs something of Safwa's, and moving it is not an option
because it *is* Safwa. The window used to end at a Summary, and a Summary is a feature —
another project may want none, or a different one. But a window does have to end somewhere,
so the dependency inverts instead: the shell declares what it needs, and the composition root
binds Safwa's feature to it. The shell never learns that what it got is a summary.

Two facts make the seams cheaper than they look. `ai/messages.py` already declares `Memory`
as a protocol and takes `workspace_state` as a callable, so *told, not importing* is the
house style. And `close_window` already takes what writes as a callback, so the half of a
seam that hands work back was there before any of this.

### Phase 1 — the moves — **done**, 43 imports down to 16

Twenty-seven of the 43, and nothing to decide in any of them.

- `MessageKind` leaves `enums.py` for `adapters/kinds.py`, and `MARKS` is built there out of
  it. Not `shell/`: `telegram/services.py` imports `adapters/`, so the reverse edge would be a
  cycle. Twelve imports. `TelegramHistorySource` is handed the table rather than importing
  it, which is what a second project needs in order to bring its own kinds.
- Fifteen constants have exactly one reader each and go to it — ten ASR and faster-whisper
  settings to `adapters/asr.py`, `EDGE_CONTEXT_MESSAGE_LIMIT` to
  `adapters/telegram_history.py`, `TOAST_SECONDS` to `telegram/chat.py`, `PAGE_SIZE` to
  `telegram/layout.py`, and the two ASR limits to `turn/dialogue.py`.

Two constants that look like the others are not moves and stay: `SUMMARY_TRIGGER_TOKENS` and
`SCHEDULER_POLL_SECONDS` each have a reader on both sides of the boundary, which makes them
cross-feature tuning and so `constants.py`'s. The shell takes them as parameters, in phase 3.

### Phase 2 — the ratchet — **done**

Rule Q: the modules import nothing outside themselves, with the sixteen left as named
exceptions the way `RULE_H_EXCEPTION` is. After phase 1 rather than before, because a rule
with sixteen exceptions can be read and one with forty-three cannot.

The exception list lived in `scripts/architecture_metrics.py`, blocked by the group that closed
each — and those blocks were the order phase 3 was written in. It was not copied here, because
two copies of a shrinking list is one copy too many.

The rule refused one more import, and equally an exception whose import was already gone.
Without the second half the list stops shrinking and starts growing.

### Phase 3 — the seams — **done**

Each group closed deleted its own block from the exception list, and the list emptied.
A, B and G were carrying rather than inverting, C and D were the window, F was one command in
the wrong package, and E was the root session.

- **A — `Settings` becomes the fields each adapter reads — done**. `build_transcriber` takes
  the eight it read, so `ASRProvider` could follow it into `asr.py` and `config.py` reads it
  from there. `TelegramHistorySource` only ever named `Settings` in a factory and a CLI, and
  both are the application's: they are `bootstrap/auth.py` and the composition root now.
- **B — the plug contract travels — done**. The manifest declares what a feature
  plugs into rather than which features exist, so it is `telegram/manifest.py` now. Its
  `Settings` became the four fields the contexts are actually read for: the owner, the
  timezone, and whether the scheduler runs and how often.
- **C — the window — done**. Where the window ends was four things the package knew: which
  kind, the heading to strip off it, the stripping, and the label the model reads in its
  place. All four are one `WindowEdge` the host is asked on every read, so what ends the
  window can be a different thing on every turn and the package holds no literal of Safwa's.
  `Services.continuity` is the `WindowKeeper` protocol and `close_window` the one method the
  shell calls; the token budget and the token estimate arrive as parameters.
- **D — the Summary's cut — done**. There was no cut to record. The state written on every
  Summary was read nowhere: the window has always found its edge by reading the chat, which
  is where the rule says the dialogue lives. That table, the operation that filled it and the
  message id threaded up to them are gone, and `send_summary` posts and nothing else.
- **E — the Advisor's session — done**. It was eight `ai/` imports, eight `proposals/`
  imports and two Safwa names, so it is `tg_agent_shell/session.py` and the class is `RootSession`:
  what it composes is the engine and the review flow, and neither the prompt nor the persona
  is in it. `MemoryFileStore` is the `Memory` protocol, `workspace_context` is handed in as
  `workspace_state`, and `MAX_TOOL_CALLS` and `SUBAGENT_DEADLINE_SECONDS` had no other reader
  and travel with it. `features/advisor/` keeps `agent.py`, which is what its own `__init__`
  already said it is. It is a module rather than a package because `ai/` may not name the
  review flow and the aiogram surface is not where it belongs either, so it sits at the top of
  the package, beside the two halves it composes rather than inside one of them.
- **F — `command_status` — done**. It reports the workspace mode, the revision and whether
  `memory.md` can be read — three things of Safwa's, so it is `features/diagnostics` now and the
  shell publishes one command, `/cancel`, because the turn is the shell's. `sprint_is_active`
  sat in `telegram/services.py` and was called only by two features, so it is a door on
  `features/planning/api.py`. `Services.memory` is `Memory`, the protocol `ai/messages.py`
  already declared.
- **G — `SCHEDULER_POLL_SECONDS` — done**. Read by `cues/` and by `features/reminders/`, so it
  stays in `constants.py` as cross-feature tuning. It was only ever a default on a parameter
  the one caller already passed, so `run_cue_queue` now requires it.

### Phase 4 — the move — **done**

`git mv`, the import paths, and an entry in `pyproject.toml`. `shell/` became
`tg_agent_shell/telegram/`, because a package named for the shell cannot hold a directory of
the same name, and what is in there is the aiogram surface rather than the idea;
`proposals/telegram/` sits beside it as the second adapter of the one transport. 511 import
lines in 174 files, and `foundation/` split five travelling files from four that stayed.

Rule F covers the package, and Rules N and Q are deleted rather than extended: both existed
only to make this move possible. Rule M stayed and changed target — the engine used to import
no feature and now imports nothing of the package built on top of it, which is the same
property said where it can still be broken.

The move is what makes the rest visible rather than what finishes it: batch 3 is what the
package needed before the test the other three have could be written at all. Candidate 8
blocks nothing — its only tie to the move is that a helper offered by the shape of a SQL
query is a wart the first other project would inherit.

## Batch 3 — landed

The package names no product in what the model reads, and no feature's fields in what the
engine declares.

11. **done** — the six contracts are each in their own feature's `agent.py`, where
    `DiaryToolInput` already was. `ai/contracts.py` went from 547 lines to 294, and what is
    left is the shape of a tool call rather than the fields of one.
27. **done** — the read tool is `query_data`. Its old name was a product's, in the tool list
    every session is shown, so a second project would have inherited it; the schema, the four
    prompts naming it and the snapshot moved together.
28. **done** — `ai/autoapproval.py` keeps how a rule is read — `SCALAR_UPDATE` and
    `RELATIONSHIP_LINK` — and which of an entity's actions has one is a
    `ProposalContribution` field, declared beside the tool whose fields it names.

### Benefits

- Two hardcoded field-name lists in the normalizer are one look at the annotation: a field
  that takes only a list is one a bare value belongs inside, which is what the type already
  said. The third is `content_fields`, declared by the model that has such a field.
- The autoapproval allowlist was the last table keyed by entity name outside a feature.
  Adding an entity no longer means editing it, and a feature that wants none writes nothing.
- The prompt-prefix snapshot moved by exactly five entries — the read tool and the four
  prompts that name it — which is what proves the other six schemas did not change while
  their classes did.
- Five public names in the package were still Safwa's when this landed: three `advisor`
  (candidate 18, batch 4) and two the Sprint gate (candidate 27 below). Until those, the
  vocabulary test cannot be switched on, and that is now the whole of the list rather than an
  estimate.

## Batch 4 — landed

The three public names spelled `advisor` were the root session's under a persona the package
does not own, and the router carried the product's name beside them.

18. **done** — the field is `Services.root`, the prompt builder's is `root` beside `routed`,
    the Cue producer is `add_cue`, and the router is `Router(name="tg_agent_shell")`.

### Benefits

- The field's type has been `RootSession` since group E and the field now says the same, so
  `services.root` no longer promises a persona `features/advisor` owns.
- `messages_for` chooses between `root` and `routed`, which is the one distinction the prompt
  builder makes.
- A Cue's producer says what it does rather than who reads it: `add_cue` beside `next_cue`.
- Two public names in the package are still Safwa's, both the Sprint gate of candidate 27.

## Batch 5 — landed

Two candidates and one property: no public name in the package is Safwa's, and the test the
other three packages have is switched on over all 58 modules.

27. **done** — `sync_bot_commands` publishes what it is handed, and which screens are real
    right now is `available_screens`, in the feature that owns the Sprint.
28. **done** — the persona is `features/advisor/agent.py`'s, `routed_prompt` composes it, and
    a routed subagent carries the prompt it was given.

### Benefits

- The gate was a field on the plug contract and a parameter on the shell's filter, both
  saying Sprint. It is one function beside `sprint_is_active` now, so the shell lost a
  field rather than gaining a euphemism for one.
- The menu and the slash list ask the same function the same question, so they cannot
  disagree about Today; before, each carried its own copy of the condition.
- The engine holds no voice at all: what a subagent reads is composed by the application,
  in the one place that already fills `{views}` and the routing rules.
- The prompt-prefix snapshot did not move, which is what proves the persona changed address
  and not a byte.
- `public_names` and `words` are `tests/vocabulary.py`, read by this test and by
  telegram_llm's rather than copied a third time.
- `action`, `request`, `summary`, `value` and `workspace` are not on the foreign list:
  generic code needs those words for its own things, and a list that cries wolf is not read.

## Candidates — from the owner

2. **no** — the review flow travelled with the engine rather than into it, and moving the
   package under `ai/` would reverse `telegram`, `bootstrap` and `MutationCatalogue`.
6. **done** — the split was not spec-from-spec: the session is shell code and
   `features/advisor` keeps its prompt. Group E.
7. **yes** — continuity splits into summary and memory once continuity.feature does; the two share
   only `persona.py`.
8. **first** — the trigger is hardcoded in `ai/tools.py` as `agent.kind` plus `is_complex_read`, so
   a helper needs its own spec with a predicate — not a hook framework for one subscriber.
9. **split** — `turn/` and `cues/` are the runtime and sit beside the engine now. What is left
   is the second half: `turn/dialogue.py` is a Telegram handler and belongs in `telegram/`.
10. **done** — `remove` named six entities from outside a feature, which is what Rule H started
    reading the moment the package left `features/`. It is `workspace_mutator/remove.py` and the
    subagent that calls it publishes it; proposals keeps the flow and publishes no tool.

## Candidates — found in the same pass

11. **done** — every feature's contract sat in the engine that owns no feature; each is in
    its own `agent.py` now. Batch 3.
12. **yes** — `RoutedSubagent` and `AgentSpec` are one concept declared twice, seven fields
    each; both are in `tg_agent_shell` now, so the duplicate is one package's to settle.
13. **yes** — `heavy_analyzer` has no `module.py` and `bootstrap/modules.py` imports its agent
    directly, which is the second exception to the registry after the Advisor.
15. **yes** — `constants.py` still keeps `SPRINT_LENGTH_DAYS`, `ARCHIVE_AFTER_SPRINTS` and
    `DIARY_TIME_DEFAULT`, which one feature each reads.
16. **yes** — `adapters/` is two unrelated boundaries, voice input and Telethon history, under
    one name that says neither. Both are the shell's, so splitting them is all that is left.
17. **check** — `backup.py`, `qa.py` and `recovery.py` sit at the package root; `recovery.py` is
    lifecycle and belongs under `bootstrap/`.
18. **done** — the three names spelled `advisor` and the router's own name are the
    package's now. Batch 4.
19. **check** — `features/workspace_mutator/state.py` builds one block out of every entity, the
    other place a single module knows the whole roster.
20. **yes** — the package profile, the file profile_settings.feature and the "⚙️ Settings" button
    are three names for one screen.
21. **check** — agents.feature, screens.feature and telegram_history.feature have no package,
    and advisor, workspace_mutator, home and retro have no scenario file, against one package
    per scenario file.
22. **done** — the container moved with the shell, so there is no import left to reverse.
    What the fields are called is candidate 18.
23. **check** — `foundation/screens.py` carries `ScreenCommand`, `ScreenSpec` and
    `TextInputFlow`, which is Telegram vocabulary in the layer under the domain.
24. **check** — `features/cards/telegram` is eleven modules; the stage lists may want a package
    of their own.
25. **no** — values and tags are the same nine modules twice, but each keeps its own rules, and
    `RecordToolInput` is already the whole of what they share.
27. **done** — whether a command applies is the application's to answer, and it answers
    with `available_screens`. Batch 5.
28. **done** — the persona is the product's, and the engine composes no prompt of its own.
    Batch 5.
26. **yes** — the slash list is longer than it needs to be: `/backlog` and `/settings` go, and
    `/sprint` joins `/today` in cards. `/tags`, `/values` and `/reminders` already sit with the
    feature each names, so what is left is two deletions and one move. Whether a command that
    goes keeps its menu button is `ScreenCommand.command = None` and is not settled here.
