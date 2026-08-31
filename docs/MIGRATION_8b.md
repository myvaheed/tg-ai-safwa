# Phase 8.b — the handlers go to the features, and the turn gets a name

The plan for the last batch that moves production code. [MIGRATION.md](MIGRATION.md) records what
every phase delivered; this file records what 8.b is going to do, before it does it, and is folded
back into `MIGRATION.md` as "What Phase 8.b delivered" when it is finished.

8.a sent the chat out of Safwa. 8.b spreads what is left across the features that own it, replaces
`GenerationGuard` with a named turn, and empties the flat modules. After it, `src/safwa/telegram/`
does not exist.

## Where 8.b starts

Measured after the 8.a review landed.

```
181 modules. Rule F 0, Rule G 2, Rule H 24, Rule L 1. DoD #1 24, DoD #3 3.
814 passed, 3 skipped. ruff clean. prompt_prefix.json and schema.json unmoved.

src/safwa/telegram/   6329 lines, 16 modules
  callbacks.py 1282   cards.py 980   dialogue.py 447   plan.py 437   checks.py 433
  _core.py 425   commands.py 382   _messaging.py 282   sprint.py 267   _presentation.py 263
  items.py 252   screens.py 228   text_input.py 193   reminders.py 193   proposals.py 193
  __init__.py 72

src/safwa/history.py 392   src/safwa/domain.py 190   src/safwa/main.py 250
src/safwa/foundation/state_flow.py 230, imported by nothing under src/

CALLBACK_ACTIONS: 70 entries.  BOT_COMMANDS: 15.  text-input flows: 7 branches in dialogue.py.
The messaging adapters are called from 189 places.
tests/test_telegram_item_ui.py is 134 KB — the only remaining test monolith.
```

## What the reading of the code changed in the plan

Six things, each of which breaks a stated exit criterion if it is not planned for. They are the
reason this file exists rather than the table in `MIGRATION.md` being followed as written.

### 1. Criterion 12 cannot be met by steps 5 and 6

`DoD #3` counts modules over 600 lines. The criterion says `4 → 0`; the 4 was measured when
`history.py` was 653. It is 3 today, and only two of the three are dissolved by this batch:

```
1282  telegram/callbacks.py           dissolved by step 5
 980  telegram/cards.py               dissolved by step 5
 816  features/cards/use_cases.py     touched by no step
```

The third is already a feature module in its right place.

**Decision, taken by the owner 2026-08-31: soften the criterion rather than split the module.**
Criterion 12 reads **DoD #3 3 → 1**, and the one is `features/cards/use_cases.py`. §17 asks for a
named reason rather than a number, and the reason is the one its own docstring already states:

> A Sprint commitment follows an Action's stage, and every writer of that stage is in this file:
> each one calls Planning's door afterwards, and no Card row here ever touches a commitment itself.

That property is checkable by opening one file, and only while there is one file. Splitting the
module would scatter the writers of an Action's stage across three, and the guarantee would become
something a reader has to reassemble. A `rules.py` extraction is available later if the module keeps
growing for a different reason; it is not owed now.

### 2. Criterion 11 reaches outside `telegram/`

Rule H is 24, and three of them are not in the Telegram package:

```
ai/contracts.py:500   collection over card, check, diary, request, tag, value
ai/tools.py:56        dict over card, check, diary, request, tag, value
history.py:97         collection over card, check, diary, request, tag, value
```

So the catalogue of openable and citable items is a **`FeatureModule` contribution**, not only a UI
mechanism. Step 4 is written as "commands, callback actions and text-input flows" and does not name
it, while step 6 depends on it — 8.a deferred the citations move onto "the treatment `SqlView`
already got". It is named in step 4 here.

### 3. That catalogue collides with criterion 14

`OpenInput.item_type` is `Literal["card", "check", "tag", "value", "request", "diary"]`, and its
digest is in `tests/snapshots/prompt_prefix.json` under `tool:open`. Assembled from `MODULES` the
order would be `card, check, value, tag, diary, request` — two swaps, and the snapshot moves.
`MODULES` cannot be reordered instead: the same order feeds `_routing_rules()` and `{views}`, so it
would move `SYSTEM_PROMPT` as well.

**Decision, taken by the owner:** the `Literal` stays hand-written in `ai/contracts.py`. A tool's
enum is prompt text, and prompt text is written where the model reads it. The catalogue owns
behaviour only — which model class to load, which screen to open, which citation label to build.

Consequence: criterion 11 reads **Rule H 24 → 1, and the one is the `open` tool's enum**, with that
sentence as its named reason. `history.py:97` and `ai/tools.py:56` still go to zero.

### 4. Rule G is 2 and one of them is inside the package

```
adapters/asr.py:283     asyncio.create_task(asyncio.to_thread(decode))
telegram_llm/host.py:338 asyncio.create_task(self._expire_toast(...), name="toast-expiry")
```

The second arrived in the package with `_messaging.py` in 8.a. `LIFECYCLE_MODULES` names only
`safwa/main.py` and `safwa/bootstrap/main.py`, and a reusable package can never be in Safwa's list,
so `Rule G 2 → 0` is unreachable as long as either module starts its own task.

This is not a formality. A Toast timer started inside `telegram_llm` outlives Safwa's shutdown:
nothing cancels it, because nothing knows it exists.

**Decision, taken by the owner 2026-08-31: neither module starts a task.** Both take the start from
outside — one `spawn` callable supplied by `bootstrap/main.py`, which is the module that can also
cancel it. `Rule G 2 → 0` holds honestly. Landed in step 6.

### 5. Step 5 has no declared home for the shell

