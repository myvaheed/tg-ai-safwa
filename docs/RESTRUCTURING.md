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

- `MessageKind` leaves `enums.py` for `foundation/kinds.py`, and `MARKS` is built there out
  of it. Not `shell/`: `telegram/services.py` imports it, so the reverse edge would be a
  cycle. Twelve imports. `TelegramHistorySource` is handed the table rather than importing
  it, which is what a second project needs in order to bring its own kinds.
- Fifteen constants have exactly one reader each and go to it — ten ASR and faster-whisper
  settings to `asr.py`, `EDGE_CONTEXT_MESSAGE_LIMIT` to `history.py`, `TOAST_SECONDS` to
  `telegram/chat.py`, `PAGE_SIZE` to `telegram/layout.py`, and the two ASR limits to
  `telegram/dialogue.py`.

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
  The token budget and the token estimate arrive as parameters.
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

## Batch 6 — landed

One way to declare a subagent, and a registry whose only exception left is the root
session's own prompt.

12. **done** — `AgentSpec` is the declaration, `RoutedSubagent` is the session, and
    `AgentSpec.bind` is the one place the second is built from the first.
13. **done** — `heavy_analyzer` declares a `HelperSpec` in its own `module.py`, and
    `HELPERS` is derived from `MODULES` like every other registry.

### Benefits

- The two dataclasses carried the same docstring and seven fields each. What each is
  says so now: one is what a feature writes, the other what a session runs on.
- `purpose` was written at six construction sites and read at none — the routing rules
  are built from `AgentSpec`, which is the only place a purpose was ever needed.
- The composition root imports one name per feature and reaches into no feature's
  `agent.py`, so the last registry exception is `features/advisor`, which the root
  session is wired with directly.
- `{views}` is filled by one function for a subagent and a helper alike, so a helper's
  prompt cannot fall behind the catalogue its views are built from.
- The prompt-prefix snapshot did not move: the helper's prompt changed address, not a
  byte.

## Batch 7 — landed

A limit one module owns lives in that module, and the owner's message handlers moved to
the surface that registers them.

9. **done** — `dialogue.py` is `telegram/dialogue.py`, beside `commands.py` and
   `callbacks.py`, the two other modules that register `@router` handlers. `turn/` is
   the lease and its row.
15. **done** — six constants left `constants.py` for the module that reads them, and
    each of the four candidate 15 raised was checked on its reader count first.

### Benefits

- `SPRINT_LENGTH_DAYS` and `DIARY_TIME_DEFAULT` are the defaults of two columns and now
  sit above those columns in `profile/model.py`, where a reader of either finds both.
- `ARCHIVE_AFTER_SPRINTS` is Planning's, `SPRINT_PLAN_PAGE_SIZE` and
  `SPRINT_PLAN_TITLE_LIMIT` belong to the one screen that draws that table, and
  `REQUEST_RESULT_LIMIT` to the one screen that lists those rows.
- `SPRINT_LENGTH_MIN_DAYS` and `SPRINT_LENGTH_MAX_DAYS` stayed: Planning and the Profile
  both read them, which is what `constants.py` is for.
- Three comment lines described marks that left in batch 2, and one pointed at
  `ai/contracts.py`, which batch 3 emptied. Both are gone.
- `telegram/dialogue.py` imports its siblings by module rather than the package it is
  inside, so nothing about it is different from `commands.py` any more.

## Batch 8 — landed

`adapters/` was two boundaries and a vocabulary under a name that said none of them.

16. **done** — `asr.py` and `history.py` are one module each at the package root, and
    what a bot message is went to `foundation/kinds.py`.

### Benefits

- The two boundaries are named for what is on the other side of them: a speech service
  and a Telethon session reading the real chat.
- `MessageKind` is not a boundary and never was. It is vocabulary every layer names, so
  it sits with the clock, the errors and the base row, where `ai/` may reach it.
- An import says where a thing lives again: `from tg_agent_shell.history import
  TelegramMessage` rather than a package that also held the microphone.

## Batch 9 — landed

The fifteen constants batch 7 counted and left. Every limit with one reading module went
to it, and what is left in `constants.py` is read on both sides of a boundary.

