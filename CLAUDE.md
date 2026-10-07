# Repository guidance

Shared guidance for coding agents. Read the documents the task needs; each contract has one
home. `tests/brd/` holds approved behavior, and [tests/brd/README.md](tests/brd/README.md) explains
scenario wording and identifiers.

## Start with the task

| Task | First reading | Verification |
|---|---|---|
| Change domain behavior | the feature's `.feature`, `use_cases.py`, `model.py` | scenario tests and affected adapters |
| Fix a screen | the feature's `.feature`, Telegram adapter, [screens.feature](tests/brd/tg_agent_shell/screens.feature) | UI tests; E2E when the flow crosses a turn |
| Change execution or routing | [shell scenarios](tests/brd/tg_agent_shell), `agent_runtime/`, [AGENT_ARCH.md](docs/AGENT_ARCH.md) | budgets, cancellation, suspend/resume and claims |
| Add a feature | [FEATURE_MODULES.md](docs/FEATURE_MODULES.md); [Tags](src/safwa/features/tags) for manual and AI editing, [Diary](src/safwa/features/diary) for proposal-only writes | registration, scenario tests, one whole path |
| Remove a feature | its scenarios and feature map; callers, reader views, tools, menu and foreign keys | no dangling registrations, imports, schema or test references |
| Build another bot | application contract in [AGENT_ARCH.md](docs/AGENT_ARCH.md), [wallet example](examples/wallet/app.py) | `tests/shell/`, including the example with Safwa unimportable |
| Change hooks | [HOOK_ARCH.md](docs/HOOK_ARCH.md) and the owning feature's scenarios | registration, effect and affected event path |

## Package boundaries

- `llm_gateway`: provider boundary. `agent_runtime`: sessions and execution, independent of Telegram.
- `telegram_llm`: chat, screens and history. `tg_agent_shell`: storage, review, hooks and Telegram integration.
- `safwa`: this bot's domain, persona and composition. Shared packages import no application.
- Safwa's [MODULES](src/safwa/bootstrap/modules.py) declares features; `HOOKS` declares reactions.
  Registration is derived. Domain features can depend on each other through the documented doors;
  removing registration alone does not remove those dependencies.
- Layer and transaction rules, including why `api.py` defines lower-layer operations rather than
  re-exporting `use_cases.py`, belong to [FEATURE_MODULES.md](docs/FEATURE_MODULES.md).

## Invariants

- Send bot messages through `ChatHost` and give them the correct `MessageKind`; kept chat is dialogue.
- The model proposes changes; mutation tools belong to subagents, never the root Advisor.
  Save and manual UI call the same domain operations. Use cases take the caller's session and
  never commit; the caller owns the transaction.
- Each agent session owns its budget and transcript. New sessions get new counters; resume keeps
  the same counter. A screen suspends the chain. Cancellation ends all its unfinished sessions.
- The root model supplies the dialogue answer. Adapters and hooks may publish system messages and
  screens through the host.
- A reader reaches only its declared views. Publishing a view does not grant access to it.
- Keep the system prompt prefix byte-stable; volatile context follows the dialogue.
- `TurnManager` owns foreground/background access. Background work checks currentness before
  publication or commit; the owner's action takes precedence.

## Designing and coding

**Simple is the test of correct.** A right solution is simple. When it is not simple, something is
wrong — go back and find it instead of building around it. A hard problem's right solution is a
composition of simple modular ones, never one complex whole: a monolithic complex solution is a
wrong solution. Several mechanisms that all compensate for one missing property are the signal.

- **The fewest mechanisms that solve the problem.** No abstraction for one call site, no
  configurability nobody asked for, no error handling for an impossible case.
- **Every changed line traces to the request**, or to a declared batch's stated scope. That batch
  deletes the mechanism it replaces, the workarounds that compensated for it, and the vocabulary
  only it read; dead code outside that scope is named, not deleted.
- **State the success criterion first, and make it checkable**: a test that reproduces the bug, a
  scenario that fails without the change, a scanner count that moves.
- Say what you assumed. Ask when the answer changes what you build; otherwise assume and keep going.

## Implement the requested change