"`telegram/__init__.py` is deleted" leaves `router`, `Services`, `OwnerAndWritingMiddleware`,
`CallbackContext`, `callback_token_handler`, `BOT_COMMANDS`, `sync_bot_commands`, `menu_markup` and
`start_payload` with nowhere to be. None belongs to a feature, and none may go into `telegram_llm`,
which cannot import Safwa.

**Decision: `src/safwa/shell/`.** It is Safwa's Telegram application — the router, the container, the
middleware, the menu and the token dispatch — and it is the only module a feature's `telegram.py` is
allowed to import besides `telegram_llm`.

### 6. 8.b has no line-count ceiling on what it produces

8.a had criterion 6, "no module produced by this batch is over 400 lines", and it is what would have
caught a `use_cases.py` at 816. 8.b produces far more modules than 8.a did.

**Decision: the same criterion applies to 8.b**, as criterion 17.

## The steps

Each one is green on its own, the way 7.b was landed in five. Step 5 is split into five because
rolling back 4500 lines and seventy callback actions as one piece is not a safety net.

| Step | What | Checked by | |
|---|---|---|---|
| 1 | `AG-TURN-010` amended, `AG-TURN-022` and `AG-TURN-023` written; the queue tests deleted by name | `test_brd_traceability` | **done** |
| 2 | The queue goes; `TurnManager` arrives in `src/safwa/turn/` | 3 scenarios | **done** |
| 3 | `foundation/state_flow.py` deleted, the states kept | criterion 10 | **done** |
| 4a | `FeatureModule` grows `screens`; the entity list collapses from four copies to one | Rule H 24 to 10 | **done** |
| 4b | `FeatureModule` grows `commands` | `BOT_COMMANDS` gone | **done** |
| 4c | `FeatureModule` grows `callback_actions` | `CALLBACK_ACTIONS` gone | **done** |
| 4d | `FeatureModule` grows `text_inputs` | 7 flow branches gone | **done** |
| 5a | The shell: `src/safwa/shell/`; the host is built once and owns its own state | criterion 17 | **done** |
| 5b | Cards and Checks | criterion 17 | **done** |
| 5c | Planning and the Sprint | Rule E gains its second door | **done** |
| 5d | Values, Tags, Requests, Reminders, Profile | Rule H 8 to 3 | **done** |
| 5e | Proposals; `src/safwa/telegram/` does not exist | criterion 8 | |
| 6 | `domain.py`, `history.py`, `main.py`; Rule G; Rule L; `CARD_EDITOR` → `EDITOR` | criteria 11, 12, 19 | |
| 7 | Allowlist regenerated; `CLAUDE.md` and `README.md` corrected by deleting | criterion 18 | |

**Steps 1 and 2 land together.** `test_brd_traceability` fails on an approved scenario with no
test, and `AG-TURN-010`'s only citation was the queue test the amendment deletes. Writing the
scenarios without the code that makes them true cannot be green, so the two are one commit.

### Step 1 — the scenarios

Write into `tests/brd/agents.feature` from [brd/agents_turn.md](brd/agents_turn.md), approved
2026-08-30: `AG-TURN-010` replaced with the amended text, `AG-TURN-022` and `AG-TURN-023` added.

Delete by name, because they assert behaviour the amendment removes:

```
tests/test_telegram_item_ui.py::test_messages_are_queued_with_placeholders_and_restored_as_one_turn
tests/test_telegram_item_ui.py::test_voice_arriving_during_generation_is_queued
tests/test_telegram_item_ui.py::test_a_queued_transcript_is_previewed_and_split_on_drain
```

The other four in `test_telegram_item_ui.py` that reach `materialize_queued_dialogue` are about
something else and are rewritten, not deleted; the `queued` names in `tests/e2e/` are the proposal
queue, a different word, and are untouched.

### Step 2 — the queue goes and `TurnManager` arrives

**Where it lives: `src/safwa/turn/`.** Settled by the owner. A turn is the same kind of thing as a
Cue: a process that lives outside `features/` and whose rules are written under the identifiers of
the features it serves. Its rules stay `AG-TURN-*` in `agents.feature`, which settles the conflict
§9.2 creates with `tests/brd/README.md`.

Its identity, which §9.1 requires be named: **the Telegram message id being answered**, held in
memory only. A turn does not survive a restart, and nothing in the plan asks it to.

The union, which is §9.3's minus `queued`:

```python
type TurnState = Idle | Answering | BackgroundWork | Suspended | Cancelling
```

`dialogue_revision` stays a separate monotonic counter and does not enter the union: it is an
invalidation token for a request already sent to the provider, not a state.

Deleted here: `QueuedMessage`, `GenerationGuard.begin_queue` / `finish_queue` / `abort_queue` /
`drain_queue`, `queue_messages` as a field, `queue_owner_text`, `materialize_queued_dialogue`, the
`while True` loop in `run_dialogue_turn`, `QUEUE_PREVIEW_CHARS`, and `voice_message`'s "the lease is
settled only now" branch. About 200 lines.

`send_owner_turn` stays: a transcription still reaches the conversation through it, `TG-RELAY-004`.

What replaces the queue is one message in the chat while an answer is written, carrying a tappable
`/cancel`, removed when the answer arrives or when the owner cancels — `AG-TURN-022`. Anything else
the owner sends meanwhile is taken out of the chat and dropped, and a recording is not even
downloaded, which is also what stops Safwa paying to transcribe something it will discard.

### Step 3 — `state_flow.py`

230 lines written in Phase 0, imported by nothing under `src/` ever since. Phase 7.c already dropped
a frozen union and a reducer for the same reason and set the precedent.

**Deleted, with `tests/test_state_flow.py`, and the states kept.** After step 2 the turn has one
writer and its readers ask it directly — `active`, `background`, `source_message_id` — on a
`Services` they already hold. Nothing subscribes, and the message `AG-TURN-022` puts in the chat is
an effect of a transition rather than a subscription to one. Rule C's second half, which forbade a
Manager returning a `MutableStateFlow`, went with it: it named a type nothing can produce any more.

