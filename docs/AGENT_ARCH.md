# Agent architecture

How the AI side of Safwa is put together: what a session is, how a turn runs, and what each
subsystem around it owns. Sections describe the code as it stands except the explicitly planned
Proposal lifecycle below, agreed 2026-09-13 for the follow-up after Wave 1.

The rules behind these mechanisms live in [CLAUDE.md](../CLAUDE.md); this file is the shape.

## The five packages

`src/` holds five. Four of them import no Safwa at all, so they can be taken to another project:

```mermaid
flowchart TD
    LG["llm_gateway<br/><i>one request to a model</i>"]
    AR["agent_runtime<br/><i>the session, the loop, the suspension</i>"]
    TL["telegram_llm<br/><i>the chat, the screen, the button</i>"]
    AI["tg_agent_shell/ai<br/><i>the agent engine</i>"]
    SH["tg_agent_shell/telegram<br/><i>the Telegram application</i>"]
    FE["safwa/features/*<br/><i>what the owner keeps</i>"]
    BS["safwa/bootstrap<br/><i>the composition root</i>"]

    AR --> LG
    AI --> AR
    AI --> LG
    SH --> TL
    FE --> AI
    FE --> SH
    FE --> TL
    BS --> FE
    BS --> AI
    BS --> SH
```

**An arrow down is "imports".** There is not one pointing back, and that is read:

| Rule | What it forbids |
|---|---|
| F | a module of the four upper packages importing `safwa` |
| M | a module of `tg_agent_shell/ai/` importing what is built on it |
| E | a feature module opening a door above its own layer |
| H | a module outside a feature fanning out over entity names |

- **`llm_gateway`** — reaching a model has no state, so the contract is tiny: one completion call,
  neutral messages, an unparsed `arguments_json`, and an adapter holding the SDK, the URL and the key.
- **`agent_runtime`** — the session that can stop on a person and be continued. It knows neither
  what a tool does nor what a proposal is: a suspension hands out an opaque `InteractionRef`, and
  the resume comes back carrying it.
- **`telegram_llm`** — the chat between the bot and the person: every outgoing message sent
  registered and marked, a button used once, a screen replaced or taken down. What each kind means
  the host says once, in a `ChatVocabulary`.
- **`tg_agent_shell`** — `ai/` the engine, `proposals/` the review flow, `telegram/` the
  application, `turn/` the single foreground lease, `cues/`, `hooks/` and `foundation/`; at its root
  `registry.py` derives an application's wiring, `recovery.py` reconciles a restart, and
  `session.py`, `history.py` and `asr.py` are the root session, the chat window and the voice.
- **`safwa/features/*`** — `MODULES` lists seventeen: sixteen Safwa features, each with its rules in
  `tests/brd/`, and the shell's own `proposals`. `advisor` is the seventeenth feature package and is
  in no registry — it is the root session's prompt and the views it is told it may read, wired
  directly by the composition root.

## Assembly

```mermaid
flowchart LR
    S["Settings"] --> DB["Database · create_all"]
    DB --> BOOT["bootstrap_workspace<br/>recover_startup<br/>create_ai_views"]
    BOOT --> INFRA["provider · memory<br/>query_runner · history"]
    INFRA --> ADV["the root session<br/>+ routed subagents"]
    ADV --> SV["Services"]
    SV --> DP["Dispatcher"]
    DP --> POLL["long polling"]
    SV --> BG["background tasks<br/>cancelled in finally"]
```

All of it is [bootstrap/main.py](../src/safwa/bootstrap/main.py). Which features exist,
[bootstrap/modules.py](../src/safwa/bootstrap/modules.py) knows — and nobody else. Everything
that *follows* from that list is [registry.py](../src/tg_agent_shell/registry.py), which is the
shell's: `Registry.of(MODULES, world=…, hooks=HOOKS)` derives the view catalogue and its allowlist, the screens,
the commands, the callbacks, the text inputs, the proposal capabilities, the subagent roster, the
hooks and the background tasks, refusing each collision where it happens.

`HOOKS` is the explicit list of automatic reactions, independent of the features themselves.
`HookRegistry` selects enabled subscriptions before running their conditions. The tool adapter
emits `AfterTool`; the dialogue adapter emits `AfterTurn` after releasing the owner's turn.
Its `Run` operations use one background lease and a publication port that checks currentness.
Summary retains its own window threshold and history comparison. Its manual command calls the
same writer directly. The current scope and later stages are [HOOK_ARCH.md](HOOK_ARCH.md).