Match existing style. All Python modules use `from __future__ import annotations`.
A bug fix gets a reproducing test; other changes get checks proportionate to their effect.
Choose implementation and test structure independently, preserving approved coverage:
a batch that removes a test names what still covers its scenario.
Ask only when new user-visible behavior has materially different reasonable interpretations.
Explicit user instructions authorize that change; restoring an approved contract needs no
repeated approval. Approved scenarios outrank code, tests and documents, subject to the user's
current instruction. Reword scenarios without changing their identifiers, covered cases or
outcomes; changes to those cases or outcomes require the owner's instruction.

Code comments stay sparse and explain a non-obvious why, such as an ordering constraint.
Developer prose may explain a decision's rationale.
Replace false documentation in place; do not leave an old statement beside its correction.
New documentation goes in `docs/`; diagrams stay beside the prose they explain.

## Verify

Windows / PowerShell; prefix terminal commands with `rtk`. Setup is in [README.md](README.md).
Run affected tests first. A broad change runs the complete local suite and Ruff; E2E and shell
tests are already included in that suite.

```powershell
rtk proxy uv run pytest -q
rtk proxy uv run ruff check .
rtk proxy uv run python scripts/architecture_metrics.py
rtk proxy uv run python scripts/architecture_metrics.py cards
rtk proxy uv run pytest --brd=DI-DAY-001 -q
```

The feature map shows sources, callers, views, scenarios and tests. The scanner checks structural
rules; documentation tests check links and spelled names, not semantic agreement with the code.
Live Telegram/provider checks are opt-in and use QA configuration, never production state;
[resolve_qa_config](src/safwa/qa.py) rejects reuse of the production bot token.
E2E tests use real storage and services, replacing remote boundaries.
Never edit `telegram-bot-exampler/`, the local reference.

Prompt, tool-description and reader-view-list changes update the prompt snapshot in the same
batch. Schema changes update the schema snapshot and name the affected tables.
Update only the affected snapshot, by naming its test; no extra approval is needed:

```powershell
rtk proxy uv run pytest tests/test_architecture.py::test_rule_i_prompt_prefix_is_byte_stable --snapshot-update
rtk proxy uv run pytest tests/test_architecture.py::test_rule_j_schema_is_unchanged_outside_a_schema_batch --snapshot-update
```

## Safwa-specific rules

Safwa's domain is [DOMAIN.md](docs/DOMAIN.md). Only the retro hook writes its memory observations;
the current contract is [SPRINT_ANALYSE_TO_RETRO_AND_MEM.md](docs/SPRINT_ANALYSE_TO_RETRO_AND_MEM.md).
Update the [onboarding manual](src/safwa/features/onboarding/agent.py) in the same batch when
changing or adding a screen, command, rule or reaction the owner should meet.
These product rules do not apply to another bot using the shell.

Cross-feature constants live in [constants.py](src/safwa/constants.py), which imports nothing
from Safwa. A constant used by one module stays at its top; [config.py](src/safwa/config.py)
gets defaults from that owner. [enums.py](src/safwa/enums.py) follows the same ownership rule.

Safwa is in alpha: change the ORM schema directly, update its snapshot, and do not add migrations
or compatibility layers before the first release. `upgrade_database` adds missing tables and
indexes but never alters existing columns; the owner recreates the pre-release database.
Until the owner ends alpha, preserving its data is not a design constraint; do not add rebuild
or backup steps and warnings to ordinary schema work.

System prompts, tool/field descriptions, `hint` and `next` target small local models: short,
imperative, concrete instructions, one per line, with exact names, no rationale or repeated rules.
This does not restrict developer explanations or substantive answers.
User-facing strings are complete sentences with Safwa's capitalized domain nouns.
Bot messages are HTML; escape user/model text. Enums store plain `.value` strings.
Commit subjects follow `vX.Y <short summary>`.
Other contracts: [LLM_HISTORY.md](docs/LLM_HISTORY.md), [LLM_GATEWAY.md](docs/LLM_GATEWAY.md),
[HOME_DASHBOARD.md](docs/HOME_DASHBOARD.md), [SECURITY.md](docs/SECURITY.md), [SEARCH.md](docs/SEARCH.md).