### Step 4 — what `FeatureModule` grows

Four contributions, added to [bootstrap/module_manifest.py](../src/safwa/bootstrap/module_manifest.py):

| Contribution | Replaces | Removes |
|---|---|---|
| `commands` | `BOT_COMMANDS` and the 15 `@router.message(Command(...))` handlers in `telegram/commands.py` | one central list |
| `callback_actions` | `CALLBACK_ACTIONS`, 70 entries in `telegram/callbacks.py` | DoD #1's largest single fan-out |
| `text_inputs` | the 7 `flow == "..."` branches in `telegram/dialogue.py` — `item`, `reminder`, `sprint`, `settings`, `card_create`, `card`, `card_blocked` | `dialogue.py`'s imports of six features, and the deferred import that `features/profile/screens.py` needs today |
| `screens` | `telegram/screens.py`'s `OPENABLE_MODELS`, `_citation_label` and `open_item_screen`, and `ai/tools.py`'s duplicate `OPENABLE_MODELS` | 3 of the 4 copies of the entity list |

`screens` carries, per item type: the ORM model, the coroutine that opens its screen, and the
coroutine that builds its citation label. Assembled at import time, like `MODULES` already is —
`CITATION_PATTERN` is compiled at import and read there.

The manifest's own rule applies and is worth restating: a capability does not get a field by
default. Each of these four is plugged in by six features or more, which is what earns it one.

**Step 4 lands as four commits, one per contribution.** The same argument that split step 5:
each of the four is an independent mechanism, and the handlers they register are still in
`src/safwa/telegram/` until step 5 moves them. A contribution declared in step 4 names a callable
that has not moved yet, so `features/<name>/module.py` imports it out of `safwa/telegram/` for one
step — one import line per feature, rewritten when the handler moves. The declaration itself is
written once.

### What step 4a landed

792 passed, 3 skipped; `ruff check .` clean; 186 modules; **Rule H 24 to 10, DoD #1 24 to 10**;
Rule G 2, Rule L 1, DoD #3 3; cycles 0. `prompt_prefix.json` and `schema.json` unmoved. The
allowlist was regenerated here rather than in step 7, because a rule that starts passing has to
reset its own ratchet in the batch that fixed it.

**`ScreenSpec` and `ScreenCatalogue` live in `foundation/screens.py`, not in the manifest.** Putting
them in `bootstrap/module_manifest.py` made `telegram/_core.py` import it, and the manifest imports
`..telegram` for one annotation — a cycle `test_internal_imports_stay_acyclic` caught at once.
Moving the annotations behind `TYPE_CHECKING` does not fix it: that test reads imports statically,
and it is right to. Three modules need the catalogue — the delivery adapters, the `open` tool and
the composition root — so it belongs under all three rather than beside one, next to
`foundation/references.py`, which is the same kind of declaration.

**`foundation/marks.py` was pulled forward from step 6.** Without it, `card` and `check` were the
two screen specs whose citation label could not live in its own feature: both call `title_marks`,
and `title_marks` was in `domain.py`, which Rule L forbids a feature from importing. The
alternative was to leave those two labels in `telegram/screens.py` and have the feature modules
import back out of the module the step exists to empty. `domain.py` re-exports the three moved
functions and is 190 lines to 110. Step 6 keeps the `features/cards/model.py` duplicate to
reconcile.

**The catalogue carries the citation vocabulary, so `history.py` lost it.** `CITATION_TYPES` and
the three regexes built from it are derived from `SCREENS` now; `ChatVocabulary` is built by
`vocabulary(citation_types)` and the composition root passes `SCREENS.types`. `citation_payload`
and `parse_citation_payload` are `SCREENS.payload` and `SCREENS.parse_payload`.

The one list is checked against the one thing still written by hand:
`test_the_screen_catalogue_is_the_one_list_of_openable_items` asserts the `open` tool's `Literal`
equals the catalogue's `ai_openable` half. That is what protects decision 3 — `retro` is citable and
linkable and is not an `open` target, and nothing but this test says so.

`telegram/screens.py` is 228 lines to 93: `OPENABLE_MODELS`, `_citation_label` and
`open_item_screen`'s six branches are all one lookup now.

### What step 4b landed

794 passed, 3 skipped; `ruff check .` clean. `BOT_COMMANDS`, the 15 `@router.message(Command(...))`
decorators and the `navigation` dict are gone. A `ScreenCommand` carries the handler, the command
line, the `nav:` action and whether it needs a Sprint; the shell keeps its own three — `/start`,
`/status`, `/cancel` — and the composition root puts them in front of the features'.

**The menu dict went with them.** `navigation` looked its ten handlers up in a dict of its own, over
the same set. It reads `services.commands` now, which is also what removed the deferred import of
`command_settings` that `features/profile/screens.py` needed.

**Binding left import time, so two tests were written for what the decorators used to guarantee.**
`register_commands(router, commands)` runs from the composition root, because the catalogue cannot
be reached from inside `safwa/telegram/` without closing a cycle through the manifest. One test
binds the catalogue into a fresh router and asserts every command line is there; the other asserts
every menu button names a screen some feature declared.

**`ordinary_text` refuses a slash command in its filter now**, not in its first two lines. Handlers
are tried in registration order and the first match ends the event, so binding commands after
`F.text` would have let the dialogue handler swallow every one of them. The guard it replaces was
already in the body, so nothing changed but where it is read.

**The published order follows the module order.** It was hand-written and grouped by hand —
screens, then memory, then diagnostics. It is now the shell's three and then `MODULES`, the same
order that already fixes the routing rules and the recovery hooks.