15. **done** — `constants.py` is six names: two Sprint bounds, the weekday tokens, the
    selector page, and the two the advisor window is measured in.

### Benefits

- A budget is read where it is spent: `MEMORY_TOKEN_BUDGET` and `MEMORY_POLL_SECONDS` in
  `memory/store.py`, the three retell limits in `memory/upkeep.py`, and each background
  loop's interval at the top of the loop.
- `TOKEN_CHARS_ESTIMATE` sits in `foundation/tokens.py`, which is the one estimate every
  budget in Safwa is measured against and now says so in one place.
- `REMINDER_MIN_INTERVAL_MINUTES` and `REMINDER_CATCHUP_GRACE_MINUTES` are schedule
  arithmetic, so they are in `schedule.py` with `MINUTES_PER_DAY`; `use_cases.py` and
  `background.py` already imported it.
- Speech recognition brought its own defaults: `ASRDefaults` and the endpoint and model
  per provider are in `tg_agent_shell/asr.py`, beside `ASRProvider`, and Safwa's settings
  take their defaults from there. A second application gets them without copying a table.
- The provider endpoints are `config.py`'s, which is where `PROVIDER_DEFAULTS` reads
  them, and the two OpenRouter attribution headers are spelled where they are sent.
- `SUMMARY_TRIGGER_TOKENS` and `SCHEDULER_POLL_SECONDS` stayed, as the earlier phases
  recorded: each has a reader on both sides of the boundary. `SUMMARY_TOKEN_CEILING`
  joined them, because it is the other half of the same window.
- The history source is sized by `settings.summary_trigger_tokens` rather than the
  constant behind it, so the setting moves the window and the Summary trigger together
  instead of only the second.

## Batch 10 — landed

`foundation/` is what every layer above may name, and the engine is one of those layers.

23. **done** — `ScreenCommand`, `StartLink` and `TextInputFlow` are
    `telegram/contributions.py`; `ScreenSpec` and `ScreenCatalogue` stay in
    `foundation/screens.py`.

### Benefits

- The split is the engine's reach: `ai/tools.py` names `ScreenCatalogue` to answer
  `open`, and Rule M lets it reach `foundation/` and no further. Nothing under `ai/` ever
  named a slash command, a deep link or an editor.
- `contributions.py` imports nothing of its own package, so both `Services`, which stores
  the three, and `FeatureModule`, which declares them, sit above it and neither imports
  the other.
- A feature's `module.py` now reads where each half lives: what can be cited comes from
  `foundation/`, what the owner taps comes from `telegram/`.

## Batch 11 — landed

What runs during startup lives with the startup.

17. **done** — `recovery.py` is `bootstrap/recovery.py`. `backup.py` and `qa.py` stay at
    the root: neither runs during a boot.

### Benefits

- The only module that imported it is `bootstrap/main.py`, which calls it between the
  feature hooks and the run machinery. It is a sibling now rather than a reach upwards.
- `qa.py` stays beside `config.py`, which is what it guards: a QA `Settings` that reused
  the production token or session path is what it hard-fails on.
- `backup.py` operates on the data directory rather than on a running application, so it
  belongs to neither the composition root nor a feature.

## Batch 12 — landed

One screen had three names. The owner's name is Profile.

20. **done** — the package was already `profile`; the file, the command, the button, the
    header and every scenario that named the screen say Profile now.

### Benefits

- `profile_settings.feature` is `profile.feature`, so the file and the package that keeps
  it are the same word. The `PS` prefix stayed: it identifies scenarios the owner already
  refers to, and `PR` is Proposals'.
- The lowercase half went with it. A value on that screen is a *field*, which is what the
  code has always called it — `ProfileField`, `set_profile_field`, `PROFILE_FIELDS` — so
  the scenarios say field rather than setting.
- The screen's descriptor for one editable value could not take the name `ProfileField`,
  which is the enum those values are named by, so it is `EditableField`.
- `Settings` still means one thing in this codebase: the `SAFWA_*` environment, in
  `config.py`. Nothing on a screen answers to that name any more.