## What an application gives the shell

The shell runs an application it knows nothing about. This is the whole of what it asks for, and
[examples/wallet/app.py](../examples/wallet/app.py) is the second answer to it — a ledger of
wallets and entries, with none of Safwa's nouns in it.

| What the shell asks for | The type that carries it | Safwa's answer |
|---|---|---|
| which features exist | `tuple[FeatureModule, ...]` handed to `Registry.of` | `MODULES` |
| what a proposal is made against | `WorldReader` → `World(revision, timezone)` | the `workspace` row |
| the database | its own `Base`, plus `upgrade_database(url, Base.metadata)` | `safwa/foundation/models.py` |
| durable notes | `Memory` — one `sync()` returning something with `.text` | `data/memory.md` |
| the state a session reads first | `(session) -> StateBlocks(state, clock)` | `workspace_context` |
| the chat, and where its window ends | `TelegramHistorySource` with `MARKS` and a `WindowEdge` | `SummaryEdge` |
| what the model is told it is | one system prompt, with `Registry.routes` filling the routes in | `SYSTEM_PROMPT` |
| the container every handler reads | `Services`, filled from the registry | `bootstrap/main.py` |
| the home screen | exactly one `ScreenCommand` with `nav=HOME_NAV` | the `home` feature |
| a restart | `recover_startup(session, registry.recovery)` | called before polling starts |
| a shutdown | cancel the background tasks, close the history, the provider and the bot | the polling `finally` |

Everything else is the application's own: the persona, the provider, the product dependencies,
and the startup itself — Safwa's carries ASR, Telethon and OpenRouter headers, and the example's
carries none of them, which is why the shell holds no `run()` of its own.

**A second application declares its tables on a `Base` of its own**, and `upgrade_database` takes
that metadata beside the shell's. Two applications in one process then create their own tables and
never each other's; `tests/shell/` runs the example with `safwa` unimportable and counts what a
fresh database gets.

## The map

```mermaid
flowchart TB
    OWNER([Owner in Telegram])
    subgraph TG[shell · turn · feature adapters]
        HANDLERS[commands / callbacks / dialogue]
        GUARD[TurnManager]
        SCREENS[screens · proposals · renderers]
    end
    subgraph RT[agent_runtime]
        MGR[AgentManager · the loop, the routed chain, suspend and resume]
    end
    subgraph AI[tg_agent_shell: ai · session.py · proposals]
        ADV[the root session]
        SUB[Routed subagent session]
        MINI[Mini session]
        PREP[ChangePreparer]
        AUTO[AutoApprovalReviewer]
        SQL[ReadOnlyQueryRunner]
    end
    subgraph BG[background loops]
        CUE[cue-queue]
        REM[reminder-scheduler]
        SPR[sprint-expiry]
        MEMP[memory-file-poll]
        MEMM[memory-maintenance]
    end
    HIST[(telegram_history.py · Telethon)]
    MEM[(data/memory.md)]
    DB[(SQLite · ai_* views)]

    OWNER --> HANDLERS --> GUARD --> MGR
    MGR --> ADV
    SCREENS -.->|Save resumes the session| MGR
    ADV -->|route| SUB
    ADV -->|call_helper| MINI
    SUB --> PREP --> SCREENS --> OWNER
    PREP --> AUTO
    ADV --> SQL --> DB
    MINI --> SQL
    HIST --> ADV
    MEM --> ADV
    CUE --> ADV
    REM --> CUE
    SPR --> DB
    MEMP --> MEM
```

Only the Advisor writes to the chat. Everything else either hands it words or opens a screen.

## A session is the unit

Every agent run — the Advisor, a routed subagent — is an `AgentSession` backed by one `agent_runs`
row. The row is what survives a suspension:

| field | what it carries |
|---|---|
| `kind` | `advisor`, or the subagent's name |
| `parent_run_id` | who routed here; null for the Advisor |
| `state_json` | dialogue, transcript, tool count, repair rounds, receipts, `host_state`, `helper_offered`, `interaction_token` |
| `claimed_at` | the atomic claim that stops two resumes of one session |
| `status` | `running`, `awaiting_approval`, `interrupted`, `completed`, `failed`, `abandoned` |