### What step 4c landed

794 passed, 3 skipped; `ruff check .` clean. The 70-entry `CALLBACK_ACTIONS` dict is nine
per-feature groups the features declare, plus one the shell keeps. `callback_token_handler` reads
`services.callback_actions`, and the composition root is what puts the two halves together — 87
actions once the generated selector families are counted, and the assembler refuses two features
answering the same one.

**The handlers stay in `callbacks.py` and the groups are declared there.** Every one of them is
module-private (`_on_card_view`), and a feature importing those names across a package boundary is
the convention this repo states outright. Declaring the groups beside the handlers keeps the names
where they belong and makes step 5 a move of a group and its functions together, rather than
seventy renames now and seventy moves later.

**One group is the shell's: the eight `item_*` actions.** They are the Value and Tag editor, one
screen over two entities, and it is the same `dict over tag, value` Rule H already reports in
`_core.py`. Step 5d splits the screen, and the shell's group goes with it.

**Two handlers are wired across features and are named here so 5b does not discover them.**
`card_checks` is the Card screen's button into its Checks, and `check_back` is the Check screen's
way back to its Card. Each belongs to the feature that draws the button; each calls the other
feature's renderer, so both become calls through an `api` when the screens move.

**The AST invariants were kept by widening, not by dropping.** `test_no_individually_registered_handler_is_unreachable`
and `test_every_inline_button_action_has_a_registered_handler` read every `*_CALLBACK_ACTIONS`
group now instead of the one dict, so an unreachable handler is still a failure.

### What step 4d landed

795 passed, 3 skipped; `ruff check .` clean. **Rule H 10 to 8, DoD #1 10 to 8.** `dialogue.py` is
447 lines to 224, and it imports no feature at all: the seven `flow == "..."` branches are eight
`TextInputFlow` declarations the features own.

**A flow is three things, because the editor already is the rest of it.** `validator` says what this
field accepts, `apply` writes the value, `render` redraws the screen the editor replaced. Validating,
keeping the editor alive on a refusal, taking the typed message out of the chat and ending the
editor's session were the same five lines in all seven branches, and they are the driver's now.

**The driver owns the transaction, which Rule B is what settled.** The first version had each
feature's `apply` open its own session and commit — six new Rule B violations, because an adapter
does not open or close a business transaction. `apply` takes the session it is given, and the shell
opens it, ends the editor's `UiSession` row in it and commits once. A draft that keeps editing adds
its replacement row inside that same transaction.

**The `item` flow became `value` and `tag`.** It was one flow branching on the entity to pick which
`update_*_fields` to call, which is the same `dict over tag, value` Rule H reports in `_core.py`. The
editor records the entity it was opened for as its flow name, so each feature answers its own and
nothing branches. That is one of the two Rule H violations this step removed; the other was
`dialogue.py` comparing a flow name to `"reminder"`.

**One test replaces what the seven-branch chain guaranteed by construction:** every literal flow name
written into a `UiSession` is a flow some feature declared.

**`features/cards/telegram.py` is 532 lines and criterion 17 says 400.** It carries the proposal
presenter, the citation label and three text-input flows, and step 5b moves `telegram/cards.py`'s 980
lines in on top. It is split there, not here — named now so 5b does not treat it as one file.

### Step 5 — the handlers move

`telegram/` dissolves into `shell/` plus the features. What goes where:

| From | To |
|---|---|
| `_core.py` — `Services`, `router`, `OwnerAndWritingMiddleware`, `CallbackContext`, `audio_payload` | `shell/` |
| `_core.py` — `RELATION_CHOICES`, `CARD_CHOICE_FIELDS`, `CHOICE_TITLES`, `ITEM_CARRIERS`, `NAMED_CHOICE_FIELDS` | `features/cards/telegram.py`; `ITEM_CARRIERS` is Rule H's `dict over tag, value` and dies as a dict |
| `_core.py` — `GenerationGuard`, `QueuedMessage`, `queue_owner_text` | gone in step 2 |
| `_messaging.py` | gone; its 189 call sites call `ChatHost` directly. `_edit_lock` and `_toasts` stop being module globals and become `ChatHost` fields, which is what the 8.a review left waiting for a host with a home |
| `_presentation.py` — `CATEGORY_EMOJIS`, `ENERGY_EMOJIS`, `kind_emoji`, `kind_label`, `card_overview_text`, `_live_card_order`, `paginate_cards` | `features/cards/telegram.py` |
| `_presentation.py` — `proposal_change_summary`, `proposal_outcome_text`, `PROPOSAL_OUTCOME_HEADINGS` | `features/proposals/render.py` |
| `_presentation.py` — `paginate`, `Page`, `with_notice` | `shell/` |
| `_presentation.py` — `menu_markup`, `menu_row`, `start_payload` | `shell/` |
| `text_input.py` | `shell/`; the flow branches go to their features as `text_inputs` contributions |
| `cards.py`, `checks.py` | `features/cards/telegram.py`, `features/checks/telegram.py` |
| `plan.py`, `sprint.py` | `features/planning/telegram.py` |
| `items.py` | `features/values/telegram.py`, `features/tags/telegram.py`, `features/saved_requests/telegram.py` |
| `reminders.py` | `features/reminders/telegram.py` |
| `proposals.py` | `features/proposals/telegram.py` |
| `screens.py` | the `screens` contribution of step 4; `report_open_failure` to `shell/` |
| `commands.py` | the `commands` contribution; `dismiss_screens_before_a_command` to `shell/` |
| `dialogue.py` | `src/safwa/turn/`; it is the turn's entry point once the flow branches leave |
| `callbacks.py` | the `callback_actions` contribution; `callback_token_handler` to `shell/` |