- The prompt-prefix snapshot moved once, deliberately: the Advisor's prompt told the model
  to send the owner to Settings for a Sprint config.

## Batch 13 — landed

A screen the menu already offers does not also need a command line.

26. **done** — `/backlog` and `/profile` are menu buttons and nothing else. `/sprint`
    stayed where it is.

### Benefits

- The published slash list is thirteen instead of fifteen, and every command left is
  something the menu cannot do: a dashboard the owner types straight into, or an
  operation with no screen at all.
- `ScreenCommand.command = None` was already the shape — Add has been a menu button and
  nothing else since batch 1 — so the two deletions are two fields each and no mechanism.
- `tests/e2e/test_startup_e2e.py` now asserts what is published *and* what is not, so a
  command line added back by accident fails there.
- `/sprint` did not move to cards. The candidate's own rule is that a command sits with
  the feature it names, and the Sprint screen is Planning's: declaring it in `cards`
  would make the Cards manifest contribute a Planning screen and make Cards import it.

## Batch 23 — landed

A failure in an extension point was reported as a failure of the owner's request.

Work that runs after the answer sat inside the turn's own `try`, so a Summary that could not
be written told the owner "Safwa could not complete that request. Your planning data was not
changed." — after the answer was in the chat and the change was saved. And a watcher that
raised reached them as its bare exception text, naming neither the feature nor the call.

### Benefits

- `run_after_turn` runs each piece under its own `except`. A failure says which work failed and
  that the answer stands, and the pieces after it still run. One broken subscriber cannot
  silence the rest, which is what a list of them is for.
- `WatcherFailed` names the watcher and the call it fell over on, before or after. The owner
  reads which feature broke rather than a sentence with no subject.
- The turn still ends when a watcher raises. That half was right: a refusal that did not finish
  is not a decision to allow the call, and the request genuinely did not complete.
- `AG-TURN-034` is the rule that was missing, and the two watcher scenarios say what the owner
  is told. The old wording had been true of the Summary since long before it was a
  contribution; making it an extension point is what made it worth fixing.

## Batch 22 — landed

The engine decided when a helper was worth calling, and in what words.

The gate ended in `capped or is_complex_read(sql)` — one regular expression that
was written for the heavy analyzer and stood as the answer for any helper. `HelperSpec` carries
both halves now: `offer_when(sql, rows)` and the `offer` the model reads. The engine asks each
helper in the order the features declared them and hands back the first answer.

### Benefits

- The engine holds no opinion about SQL any more. `is_complex_read` and the new `is_capped` stay
  in `ai/sql.py` as facts about what a read came back as, and `heavy_analyzer/agent.py` composes
  them into `worth_a_helper`, which is `HAN-OFFER-001`, `002` and `003` in one line, in the
  feature that owns those scenarios.
- The offer wording was built from the helper names in `ToolAdapters.__init__`, so the sentence
  a small model reads was the engine's. It is `OFFER` in the feature, next to the prompt it goes
  with.
- `HelperPort` replaces the bare callable the adapters held. One value carrying how to call a
  helper, when it is earned and in what words beats three mappings keyed by the same name.
- `AG-TOOL-033` is the half that stayed the engine's, written down for the first time: a result
  that failed already carries one instruction, and nothing is added beside it. `HAN-OFFER-004`
  cites it rather than restating it — the rule protects every helper, not this one, and a feature
  that had to remember it would be the feature that forgets.

## Batch 21 — landed

Summaries and memory were one package because one class held both.

One class held both, with two public methods and no field they both read: `close_window` never
touched the memory store. They are `DialogueSummary` in `features/summary` and `MemoryUpkeep`
in `features/memory`, and the shell learned to say when a turn is over instead of holding one
of them itself.

### Benefits

- `Services` loses its `continuity` and `memory` fields, and the protocol that typed the first
  of them. Both were the application's objects sitting in the shell's container, and that
  protocol was invented for one object. What is left on `Services` is what the shell reads.
- `after_turn` is what replaces the field: a `FeatureModule` contribution the shell runs once
  the owner's turn has been answered, reading nothing back. The Summary is its first
  subscriber, and it takes the background lease itself rather than being handed one.