```mermaid
stateDiagram-v2
    [*] --> running
    running --> awaiting_approval: a screen is open
    awaiting_approval --> running: Save / Discard
    running --> completed: answered in words
    awaiting_approval --> interrupted: the owner wrote instead of deciding
    awaiting_approval --> abandoned: nobody answered in PROPOSAL_REVIEW_MINUTES
    interrupted --> running: route back on the same turn
    interrupted --> abandoned: the turn ended without coming back
    running --> failed
    completed --> [*]
```

`AgentSession.restore` rebuilds a suspended run from its own row. The context prefix is **not**
restored — it is rebuilt from live state, so the workspace and the clock are current while the session's
own steps come only from its record.

Four things happen to a session that stops on a person, and `AgentManager` owns all four:

| | |
|---|---|
| suspend | checkpoint the session and hand back an `InteractionRef` — its run id and a minted token |
| `resume(ref, value)` | claim by that reference, answer the calls it stopped on, run it on, hand its receipt up the chain |
| `interrupt(ref, results, summary)` | leave it unfinished with those results and the note that the owner wrote instead of deciding |
| `close(ref, results)` | answer the calls it stopped on and end it with every caller above it: nobody answered, and nothing comes back to it |

The reference is opaque: the runtime never learns what the person was shown, and Safwa keeps the
mapping — the approval batch carries the token, so a Save resolves the screen and names the session
in one step. The token is minted at the checkpoint and cleared when it is taken, which is what makes
one answer to one screen resume exactly one turn.

A session runs until it answers in words. A turn that stops with nothing is told so and asked again,
bounded by the repair rounds. A session with no parent is the one that guarantees the owner sees
something: the receipts, or one `⚠️` line.

## One Advisor turn

```mermaid
sequenceDiagram
    participant O as Owner
    participant G as TurnManager
    participant A as Advisor
    participant P as Provider
    O->>G: message
    G->>A: begin(message_id)
    A->>A: ContextBuilder.messages_for()
    loop until a turn calls no tool
        A->>P: messages + tools
        P-->>A: tool calls or prose
        alt immediate tool
            A->>A: run it, feed the result back
        else mutation tool
            A->>A: not held by the Advisor at all
        end
    end
    A-->>O: one message, citations rendered
```

`IMMEDIATE_TOOLS` are `query_data`, `route`, `open` and `call_helper` — the tools `ToolAdapters`
answers itself, and they run inside the turn. `query_data` is published to every session it runs,
the Advisor's and a subagent's alike, so a feature declares only its own readers.
The Advisor holds no mutation tool: every write is a proposal authored by a subagent.

An immediate tool and mutation tools must not arrive in one provider response; the runtime rejects
the mutations and the model retries them once it has seen the read.

A name that is not on the session's own tool list is refused before any dispatch, so the tools sent
to the model are exactly the tools it can reach: the Advisor cannot prepare a change and a subagent
cannot `route`. Every call is charged to `MAX_TOOL_CALLS`, a refused one included — a session that
only ever sends malformed responses is stopped by the budget rather than running on.

A feature may watch what the model calls: `before_tool` is given the call and refuses it by
answering with a result, `after_tool` is given the call and what it produced. `route` reaches
neither, because the runtime answers it before the adapters are reached. A watcher that raises
ends the turn as `WatcherFailed`, which names it and the call. No feature declares one yet
(`AG-TOOL-031`, `AG-TOOL-032`).

## Context, and why its order is fixed

`ContextBuilder` builds the request in order of how often each block changes, so a remote provider
can cache the stable prefix:

```text
messages[0]  system   SYSTEM_PROMPT                  ← cache breakpoint
messages[1]  user     [System]: memory + workspace state
   …         user/assistant   the dialogue window
messages[-1] user     [System]: the clock            ← volatile, always last
```

Only `messages[0]` is a system message. Every other context block goes through `system_note`, which
sends it as a user message prefixed `[System]: ` — the Qwen3.5 chat template raises on a second
system message.

**New volatile context goes after the dialogue, never into a system block.** One timestamp in
`messages[0]` costs every cache hit and scatters OpenRouter's sticky provider routing.
`tests/snapshots/prompt_prefix.json` is what notices if it moves.