**`tests/test_telegram_item_ui.py` splits with them.** 134 KB is the last place in the repo where
one file knows about every entity. Leaving it whole would recreate, in the tests, exactly the
registry this phase removes from the code. Each of 5b–5e takes its share into the feature's own test
module.

### What step 5a landed

795 passed, 3 skipped; `ruff check .` clean; 189 modules; no rule count moved and cycles stayed 0.
`src/safwa/shell/` is 964 lines in six modules, none over 300: `services.py` (the container, the
router, the middleware), `chat.py` (Safwa's verbs over the host), `layout.py` (paging, menu,
notices, citation titles), `text_input.py` (the editor and its driver) and `screens.py` (opening a
cited item). `src/safwa/telegram/` is 5,977 lines to 4,704 and holds only feature screens now.

**The shell never imports `safwa.telegram`, and `telegram/_presentation.py` imports the shell.**
That is the direction 5b–5e empty the package along. `bootstrap/module_manifest.py` takes `Services`
from `..shell`, and the packet documents were relinked with the files.

**One thing in the plan was not done as written: the 189 call sites.** The step said
`_messaging.py` goes and its callers call `ChatHost` directly. Reading them, that is not
simplification: `send_registered(message, services, text, kind=MessageKind.DASHBOARD)` becomes
`services.chat.send(message, text, kind=MessageKind.DASHBOARD.value)` — longer at every site, with
`.value` spelled 189 times. And `telegram_llm` refuses to know what a kind means on purpose
(`marking.py` says so), so the layer that translates `MessageKind` into it is the border, not a
restatement of it. `_messaging.py` moved to `shell/chat.py` whole.

**What the review actually found is fixed: the host had no home.** `_host(services)` built a new
`ChatHost` on every call, which is why its edit lock and its live Toasts had to be module globals in
`telegram_llm/host.py`. The composition root builds one host, `Services` carries it, and both are
its own fields. A test that reached into `host._toasts` now reads `services.chat.toasts`.

**`proposal_outcome_text` went to `features/proposals/render.py`** rather than travelling with the
shell. It is what a resolved proposal says, which is the proposals feature's wording; the shell only
asks for it when it freezes a review the owner walked away from. That is 5e's work for one function,
done here because leaving it behind would have made the shell import the package it is emptying.

**`telegram/_core.py` is 112 lines and `telegram/_presentation.py` is 145.** What is left in both is
the Card selector vocabulary and Card presentation, which move to `features/cards/` in 5b.

### What step 5b landed

795 passed, 3 skipped; `ruff check .` clean; 194 modules; cycles 0. **DoD #3 3 to 2** —
`telegram/cards.py` is gone and `features/cards/use_cases.py` is the named one. Rule counts did not
move: the allowlist was regenerated because `ITEM_CARRIERS` changed file, not because a count did.

`src/safwa/telegram/` is 4,704 lines to 3,329, and `_core.py`, `_presentation.py`, `cards.py` and
`checks.py` do not exist. **No module this step produced is over 400**: the largest are
`features/cards/telegram.py` 374 and `features/cards/screens.py` 340.

**A feature's Telegram adapter is a package where it is big.** `features/cards/telegram.py` became
`features/cards/telegram/`, and the rest of Safwa still writes `from .telegram import ...` — a
feature whose adapter is one screen keeps it one file, and the import does not say which it is.
Inside, the names are free: `presentation.py` (190) is what a Card reads as — labels, the overview,
list order, the citation; `selectors.py` (241) is which relationships a Card offers and the two
screens that offer them; `creation.py` (249) is the draft; `lists.py` (214) is every screen showing
several Cards; `screens.py` (340) is the Card itself; `text_input.py` (107) is the three typed-value
flows; `review.py` (374) is the proposal screen. `features/checks/telegram/` is `screens.py` (322),
`resolution.py` (114, the Done-gate) and `review.py` (154).

**That costs Rule B its sight of these two features, and the loss is named rather than hidden.**
Rule B reads file *names* — `process_modules("telegram.py", "agent.py")` — so `telegram/review.py`
is not scanned. Widening it to a feature's `telegram/` package would report **15 commits**: every
screen opens a session, mints its single-use `CallbackToken` rows through `token_button`, and
commits before it sends. Those are real by the rule's wording and by design in the code, and they
passed until now only because the screens lived outside `features/`. Paying them down means a screen
driver that owns the transaction, the way `handle_text_input` came to own the editor's in 4d — which
is 5e-sized work over roughly forty call sites. **The owner chose to leave Rule B name-based**: the
rule describes a state the code has not reached, and putting a debt one named step will clear into
the ratchet moves the counter instead of the code. `docs/FEATURE_MODULES.md` records the gap where
the adapter package is described.

**The choice screen went to the shell, not to Cards.** `choice_screen` and `choice_rows` were in
`telegram/cards.py`, and `telegram/checks.py` imported them from there. Neither function knows what
a Card is — a Card field, a Card relationship and a Check's Values are all chosen through them — so
moving them with Cards would have made `features/checks` import `features/cards` for a screen shape.
They are `shell/selector.py` (63), and `MessageKind.CARD_EDITOR` is the name step 6 corrects.

**Three functions were added to `cards/api.py` and one to `checks/api.py`.** The Card screen counts
the Checks that hang on it (`checks.api.card_checks`); the Check screens name the Card they hang on
(`cards.api.card_title`, `live_card_title` for the Done-gate, which refuses an archived Card, and
`card_labels`, which is a title with the marks it carries). That is the whole of the two features'
new coupling, and Rule E stayed 0.