- `Services.features` is where Safwa's own objects live now — carried and never read, the way a
  session carries `host_state`. Each feature reaches its own through its `api.py`, so a handler
  names one object rather than a container of everything.
- CO is retired rather than reused. Two packages cannot share a numbering line, and the
  identifiers are never renumbered inside their own, so `SUM` and `MEM` start at 001 and
  `tests/brd/README.md` says why the old prefix is gone.
- The split is 2 scenarios against 9. That is the shape the code already had: the Summary is
  one provider call over the window, and memory is a file, a cursor, a schedule and a cache.
- The file-name list in `docs/FEATURE_MODULES.md` calls itself the whole vocabulary and asks
  a batch that needs a new name to answer for it. Two roles were already in the tree and
  missing from it: the background tasks four features declare, and the long-lived
  collaborator the composition root builds, which is what the class split here had been. Both
  are named there now.

## Batch 20 — landed

Three places in the shell were extension points named after their one subscriber.

`ToolAdapters.run` now runs two lists around the call it dispatches. A `before_tool` watcher
is given the session and the call, and answers with a result to refuse it or with nothing to
let it run. An `after_tool` watcher is given the call and what it produced. Both are
`FeatureModule` fields, collected in `MODULES` order like every other contribution.

### Benefits

- The refusal needs no new vocabulary. `refuse_mixed` already hands the model a result in
  place of a tool's, so a watcher answers in the shape the model already reads.
- A watcher that raises ends the turn rather than being stepped over. A refusal that did not
  finish is not a decision to allow the call, and a broken watcher is a bug in the feature
  that declared it, so it fails loudly.
- `AG-TOOL-031` and `AG-TOOL-032` are the mechanism written down, with four tests in
  `tests/test_tool_watchers.py`. No feature declares a watcher yet; this is the same shape
  `run_poll` landed in, where the shell rule has a scenario and a test of its own.
- `route` reaches neither list, and that stays a fact of `agent_runtime/loop.py` — it answers
  a route before the adapters are reached. It is in the `run` docstring rather than in a
  scenario, because it is not a rule this mechanism keeps.

## Batch 19 — landed

The engine decided whether to offer a helper by reading a Safwa word.

The helper gate opened with `agent.kind != "advisor"`. It asks whether this is the
session that talks to the owner, and the engine already answers that question elsewhere:
`definition()` calls a session root when `self.subagents.get(kind) is None`. That is the test
now.

### Benefits

- The last behavioural Safwa literal is out of `tg_agent_shell/ai/`. What is left of the word
  is prose — docstrings, log lines, and the `kind: str = "advisor"` defaults in
  `agent_runtime` — none of which anything branches on. `ai/conversation.py` still renames the
  assistant role to `Advisor` in the text the model reads; that is a second literal doing a
  different job, and it is named here rather than changed.
- No new field. A helper offer for a second bot's root session works with nothing declared,
  because being root is something the engine can already see.
- Behaviour is unchanged: the Advisor is the root session, and no other session reaches
  `ToolAdapters` — a helper runs as a mini session, which has its own runner and no tool
  port at all.

## Batch 18 — landed

Five background loops were the same six lines, written five times.

`foundation/poll.py` holds `run_poll`: one tick, a log if it raised, and the wait before the
next. The Cue queue, the Reminder poll, the Sprint expiry poll and both memory polls call it.

### Benefits

- Three of the five carried `except asyncio.CancelledError: raise` ahead of `except Exception`.
  It never ran: `CancelledError` is a `BaseException`, so `except Exception` was never going to
  catch it. The two loops without that clause behaved identically, which is the tell. `run_poll`
  says in its docstring why there is no such clause, so it does not come back.
- One log line for a poll that fell over, named by the poll. There were five wordings.
- The tasks stay five. Their intervals run from 5 seconds to 300, and one loop at the finest of
  those would run the Sprint check sixty times too often and put every poll in one queue, where
  a slow memory sync delays a Cue. More than that, the Reminder tick writes nothing while a Cue
  is still waiting — that is `RM-GATE-017`, and merging the two would turn a rule into the order
  of two statements.