A routed subagent's context is the same order under its own prompt: prompt → workspace state →
`<Conversation>` → this turn's receipts → clock.

## `route` — one turn handed to a subagent

```mermaid
sequenceDiagram
    participant A as Advisor
    participant B as workspace mutator
    participant S as Screen
    participant O as Owner
    A->>B: route("workspace_mutator")
    B->>B: query_data, then one mutation tool per change
    B->>S: open review → review screen
    Note over A,B: whole chain suspends, status awaiting_approval
    O->>S: Save / Discard
    S->>B: resume
    B-->>A: receipt {did, text, error}
    A-->>O: one message
```

- A subagent reads the conversation as **data**: the newest `SUBAGENT_HISTORY_LAST_MESSAGES = 10`
  come as one `<Conversation>` block, one tag per author, so nothing it did not write reaches it in
  the `assistant` slot.
- A routed session must open with a tool call — only its first turn, because the loop ends on a turn
  that calls none.
- A routed subagent has no `route`, so there is no recursion.
- A request naming two domains is two routes and one message.
- `SUBAGENT_DEADLINE_SECONDS = 300` bounds a subagent by the clock, not by a call count, because it
  blocks the Advisor's turn. It bounds every active stretch, the one after a Save included; the
  owner's own time deciding is outside it. A subagent the clock stops hands its caller an error
  receipt, so the turn still answers.

**Current behavior, to be replaced by the planned Proposal lifecycle:** words typed over a screen
resume the turn that opened it. Every pending proposal in that batch is
discarded, the screen is frozen into an account of what the request did, and only then is anything
generated: the Advisor's pending `route` is answered with what was proposed, what was refused, what
was already saved, and the owner's words.

The subagent is left `interrupted` — unfinished rather than waiting — so a `route` back on that same
turn resumes it holding its own plan, and a correction reaches the session that wrote the refused
proposal. Its transcript carries one line saying the owner refused *and wrote instead*; on
"rejected" alone it would propose the same thing again. The turn that routed there is the outer
bound: when it answers or fails, `_close_unfinished_children` ends what it left behind.

A review nobody answers ends the chain the other way. After `PROPOSAL_REVIEW_MINUTES` on
screen, `AgentManager.close` answers the subagent's calls as `expired` and records it and the
Advisor that routed there `abandoned`: nothing is regenerated, and the owner's next words are a
new request rather than a correction to this one (`PR-EXPIRE-029`).

The routing rules in `SYSTEM_PROMPT` are generated from the roster, so a subagent is routed to
exactly when its `AgentSpec` is in `MODULES`; its `purpose` **is** the prompt line.

## `call_helper` — one turn that reads and answers with rows

```mermaid
flowchart LR
    Q[query_data result] -->|JOIN, GROUP BY, subquery, or capped| OFFER[notice names call_helper]
    OFFER --> TOOLS[tool added to this session]
    TOOLS --> CALL[call_helper]
    CALL --> HA[heavy_analyzer mini session]
    HA -->|query_data ×N| DB[(ai_* views)]
    HA -->|forward_output| ROWS[rows + the SQL]
    HA -->|report_failure| ERR[one sentence]
    ROWS --> A[Advisor keeps its turn]
    ERR --> A
```

- Nothing about helpers is in `SYSTEM_PROMPT`. **The read that needed one is what offers it**, and
  the tool is added to that session's tools there and then (`helper_offered`, which survives a
  suspension).
- Which read needs one is the feature's own hook: `complex_read_candidate` reads the completed
  call and its rows, using `worth_a_helper` and `OFFER`. `OfferTool` grants its named helper and
  adds the notice to the result. The grant stays in `host_state` across suspension and belongs
  only to that session. `HelperSpec` holds the helper's capability independently of the offer.
- A read that *failed* offers nothing, whatever the helper would have said: its `hint` already
  says to repair that one SELECT, and that half stays the engine's.
- `heavy_analyzer` is a **mini session** (`ai/mini.py`), not a routed subagent: read tools plus two
  terminal tools, prose is never accepted, and it is in no routing rule and no base tool set.
- It never speaks. The answer is the last read itself — fifty rows cannot be retold, and a small
  model retelling numbers is where numbers get invented.
- `HEAVY_ANALYZER_MAX_TOOL_CALLS = 10` bounds it. It blocks the Advisor exactly as `route` does, and
  `/cancel` cancels the turn and the helper with it.