**`ITEM_CARRIERS` did not die in 5b.** The plan had it move to `features/cards/telegram.py`, where
Rule H would stop counting it. It is not Cards': it says what carries a Value or a Tag, and the
answer includes Checks. Killing it needs each of those features to declare its own carriers, which
is 5d. It moved to `telegram/items.py` instead — the one screen that serves both entities, beside
the two functions that read it — and 5d moves the screen and the dict together. Rule H stays 8.

**`command_add` was deleted rather than moved.** It was `await start_manual_card_creation(...)` and
nothing else, so the menu button names that function directly. `command_backlog` moved to
`features/cards/lists.py` with the dashboard it opens.

**The test monolith lost its Card and Check share.** `tests/test_telegram_item_ui.py` is 3,579 lines
to 2,662; `tests/test_cards_ui.py` (729) and `tests/test_checks_ui.py` (78) are the eighteen Card
screen tests and the two Check ones, and `tests/ui_harness.py` (203) is what every screen test needs
— the fakes, the container, and where the UI lives. The Card *proposal* tests stayed: they exercise
the review screen, which is 5e's move.

**The AST invariants stopped naming a package.** `_telegram_module_trees` walked
`safwa.telegram` and had `features/profile/screens.py` added to it by hand. It now asks which
modules under `features/`, `shell/` and `telegram/` reach for aiogram, so a new screen is scanned
because it draws one, not because someone remembered to list it.

**CLAUDE.md's Telegram layering paragraph was corrected.** It described `_core.py` ←
`_presentation.py` ← `_messaging.py` ← `text_input.py`, none of which is in that package any more —
5a made it false and 5b removed the last two names in it.

### What step 5c landed

795 passed, 3 skipped; `ruff check .` clean; 197 modules; cycles 0; no rule count moved.
`src/safwa/telegram/` is 3,329 lines to 2,621. `features/planning/telegram/` is `sprint.py` (317 —
Today, the running Sprint, Planning, the retro and the Success-criteria flow), `plan.py` (376) and
`state.py` (87), and the old `features/planning/telegram.py` folded into `sprint.py`: one flow and a
citation label belong beside the screen they redraw, and only Cards had enough of them to earn a
`text_input.py`.

**Rule E was amended, by the owner's decision, to name two doors.** Moving these screens made three
edges into Cards that `cards/api.py` cannot carry: `render_card`, `card_list_rows` and
`card_list_text` are screens, and `move_card` is a write. `cards/api.py` importing
`cards/use_cases.py` closes a cycle through `planning/api.py` — a stage write syncs a Sprint
commitment, which is the design and is what its docstring already says. The direct edge
`planning.telegram -> cards.telegram` is acyclic; only the rule forbade it.

- **`features.<other>.telegram` is a door.** A screen is public already: `FeatureModule.screens`
  hands `render_card` to the composition root.
- **`features.<other>.use_cases` is a door for an adapter only** — a module named `telegram.py` or
  inside a feature's `telegram/` package. That is what carries `move_card` to the plan screen.

This was not 5c's problem alone. `features/values/telegram.py`, `features/tags/telegram.py` and
`features/reminders/telegram.py` all reach a screen in `telegram/` today, legally only because that
screen is outside `features/`. Every one of those becomes a Rule E edge in 5d and 5e, and the
amendment is what lets them move at all. **Rule E stayed 0.**

**`plan.py` shed its state.** `state.py` is what the plan screen remembers between taps — the page,
the picked Requests, the screen's own message id and the Requests' resolution — and `plan.py` is the
screen and its handlers. The cut is one-way and it is what keeps the screen under 400.

### What step 5d landed

795 passed, 3 skipped; `ruff check .` clean; 204 modules; cycles 0. **Rule H 8 to 3 and DoD #1 8 to
3.** `src/safwa/telegram/` is 2,621 lines to 1,757 in five modules — `callbacks.py` (1,063),
`commands.py` (243), `dialogue.py` (226), `proposals.py` (200) and `__init__.py` — and
`callbacks.py` fell 1,325 to 1,063.

**`ITEM_CARRIERS` died, and the shared item editor with it.** One screen served Tag and Value
through an `entity` string, a dict of `ReferenceSpec`s and eight `item_*` actions that branched on
which entity they had. Values and Tags now draw their own, in `features/values/telegram/screens.py`
(398) and `features/tags/telegram/screens.py` (346), with `value_*` and `tag_*` actions of their own.
The counts came for free: **`CardValue`, `CheckValue` and `CardTag` are owned by Values and Tags**,
so each feature counts what carries it without asking anyone — `value_link_counts` already existed,
and `tag_link_count` came out of `delete_tag`, which was computing it inline.

**`SHELL_CALLBACK_ACTIONS` is gone.** Those eight actions were its only entries, so the composition
root now hands `FEATURE_CALLBACK_ACTIONS` straight through: every inline button in Safwa belongs to
a feature.

**Five features took their handlers with their screens**, rather than leaving them for 5e. The
handlers had to be rewritten anyway — split by entity, or repointed at a moved screen — and writing
them in `callbacks.py` first would have meant writing them twice. Values, Tags, Requests, Reminders
and Profile each declare their own `*_CALLBACK_ACTIONS` beside the screens that draw the buttons.

**`features/profile/screens.py` became `features/profile/telegram/screens.py`.** It was the one
adapter with a name outside the vocabulary, from before the vocabulary existed.

**`command_reminders` was deleted rather than moved**, like `command_add` in 5b: it was
`await render_reminders(message, services)` and nothing else, so the command names that function.

**The test monolith gave up 5c's share as well as 5d's.** 5c moved the screens and left their tests
behind; both are paid here. `tests/test_telegram_item_ui.py` is 2,635 lines to 1,718, and
`test_planning_ui.py` (427), `test_requests_ui.py` (136), `test_reminders_ui.py` (131),
`test_values_ui.py` (99), `test_settings_ui.py` (94) and `test_tags_ui.py` (88) are the screens'
own. `seed_plan` and `plan_filters` went to `tests/ui_harness.py` and lost their underscores: two
test modules build a plan, so the helper is no longer module-local.