- The line count barely moves. What moved is that the loop has one home, so a fix to it is one
  edit rather than five that have to be found first.
- `AG-POLL-030` is that home written down, with a test of its own: a round that raises does not
  end the timer, and shutting down still does. `RM-POLL-021` now says only what is the
  Reminders' own — that they do not stop firing for the rest of the day — and cites it.

## Batch 17 — landed

A rule the shell keeps and no scenario states is a rule only the code remembers.

Batch 16 left seven scenarios leaning on shell machinery with nothing to cite. Five of those
rules are now written down, and six of the seven cite one.

### Benefits

- `AG-READ-027` is what CLAUDE.md calls "a reader is scoped by the view list in its prompt",
  approved for the first time. Five tests in `tests/test_ai_sql.py` and
  `tests/test_heavy_analyzer.py` were proving it under no rule at all.
- `AG-CUE-029` is the Cue queue: what Safwa owes stays owed until it reached the chat, and is
  never said twice. Seven tests move onto it, five of them from `PL-END-015` — a scenario about
  a Sprint ending, which is what the queue was carrying the day it was written.
- `AG-HELPER-028`, `SC-PAGE-007` and `SC-INPUT-008` do the same for the helper's budget, the
  paged list and the typed-value screen. Each had a tested mechanism and no rule.
- `RM-POLL-021` got no target. The loop that survives its own failures is
  `reminders/background.py`, Safwa's own; the Cue queue has a second one of its own. Two
  implementations of a habit are not one shell rule, and writing one down to give a reference
  somewhere to point would be inventing a rule to fit a shape.

## Batch 16 — landed

The scenarios were in the right files; some of the rules in them were not.

Every scenario under `tests/brd/` was read against one question: if this rule changed, whose
code would have to change? Five answered `tg_agent_shell` and moved; four leaned on a rule
another scenario already states and now cite it.

### Benefits

- `AG-TURN-024` is the lease CLAUDE.md calls "one lease, and the owner always wins", written
  down for the first time. It took in `CO-GENERATION-011`, `CO-SUMMARY-003` and twelve tests
  from three files — eight of which were `TurnManager` unit tests filed under a Reminders
  scenario.
- `RM-GATE-018` needed no move: `AG-TURN-015` already said it word for word. A duplicate of an
  approved rule is worse than a missing one, because nothing fails when the two drift.
- `AG-HELPER-025` and `AG-HELPER-026` are the helper's session rules, which the heavy analyzer
  only configures.
- `TG-SUMMARY-006` is the window's rule again: it ends at the newest message that stands for
  what came before, and which message that is belongs to whoever fills the seam.
- Four scenarios that lean on the proposal flow, the turn or the marking now name the rule they
  lean on, so a reader can tell a rule from a consequence of one.
- What was left alone is named: `RM-FIRE-013`, `SR-SQL-004`, `TA-PICK-007`, `PS-UI-SAVE-008`,
  `PS-UI-INVALID-009`, `HAN-ASK-011` and `RM-POLL-021` all rest on shell machinery that no
  scenario describes, so there is nothing to cite yet.

## Batches 14 and 15 — landed

Every package has a scenario file, and every scenario file has a package.

21. **done** — the four files that describe the shell are `tests/brd/tg_agent_shell/`, and the
    five packages that had no scenarios have their own. Batches 14 and 15.

### Benefits

- The rule reads both ways now, so a package nobody wrote a rule for is as visible as a rule
  whose package is gone. `tests/test_brd_traceability.py` walks the tree rather than one
  directory, and a citation names the file it is actually in.
- A second bot built on the shell inherits four scenario files and leaves the rest behind. The
  directory is what says which is which, and it is the same boundary Rule F already holds.
- The `/status` command had no rule anywhere and no test of its own. It has two of each.
- Five packages were having their rules read out of other features' scenarios. Where that was
  the right home it stayed there: the workspace state block is still described line by line by
  the features that own each line, and Today leaving the menu is still `PL-MODE-001`. What was
  written down is only what no file was stating — the menu as a list, the deep link back in,
  the retro screen, the one voice every part of Safwa answers in, and the one way to delete.
  What opening an item means went to `screens.feature` rather than to the Advisor: the tool
  and the screen behind it are both the shell's, and only the line saying *when* to call it
  is Safwa's.