- A helper that breaks returns an error, not an exception: losing the turn would be worse than the
  answer the Advisor can still give from what it read itself.

| | `route` | `call_helper` |
|---|---|---|
| what it does | hands the turn to a subagent that **writes** | asks one that only **reads** |
| a screen | it may open one | it cannot |
| the caller's turn | suspends | stays with the caller |
| in the tool set | by the roster | only where a read offered it |

## Proposals — the only way anything is written

```mermaid
flowchart LR
    T[mutation tool call] --> C[Pydantic contract]
    C --> P[ChangePreparer.prepare against live data]
    P -->|refused| ERR[one retryable tool error, and the turn goes on]
    P --> R[open review in ProposalStore]
    R --> AUTO{autoapproval?}
    AUTO -->|allowlisted and approved| APPLY
    AUTO -->|no, or any doubt| SCREEN[review screen · Save / Discard]
    SCREEN -->|Save| APPLY[approve_proposal]
    SCREEN -->|Discard| REJ[end the review]
    APPLY --> DOM[the same use cases the manual UI calls]
```

- The model never mutates and never writes mutation SQL.
- **A response that carries mutation calls carries the subagent's plan as its text**
  (`PR-PLAN-028`): what it will change, in order. `ToolAdapters.mutation` refuses every call
  in a response with no text as `plan_required`, one retryable error, and the session runs
  again. The text is the model's working record: it stays in the session's transcript, so a
  session picked up after a decision still reads what it meant to do, and it never reaches the
  chat.
- **Every mutation tool belongs to a subagent**, never to the Advisor. `workspace` owns the workspace,
  `diary` owns the Diary. Preparation runs where the change was authored.
- **Every proposal screen is exactly Save/Discard.** A screen that needs a field control is the
  wrong screen.
- **Autoapproval is the one exception to `PR-WRITE-002`, and `PR-AUTO-024` is where it is
  approved.** It decides only whether a screen is shown: it never bypasses preparation or the
  stored proposal, and any doubt leaves the pending screen untouched. A Save it asked for that is
  refused because the workspace moved on ends the review with it, and the queue moves on the way a
  failed manual Save moves it. Which actions are eligible is each feature's
  `ProposalContribution.autoapprovals`; a create is never one of them.
- **`PR-TARGET-001` is a shell rule the feature keeps.** The generic walk carries no entity, so
  loading the target, `archived_at` and the closed repeat are the owning feature's
  `ProposalHandler.prepare`, and `target_not_found` is the one refusal `proposals/` raises itself.
- `approve_proposal` is where one Save ends, the manual one and the automatic one alike: it checks
  `workspace.revision` before any handler runs, commits the write itself, and takes the review off
  the screen only once that commit stands. `StaleStateError` is the expected failure, and it is the
  one that ends the review instead — the screen is unanswerable rather than retryable.
- Several mutation calls in one turn queue as independent screens; the model resumes only after the
  last one resolves, each result handed back as a tool result.
- A review and its approval batch are process state, not rows: `ProposalStore` holds both, they
  carry no status, and a batch is over when no screen is still waiting. Nothing
  survives a restart, so startup only clears what pointed at a review — the buttons, and the
  `related_id` of the screens in the chat. Review ids only ever go up, so a screen that still names
  one cannot reach a later review.
- **A review nobody answers is closed by the system** (`PR-EXPIRE-029`). A proposal carries
  `shown_at` from the moment its screen is drawn — a queued one has none — and the Cue poll closes
  the one that has stood `PROPOSAL_REVIEW_MINUTES = 30`: its pending proposals are recorded
  `expired`, never `discarded`, so neither the owner nor the model reads a rejection into it; what
  the request had saved stays saved; the screen is frozen into an account that the system closed
  the request; and the session chain is `abandoned` through `AgentManager.close`, so nothing is
  resumed. It takes the background lease the way a Cue does, so it never runs inside the owner's
  turn, and a Save that lands after it finds the review gone.
- Anything the model must know across an approval belongs in a **tool result**, not in a receipt.

## Cues — what Safwa is given to say when nobody asked

Only the Advisor writes to the chat, so anything the system wants said reaches the owner as one
ordinary Advisor turn. A **Cue** is the finished request for that turn: whoever had the facts wrote
them down, so the Advisor relays rather than goes looking.

