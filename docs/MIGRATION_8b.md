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
| 4 | `FeatureModule` grows commands, callback actions, text-input flows and the screen/citation catalogue | DoD #1 falls | |
| 5a | The shell: `src/safwa/shell/`; `_messaging.py` dissolves into direct `ChatHost` calls | 189 call sites | |
| 5b | Cards and Checks | | |
| 5c | Planning and the Sprint | | |
| 5d | Values, Tags, Requests, Reminders, Profile | | |
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