## Candidates — from the owner

2. **no** — the review flow travelled with the engine rather than into it, and moving the
   package under `ai/` would reverse `telegram`, `bootstrap` and `MutationCatalogue`.
6. **done** — the split was not spec-from-spec: the session is shell code and
   `features/advisor` keeps its prompt. Group E.
7. **done** — `close_window` never touched the memory store, so the two shared a constructor
   and nothing else. They are `features/summary` and `features/memory`, and
   `continuity.feature` is `summary.feature` and `memory.feature`. Batch 21.
8. **done** — the trigger was hardcoded in `ai/tools.py` as `agent.kind` plus
   `is_complex_read`. The kind half went first: a helper is offered to the root session,
   which the engine can see. Batch 19. The predicate and the wording are `HelperSpec`
   fields now, answered by the feature that declared the helper. Batch 22.
9. **done** — `turn/` and `cues/` are the runtime beside the engine, and the handlers are
   `telegram/dialogue.py`. Batch 7.
10. **done** — `remove` named six entities from outside a feature, which is what Rule H started
    reading the moment the package left `features/`. It is `workspace_mutator/remove.py` and the
    subagent that calls it publishes it; proposals keeps the flow and publishes no tool.

## Candidates — found in the same pass

11. **done** — every feature's contract sat in the engine that owns no feature; each is in
    its own `agent.py` now. Batch 3.
12. **done** — the declaration is `AgentSpec`, the session is `RoutedSubagent`, and
    `AgentSpec.bind` is what turns one into the other. Batch 6.
13. **done** — the helper is a `HelperSpec` in `heavy_analyzer`'s own `module.py`, so
    `MODULES` reaches every feature. Batch 6.
15. **done** — those three plus `SPRINT_PLAN_PAGE_SIZE`, `SPRINT_PLAN_TITLE_LIMIT` and
    `REQUEST_RESULT_LIMIT` live with the module that reads them. Batch 7.
16. **done** — `asr.py`, `history.py` and `foundation/kinds.py`, each named for what it
    is. Batch 8.
17. **done** — `recovery.py` is `bootstrap/recovery.py`; the other two stay at the root.
    Batch 11.
18. **done** — the three names spelled `advisor` and the router's own name are the
    package's now. Batch 4.
19. **check** — `features/workspace_mutator/state.py` builds one block out of every entity, the
    other place a single module knows the whole roster.
20. **done** — the screen is Profile everywhere: the package, `profile.feature`, `/profile`
    and the "⚙️ Profile" button. Batch 12.
21. **done** — the shell's scenarios are `tests/brd/tg_agent_shell/`, and advisor,
    workspace_mutator, home, retro and diagnostics have a scenario file each. Batches 14
    and 15.
22. **done** — the container moved with the shell, so there is no import left to reverse.
    What the fields are called is candidate 18.
23. **done** — the Telegram half is `telegram/contributions.py`, and `foundation/`
    keeps what the engine names. Batch 10.
24. **no** — eleven modules, and the biggest is 375 lines against a 600 threshold that one
    module in the repo crosses, none of them here. Inside is a DAG with no cycle:
    `presentation.py` is the leaf five of them read, `handlers.py` the dispatcher nothing
    reads. Outside, three imports reach in, all through the package `__init__` and none into
    a submodule, so there is no boundary to repair. The stage lists are `lists.py`, one
    module: a package of one is not a package. Revisit if a module here passes 600 lines, or
    if an import names a submodule.
25. **no** — values and tags are the same nine modules twice, but each keeps its own rules, and
    `RecordToolInput` is already the whole of what they share.
27. **done** — whether a command applies is the application's to answer, and it answers
    with `available_screens`. Batch 5.
28. **done** — the persona is the product's, and the engine composes no prompt of its own.
    Batch 5.
26. **done** — `/backlog` and `/profile` keep their menu button and lose their command
    line; `/sprint` stayed with Planning, which is the feature it names. Batch 13.