```mermaid
flowchart TB
    RM[the Reminder poll<br/>next_fire_at says when] --> ROW
    SP[finish_sprint<br/>in the transaction that ends it] --> ROW[(cues — one row, the words)]
    ROW --> CQ[the Cue poll, every 30s]
    CQ --> EXP[a review past PROPOSAL_REVIEW_MINUTES<br/>is closed first]
    EXP --> GATE{CueRuntime.can_speak?}
    GATE -->|advisor busy, pending proposal,<br/>or suspended run| WAIT[the row stays]
    GATE -->|free| TURN[CueRuntime.speak: one Advisor turn]
    TURN -->|the owner got it| DEL[the row is deleted]
    TURN -->|cancelled or failed| WAIT
    WAIT --> CQ
```

**The `cues` row is the only record of "Safwa still owes the owner these words."** It is written
inside the producer's own transaction and deleted only once the turn landed, so a crash, a shut gate
or an owner who interrupts mid-turn all lose nothing: the row is still there, and the next poll says
it again.

`CueRuntime` is the whole delivery mechanism, and there is one of it:

- **The gate** refuses while `turn.active`, while `ProposalStore.busy` — any open review or any
  suspended approval batch — and while any session holds `claimed_at`. An open proposal is an
  unanswered question, and raising a second one on top of it turns the chat into a stack of screens.
  Each tick closes the review that ran out of time before it reads the gate, so the Reminder that
  waited behind that screen is said on the same tick the screen is closed.
- **The lease** is `turn.try_begin_background()`. `still_current` compares `dialogue_revision`
  before and after the turn, so an owner who speaks mid-turn wins and the half-written answer is
  discarded.
- The answer is posted with `MessageKind.CUE`: it stays in dialogue, marked as something Safwa
  volunteered rather than a reply to a message that is not there.
- One waiting Cue is said per tick, oldest first.
- The Cue reaches the Advisor as an ordinary request from the system, answered the way the owner's
  own would be. A fired Reminder is the one that names items: the prompt tells the Advisor to read
  their current state with `query_data` before repeating an instruction that may no longer apply.

### Reminders — the poll is the alarm clock and nothing else

```mermaid
flowchart TB
    TICK[tick every SCHEDULER_POLL_SECONDS = 30] --> PEND{a Cue still waiting?}
    PEND -->|yes| TICK
    PEND -->|no| DUE{next_fire_at <= now?}
    DUE -->|no| TICK
    DUE -->|yes| PREP[prepare: stale repeats roll forward silently]
    PREP --> WRITE[one row: format_cue over up to REMINDER_FIRE_BATCH = 3]
    WRITE --> SETTLE[settle: one-shot deleted, repeat rolled forward]
    SETTLE --> TICK
```

There is no scheduling library and no in-memory timer. `Reminder.next_fire_at` says **when**, and
nothing more: the tick writes the words down and moves the row on in the **same transaction**. It
holds no gate, takes no lease and runs no Advisor turn — what guarantees the owner gets the words is
the Cue row, exactly as for anything else Safwa says first.

- **One thing waits to be said at a time.** A tick that finds a Cue still waiting writes nothing, and
  the Reminders it would have carried stay due for a later tick. That is what stops an hour of a busy
  owner turning into twelve messages the moment they are free.
- `is_stale`: a *repeating* Reminder more than `REMINDER_CATCHUP_GRACE_MINUTES = 120` overdue rolls
  forward silently, so a weekend offline does not produce 32 messages. A one-shot is never stale: it
  always fires, however late, and the Cue says how late.
- A repeat advances from its **scheduled** moment, not from the tick that took it, so a late check
  does not push every later fire late with it.
- Reminders are deterministic first — the poll only does schedule arithmetic, and the Advisor
  composes the message.

### A Sprint's end writes its own Cue

`finish_sprint` (by hand, or at the local midnight after the planned end date) writes a six-line
summary from the Sprint's own record — which Sprint and when, how it ended, its Success criteria,
the five effort figures, how the Actions ended up, the titles of what is still open — and hands it
over with `add_cue`, in the same transaction that ends the Sprint. Nothing dresses it as a
Reminder that went off: the words are the Sprint's own. Safwa is told how the Sprint went so it does
not go reading tables to find out, and ends its message with `[Sprint retro](retro:12)`.