### Step 6 — the last flat modules

**`domain.py`, 190 lines, 7 functions:**

| Function | To |
|---|---|
| `bootstrap_workspace` | `foundation/workspace.py` |
| `finish_sprint`, `expire_due_sprint` | `features/planning/use_cases.py` — this is Rule L's violation, since `planning/background.py` imports `domain.expire_due_sprint` today |
| `archive_settled_items` | `features/cards/archive.py`, calling Checks through `checks/api.py` |
| `title_marks`, `is_closed_repeat`, `live_repeat_instance_id` | see below |

The last three take `Card | Check` and belong to neither feature alone. They are the shared
vocabulary of "a repeat that closed" and "something out of sight", and the honest home is
`foundation/marks.py` — foundation is where something both features need lives without either
importing the other. `is_closed_repeat` already exists a second time in `features/cards/model.py`;
the two are reconciled here.

Named and **not** fixed: `title_marks` writes the two marks in Python, and `ai_cards` and `ai_checks`
render the same wording in SQL. Python and SQL cannot share one string, so this stays a duplication
by necessity. It is written down so it does not drift silently.

**`history.py`, 392 lines, two different things:**

| Half | To |
|---|---|
| `TelegramNotes`, `register_message`, `TelegramHistorySource`, `MARKS`, `mark_message`, `mark_kind`, `read_kind_mark`, `VOCABULARY`, `auth_main` | `adapters/telegram_history.py`, beside `adapters/asr.py` |
| `CITATION_TYPES`, `CITATION_PATTERN`, `CITATION_MARKUP`, `citation_payload`, `parse_citation_payload`, `_CITATION_PAYLOAD_RE` | assembled from the `screens` contribution of step 4 |
| `conversation_block`, `CONVERSATION_TAGS`, `_CONVERSATION_LINE` | `ai/` — it is how a subagent reads the conversation as data, which is Safwa's agent semantics |

Twelve importers, four of them tests. `tests/test_docs.py` imports `CITATION_TYPES` to know which
`(diary:12)` links in the docs are citations rather than dead paths; its import moves with the rest.

**`main.py`, 250 lines → `bootstrap/main.py`.** `LIFECYCLE_MODULES` already names that path, so Rule
G stops needing an entry for a file that does not exist yet.

**Rule G,** as decided above: `adapters/asr.py` and `telegram_llm/host.py` both take their task start
from `bootstrap/main.py` rather than calling `asyncio.create_task` themselves.

**`MessageKind.CARD_EDITOR` → `EDITOR`.** Decided by the owner 2026-08-31, on a corrected reason.

The reason first given for this was wrong, and is recorded so it is not re-derived: the rename does
**not** let `MessageKind` move into `telegram_llm`. The package does not want the enum.
`marking.py` says it outright — "what the kinds are, and what each of them means, is the host's to
say — this only carries them" — and takes them as `KindMarks(codes: Mapping[str, int])`, with
`ChatVocabulary` saying which of them are conversation. Kinds are host configuration by design, so
`MessageKind` stays in Safwa and the border 8.a drew is right as it stands.

The rename is worth doing on its own: `CARD_EDITOR` marks **every** text-input screen — a Reminder's
text, a Sprint's criteria, a Setting, a Value, a Tag — because `telegram/text_input.py` sends them
all under it. The name says Card and means editor. Its stored value could not be changed under live
rows, and there are no migrations, but the owner recreates the pre-release database, so it is
available now and impossible after the first release.

`uv run safwa-backup` first. `schema.json` does not move: this is a stored value, not a column.

### Step 7 — the documents

Regenerate `tests/architecture_allowlist.json`. Correct `CLAUDE.md` and `README.md` by deleting: the
`telegram` package layering paragraph, the `domain.py` and `history.py` pointers, the `enums.py`
split sentence once `CardKind` and `WorkspaceMode` are placed, and every "until its declared phase".
Fold this file into `MIGRATION.md` as "What Phase 8.b delivered".

## What steps 1 to 3 landed

792 passed, 3 skipped; `ruff check .` clean; 183 modules; Rule C 0, F 0, G 2, H 24, L 1; DoD #1 24,
DoD #3 3. `prompt_prefix.json` and `schema.json` unmoved.

**The queue is gone.** `QueuedMessage`, `GenerationGuard.begin_queue` / `finish_queue` /
`abort_queue` / `drain_queue`, `queue_messages`, `queue_owner_text`, `materialize_queued_dialogue`,
`QUEUE_PREVIEW_CHARS`, the drain loop in `run_dialogue_turn`, and `voice_message`'s "the lease is
settled only now" branch. `dialogue.py` 447 → 371, and it is out of the ten largest modules.

**`src/safwa/turn/` is `TurnState = Idle | Answering | BackgroundWork`, and `TurnManager` writes
it.** Three variants, not §9.3's five. `Suspended` is not built because the turn is already given
back while a screen waits, and `Cancelling` is not built because `cancel()` goes straight to `Idle`
— neither has a reader, and Phase 7.c's precedent is to keep the states that do. `Services.guard`
is `Services.turn`. `BACKGROUND_SOURCE_ID` is gone: background is a variant now, not a negative id.

**`end_background()` is the one method the union added.** `release(BACKGROUND_SOURCE_ID)` refused
to free an owner's lease, and a plain `end()` would not have; background work that finishes after
the owner arrives must not take the turn from them.