## History — Telegram is the store, not SQLite

```mermaid
flowchart LR
    CHAT[(the real private chat)] -->|Telethon, every turn| SCAN[backwards scan]
    SCAN --> KIND{MessageKind}
    KIND -->|dialogue_user · dialogue_assistant<br/>cue · summary| WINDOW[the window]
    KIND -->|dashboard · editor · approval · receipt<br/>ui_input · status · error| DROP[excluded]
    WINDOW --> BUDGET{over SUMMARY_TRIGGER_TOKENS = 6000?}
    BUDGET -->|yes| SUM[write a 📜 Summary]
    SUM --> WINDOW
```

- `history.py` re-reads the real chat on every advisor turn.
  `telegram_messages` stores event metadata, never persona text.
- **Every bot message is sent registered and marked** with a `MessageKind`. An unregistered or
  unmarked message is invisible to the LLM; a wrongly-kinded one leaks UI noise into persona history.
- The window is a **token budget**, not a message count. There is no one budget split between the
  blocks: the dialogue is worth `SUMMARY_TRIGGER_TOKENS = 6000`, which is also what says when a
  Summary is written, and a Summary is asked to stay under `SUMMARY_TOKEN_CEILING = 2000`; memory
  is capped separately at `MEMORY_TOKEN_BUDGET`. The newest Summary is the far edge of the window,
  and up to `EDGE_CONTEXT_MESSAGE_LIMIT = 20` of the messages just before it come along with it.
- The window itself is [`telegram_llm/window.py`](../src/telegram_llm/window.py) and knows nothing
  of Summaries; which message ends it is answered on every read by
  [`features/summary/window.py`](../src/safwa/features/summary/window.py), and what each
  `MessageKind` means is said once in `history.py`.
- Owner text still in the chat **is** dialogue: commands and typed field values are deleted, so
  survival is the evidence.
- Words that never reached the chat as owner text — a voice transcript — are posted back as a bot
  message of the owner's kind, or the Advisor never sees them.
- A receipt line is replayed as a **tool result** rather than as words Safwa said
  (`RECEIPT_MEANINGS`, beside the decision it reports),
  so `✅ Saved` reads as `applied` rather than as something the persona claimed.

## Memory

`data/memory.md` is authoritative, ordinary UTF-8 text: each trimmed non-empty line is a fact, blank
lines are ignored, a missing file means empty memory. `memory_fact_cache` is a rebuildable derived
cache and never the source.

AI replacements write atomically and re-check the file hash, so a concurrent local edit is preserved
rather than overwritten. A file that is not UTF-8, or that is over `MEMORY_TOKEN_BUDGET = 4000`,
injects no memory and records why instead of failing the turn.

Two loops: `memory-file-poll` picks up the owner's own edits; `memory-maintenance` runs the daily
upkeep.

## Views and prompts

```mermaid
flowchart LR
    F1[cards/views.py] --> CAT
    F2[checks/views.py] --> CAT
    F3[...] --> CAT[SqlView declarations]
    CAT -->|dropped and rebuilt every startup| DB[(ai_* views)]
    CAT --> ALLOW[ALLOWED_VIEWS]
    CAT --> DOC["view_catalogue(views, names)"]
    DOC -->|{views}| P1["SYSTEM_PROMPT · ADVISOR_VIEWS"]
    DOC --> P2["workspace prompt · AgentSpec.views"]
    DOC --> P3["heavy_analyzer prompt · HelperSpec.views"]
```

- The views are dropped and rebuilt on **every startup**. Change a view's shape in the owning
  feature's `views.py`, never with a migration.
- A `SqlView` carries its own `doc`, so the block a model reads about a view lives beside the SELECT.
- **A reader declares its views once, and that list both describes and scopes it**: it fills the
  `{views}` block in the prompt and narrows that reader's own `query_data`, so a view no list names
  is refused rather than merely unmentioned. The Diary writes its block by hand, with columns
  trimmed on purpose, and still declares the four it may read.
- `view_catalogue` refuses a name no feature publishes and a view with no `doc`, and a reader that
  declares no views at all is a wiring error rather than a reader of everything.

`query_data` is triple-guarded: regex validation of one `SELECT`/`WITH … SELECT` over the `ai_*`
views, a separate read-only connection with an authorizer allowlist, and result caps
(`DEFAULT_ROW_LIMIT = 50`, `DEFAULT_CHAR_BUDGET = 12000`, `QUERY_TIMEOUT_SECONDS = 2`). The caps
exist because a local model pays for what it reads.

A saved Request shares the validation and nothing else: it runs on the ordinary session, and no cap
applies, because its result is always a list in the interface and never enters the model's history.

## Citations

The model writes `[Go to the market](card:12)`. The host resolves it: `render_citations` looks the id
up, builds a `t.me` deep link from the **validated** id, and names the item itself. An item that is
gone keeps its words and loses its link; a target that is not an id leaves the chat as plain words.

Types: `card`, `check`, `tag`, `value`, `request`, `diary`, `retro`.

**One catalogue answers both questions.** `ScreenCatalogue.types` is what may be cited *and* the
whole enum of the `open` tool, so a feature that publishes a screen is offered by name and a type
nothing publishes is refused rather than answered with an empty screen (`SC-OPEN-006`,
`RT-OPEN-002`).

When the decision is the owner's, the model **cites** the item in its own prose instead of proposing
one.

## Concurrency

`TurnManager` is the single foreground/background lease, and the only writer of `TurnState`.

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Answering: begin(message_id)
    Idle --> BackgroundWork: try_begin_background()
    Answering --> Idle: end(message_id)
    BackgroundWork --> Idle: end_background(lease)
    Answering --> Answering: callbacks rejected, other owner text deleted
    BackgroundWork --> Answering: cancel() — the owner always wins
```

- While an answer runs, callbacks are rejected and any other owner message is taken out of the chat,
  which is what makes it not something the owner said.
- `cancel()` stops the task holding the lease and bumps `dialogue_revision`: the work ends where it
  stands, and anything that still comes back against the old revision is discarded. Only
  `dialogue_revision` invalidates an in-flight answer — the answer's own autoapproved change moves
  `workspace.revision`, which is a different question.
- A lease is named by the revision it was taken at, so work that is cancelled and finishes
  afterwards gives back its own lease and never the one handed to whatever started next.
- Background work verifies the revision before publishing or committing, and a turn that lost the
  chat ends the review it had already opened rather than leaving it with no screen.
- `OwnerAndWritingMiddleware` drops anything that is not the owner in a private chat.
- **An action button is a single-use `CallbackToken` row**, sent as `cb:<token>` and cleared at the
  next start: it names an item and changes something, so it is spent when it is pressed and dead
  after a restart. **Navigation is not a token.** A `nav:<screen>` button carries the name of a
  screen the registry knows, changes nothing and names no item, so it is drawn again however often
  it is pressed and works on a menu older than the run answering it (`SC-BUTTON-003`).

## Background loops

| task | interval | owner |
|---|---|---|
| `cue-queue` | `SCHEDULER_POLL_SECONDS = 30` | `cues/background.py` |
| `reminder-scheduler` | `SCHEDULER_POLL_SECONDS = 30` | `features/reminders/background.py` |
| `sprint-expiry` | `SPRINT_EXPIRY_POLL_SECONDS = 300` | `features/planning/background.py` |
| `memory-file-poll` | `MEMORY_POLL_SECONDS = 5` | `features/memory/background.py` |
| `memory-maintenance` | `MEMORY_MAINTENANCE_INTERVAL_SECONDS = 60` | `features/memory/background.py` |

Each is a `BackgroundTask`. All but the Cue poll are declared in a feature's `module.py`; the Cue
poll belongs to no feature, so the registry puts it in front of theirs. The composition
root starts them and cancels them in the polling `finally`. A feature that needs its own objects takes them off
`services`, the container the whole application already shares.

## Recovery

[`recover_startup`](../src/tg_agent_shell/recovery.py) reconciles interrupted work on every
boot: each feature contributes a `recover` callable through its `FeatureModule`, and everything
after those hooks is the shell's own — the run rows, the buttons and the screens it wrote.

**A restart ends every session.** One left `running` and one left `awaiting_approval` are both
recorded `abandoned`, and their claims are released. Nothing picks either up: a screen is process
state and died with the process, and an unfinished session is only ever adopted by the turn that
routed to it, which died with it. What startup clears is what still pointed at a review — the
buttons, and the `related_id` of the screens in the chat.