**Rule C was made able to see this.** It read only `features/*/{manager,model}.py`, so a process
beside the features was outside it, and it matched variants by a `State`/`Action`/`Effect` suffix,
which `Idle` and `Answering` do not carry. It now reads `safwa/turn/` and `safwa/cues/` too, and it
takes the variants from the union alias — `type TurnState = A | B` — rather than from each name.
Verified by unfreezing `Answering` and watching it fail. Without this, criterion 9 was checked by
nothing.

**One thing the plan asked for and did not get: the notice on the approval continuation.**
`continue_agent_approval` already writes "… Safwa is continuing…" into the chat, so a second
message saying an answer is being written is the same sentence twice. `AG-TURN-022` describes the
dialogue turn, and that is the only place `open_turn_notice` is called.

## What 8.b deletes

Counted, so the batch can be judged on it.

| Deleted | ~lines |
|---|---|
| `foundation/state_flow.py` and `tests/test_state_flow.py` | 230 + tests |
| the queue: `QueuedMessage`, four `GenerationGuard` methods, `queue_owner_text`, `materialize_queued_dialogue`, the drain loop, `QUEUE_PREVIEW_CHARS`, `voice_message`'s lease branch | 200 |
| `telegram/_messaging.py`, adapters the 8.a review already found restating `ChatHost` | 282 |
| `telegram/__init__.py`, re-exports only | 72 |
| the 7 `flow == "..."` branches in `dialogue.py`, and with them its imports of six features | 180 |
| three of four copies of the entity list | — |
| `domain.py` and `history.py` as modules | 582 |

## The duplication 8.b closes

The same list of entity names is written **four times**:

```
ai/tools.py:56           OPENABLE_MODELS        the comment in it admits this
telegram/screens.py:43   OPENABLE_MODELS        plus _citation_label and open_item_screen
history.py:97            CITATION_TYPES         feeds three regexes and the deep-link payload
ai/contracts.py:500      OpenInput.item_type    the Literal
```

Step 4's `screens` contribution folds the first three into one. The fourth stays by decision — a
tool's enum is prompt text.

Second: `is_closed_repeat` exists in both `domain.py` and `features/cards/model.py`. Step 6
reconciles them in `foundation/marks.py`.

Third, and not closable: `title_marks` in Python against the same wording in `ai_cards` and
`ai_checks` SQL. Written down rather than fixed.

## Exit criteria

The eight from `MIGRATION.md` as written, plus what this file changed. A miss is attributable.

| # | Criterion | Change |
|---|---|---|
| 8 | `src/safwa/telegram/` does not exist, and neither does `GenerationGuard` | as written |
| 9 | no combination of turn state is legal that the type does not allow, and `QueuedMessage` does not exist | as written |
| 10 | `foundation/state_flow.py` has a production reader, or it is deleted | as written |
| 11 | **Rule H 24 → 1**, and the one is the `open` tool's enum in `ai/contracts.py`, because a tool's enum is prompt text; Rule G 2 → 0; Rule L 1 → 0; DoD #1 24 → 1, and the one is `bootstrap/modules.py` | amended, decision 3 |
| 12 | **DoD #3 3 → 1**, and the one is `features/cards/use_cases.py`, because every writer of an Action's stage is in it on purpose; neither `domain.py` nor `history.py` exists | amended, decision 1 |
| 13 | `AG-TURN-010` amended, `AG-TURN-022` and `AG-TURN-023` green; the queue tests deleted by name | as written |
| 14 | `prompt_prefix.json` byte-identical; `schema.json` unchanged | as written; decision 3 is what protects it |
| 15 | `tests/e2e/test_advisor_flow_e2e.py` and `tests/e2e/test_subagent_e2e.py` added to, never traded | as written |
| 16 | the live Telegram suite passes: `uv run pytest tests\e2e\live --live-telegram -q` | as written; unverified in 8.a, so it is verified here |
| 17 | no module produced by this batch is over 400 lines | new, decision 6 |
| 18 | `tests/test_telegram_item_ui.py` does not exist; its tests live in the features | new |
| 19 | `CARD_EDITOR` does not exist, and `MessageKind` is still Safwa's | new, decision 6 |

## Risks

**Step 5 is the batch.** 4500 lines, seventy callback actions, 189 messaging call sites. Split into
five so a failure rolls back one group rather than leaving half a registry, which is two registries.
If 5b cannot be finished, the whole batch rolls back rather than shipping a `FeatureModule` that
half the features use.

**The import cycle 8.a was ordered to prevent.** A feature's screen needs the shared UI core and the
shared handlers need the screen back. `telegram_llm` cannot import Safwa, so the cycle cannot be
written through it — but `shell/` can import features and features will import `shell/`. Rule B has
to be read carefully here: a feature's `telegram.py` may import `shell/`, and `shell/` may not import
a feature except through a contribution it was handed. If that direction cannot be kept, the
contribution is missing, not the rule wrong.

**The snapshot.** Criterion 14 is protected by decision 3 alone. Anything else assembled from
`MODULES` that ends up in a prompt or a tool schema moves `prompt_prefix.json`, and `MODULES` order
already feeds `_routing_rules()` and `{views}`. Check the snapshot after step 4, not after step 7.

**The database, once.** Only the `CARD_EDITOR` rename touches stored data, and it is the last thing
step 6 does. `uv run safwa-backup` before it.

## What 8.b does not do

Not in this batch, named so they are not drifted into:

- the retrospective — it is a feature designed from scenarios after the migration;
- `Services` as a container — it is a god object and it stays one until there is a reason;
- `ai/contracts.py` beyond decision 3;
- uv workspace packages and wheels — that is Phase 9, and §15 says the namespace has to stop moving
  first;
- `features/cards/proposal.py` at 390 lines and `features/proposals/api.py` at 598 — both under the
  threshold, both left alone;
- splitting `features/cards/use_cases.py` — decision 1 keeps it whole and names why.
