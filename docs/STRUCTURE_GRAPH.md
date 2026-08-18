# Safwa — structure graph

Устойчивая карта проекта: зависимости, владельцы данных, ответственность файлов и основные runtime
потоки. Здесь намеренно нет номеров строк и LoC-снимков. Навигация строится по пути файла и имени
символа — они значительно реже устаревают после обычных правок.

## Как читать карту

- Стрелка `A → B` означает, что `A` вызывает или использует `B`.
- `models.py` описывает форму данных, но бизнес-изменения принадлежат `domain.py`.
- Telegram хранит канонический persona-диалог; SQLite хранит классификацию и operational state.
- `memory.md` — самостоятельный авторитетный источник долговременной persona-памяти.
- Точки входа и ключевые символы указаны именами, которые удобно искать через IDE или `rg`.

## Система целиком

```mermaid
flowchart TD
    U["Owner в Telegram"] --> BOT["aiogram Bot + Dispatcher"]
    BOT --> T["telegram handlers и renderers"]
    T --> S["Services"]

    S --> A["AIAdvisor"]
    S --> H["TelegramHistorySource"]
    S --> M["MemoryFileStore"]
    S --> DB["SQLAlchemy / SQLite"]

    A --> P["OpenAI-compatible provider"]
    A --> Q["ReadOnlyQueryRunner"]
    A --> PS["ProposalService"]
    Q --> V["ai_* read-only views"]
    PS --> D["domain.py mutations"]
    T --> D
    D --> DB

    H --> TG["Telethon → реальный Telegram chat"]
    M --> MF["data/memory.md"]

    MAIN["main.py composition root"] --> BOT
    MAIN --> S
    MAIN --> BG["background tasks"]
    BG --> SCH["Reminder scheduler"]
    BG --> EXP["Sprint expiry poll"]
    BG --> CONT["Memory watcher + daily maintenance"]
    EXP --> D
    SCH --> A
    CONT --> A
```

## Направление зависимостей

```mermaid
flowchart LR
    CE["constants.py + enums.py"] --> MODELS["models.py"]
    MODELS --> DOMAIN["domain.py"]
    CE --> DOMAIN

    DOMAIN --> APP["scheduler / continuity / analytics / saved_requests"]
    MODELS --> APP

    DOMAIN --> AI["ai/*"]
    APP --> AI

    DOMAIN --> TG["telegram/*"]
    AI --> TG
    APP --> TG

    TG --> MAIN["main.py"]
    AI --> MAIN
    APP --> MAIN
```

Практическое правило: UI и AI не изменяют ORM-сущности напрямую. Оба пути сходятся в функциях
`domain.py`, а approved AI changes применяет `ProposalService` через те же domain-функции.

## Владельцы состояния

| Состояние | Авторитетный источник | Кто читает | Кто изменяет |
|---|---|---|---|
| Cards, Checks, Values, Tags, Sprints | SQLite models | UI, AI context, `ai_*` views | Только `domain.py` и `ProposalService` через domain |
| Persona dialogue | Приватный Telegram chat | `TelegramHistorySource` через Telethon | Owner и зарегистрированные bot sends |
| Классификация bot messages | Invisible kind + event UUID marker | `history.py` | `send_registered`, `mark_message`, `register_message` |
| Долговременная persona memory | `data/memory.md` | AI context, `/memory`, maintenance | File edits, `/mem`, `PersonaContinuity` |
| Proposal/agent progress | `AgentRun` (сессия и её `state_json`), `AgentStep`, `ChangeProposal` | AI continuation, proposal UI, recovery | `AIAdvisor`, `ProposalService`, approval handlers |
| Reminder schedule и delivery state | `Reminder` | Scheduler, UI, AI views | Domain operations и `scheduler.settle` |
| Diary clock и instruction | `UserProfile` | `sync_diary_reminder` → системный `Reminder` | Кнопки `/settings` через `update_profile` |
| Transient Telegram UI | `UiSession`, `CallbackToken` | Telegram handlers | `_messaging.py`, renderers, recovery |

## Composition root

### [`main.py`](../src/safwa/main.py)

Собирает приложение и управляет его жизненным циклом.

Ключевые точки:

- `run` создаёт `Database`, provider, history, memory, advisor, `Services`, Bot и Dispatcher;
- регистрирует bot commands и middleware;
- запускает memory file watcher, Reminder scheduler и daily memory maintenance;
- в `finally` отменяет background tasks и закрывает Telethon, provider, bot session и database;
- `main` загружает `Settings`, создаёт fresh schema и запускает event loop.

```mermaid
flowchart LR
    CFG["Settings"] --> DB["Database"]
    CFG --> PROVIDER["Provider"]
    CFG --> HISTORY["TelegramHistorySource"]
    DB --> ADVISOR["AIAdvisor"]
    PROVIDER --> ADVISOR
    HISTORY --> SERVICES["Services"]
    ADVISOR --> SERVICES
    SERVICES --> DISPATCHER["aiogram Dispatcher"]
    SERVICES --> TASKS["background tasks"]
```

## Foundation

| Файл | Ответственность | Ключевые символы |
|---|---|---|
| [`constants.py`](../src/safwa/constants.py) | Все лимиты, интервалы, budgets и tuning defaults | `EFFORT_POINTS`, history/memory/query/reminder limits |
| [`config.py`](../src/safwa/config.py) | Environment-backed runtime configuration и provider defaults | `Settings`, resolved provider properties |
| [`enums.py`](../src/safwa/enums.py) | Общий словарь состояний домена и UI | `CardKind`, `CardStage`, `MessageKind`, `ProposalStatus` |
| [`models.py`](../src/safwa/models.py) | Единственный источник SQLAlchemy schema | `Workspace`, `Card`, `Check`, `Reminder`, proposal/agent/history/UI models |
| [`db.py`](../src/safwa/db.py) | Engine/session factory, fresh-schema bootstrap и `ai_*` views | `Database`, `upgrade_database`, `create_ai_views` |

До первого выпуска schema обновляется пересозданием базы после backup. `create_all` добавляет
отсутствующее, но не изменяет существующие столбцы. Поддержка миграций начинается после v1.

## Domain и application services

| Файл | Ответственность | Ключевые символы |
|---|---|---|
| [`domain.py`](../src/safwa/domain.py) | Все planning mutations и инварианты | `bootstrap_workspace`, Card/Check/Tag/Value/Sprint operations, `start_sprint`, `expire_due_sprint`, `finish_action`, reference toggles |
| [`saved_requests.py`](../src/safwa/saved_requests.py) | Проверка и выполнение Saved Request queries | `normalize_request_sql`, `request_cards`, `RequestQueryError` |
| [`reminders.py`](../src/safwa/reminders.py) | Чистая арифметика расписаний без I/O | `resolve`, `describe`, `next_fire`, `roll_forward`, `schedule_of` |
| [`scheduler.py`](../src/safwa/scheduler.py) | Poll due Reminders, schedule preparation, delivery и settle; отдельный poll истёкшего Sprint | `Firing`, `due_reminders`, `prepare`, `settle`, `tick`, `run_scheduler`, `run_sprint_expiry` |
| [`continuity.py`](../src/safwa/continuity.py) | Summary и синхронизация Telegram dialogue → memory | `PersonaContinuity`, `run_memory_maintenance`, `run_due_memory_maintenance` |
| [`history.py`](../src/safwa/history.py) | Каноническая история из Telegram и event marker codec | `TelegramHistorySource`, `HistoryEntry`, `mark_message`, `read_message_mark`, citations |
| [`memory.py`](../src/safwa/memory.py) | Валидация, atomic replace и watcher для `memory.md` | `MemoryFileStore`, `MemorySnapshot`, `parse_memory`, `memory_hash` |
| [`asr.py`](../src/safwa/asr.py) | Транскрипция голосового сообщения: OpenAI-совместимый endpoint или локальный CTranslate2 | `AudioClip`, `TranscriptionResult`, `Transcriber`, `ProgressCallback`, `OpenAITranscriber`, `FasterWhisperTranscriber`, `build_transcriber` |
| [`recovery.py`](../src/safwa/recovery.py) | Startup reconciliation interrupted operational state | `recover_startup`, `reconcile_reminders` |
| [`analytics.py`](../src/safwa/analytics.py) | Retrospective data, recommendations и PNG | `retrospective_data`, `retrospective_recommendations`, `render_retrospective_png` |
| [`backup.py`](../src/safwa/backup.py) | Portable backup/restore SQLite + `memory.md` | `create_backup`, `restore_backup` и CLI entry points |
| [`qa.py`](../src/safwa/qa.py) | Изолированная конфигурация live Telegram QA | `QaConfig`, `ResolvedQaConfig`, `resolve_qa_config` |

## AI package

```mermaid
flowchart TD
    CTX["ai/context.py"] --> SERVICE["ai/service.py"]
    CONTRACTS["ai/contracts.py"] --> SERVICE
    PROVIDER["ai/provider.py"] --> SERVICE
    SQL["ai/sql.py"] --> SERVICE
    PREPARE["ai/prepare.py"] --> SERVICE
    SQL --> PREPARE
    MINI["ai/mini.py"] --> RS["ai/reminder_sessions.py"]
    MINI --> AUTO["ai/autoapproval.py"]
    DIARY["ai/diary.py"] --> SUB["ai/subagents.py"]
    BOARD["ai/board.py"] --> SUB
    RS --> PREPARE
    SUB --> SERVICE
    AUTO --> SERVICE
    SERVICE --> PROPOSALS["ChangeProposal + AgentStep"]
    SERVICE --> DOMAIN["domain.py through ProposalService"]
```

| Файл | Ответственность | Ключевые символы |
|---|---|---|
| [`ai/context.py`](../src/safwa/ai/context.py) | Static prompt, planning context и dialogue types | `SYSTEM_PROMPT`, `DialogueMessage`, `PlanningContext`, `planning_context` |
| [`ai/contracts.py`](../src/safwa/ai/contracts.py) | Pydantic schemas и native tool definitions | mutation tool models, `MUTATION_TOOL_MODELS`, `mutation_change_from_tool` |
| [`ai/provider.py`](../src/safwa/ai/provider.py) | OpenAI-compatible transport, retries и provider turns | `ProviderTurn`, `ProviderToolCall`, `OpenAICompatibleProvider` |
| [`ai/sql.py`](../src/safwa/ai/sql.py) | Read-only SQL validation и isolated SQLite execution | `validate_read_sql`, `ReadOnlyQueryRunner`, `UnsafeQueryError` |
| [`ai/mini.py`](../src/safwa/ai/mini.py) | Узкая tool-only LLM-сессия | `run_mini_session`, `ReadToolSpec`, `TerminalTool`, terminal/retry protocol |
| [`ai/autoapproval.py`](../src/safwa/ai/autoapproval.py) | Request-only semantic review для allowlisted proposal operations | `AutoApprovalReviewer`, `AutoApprovalRule`, `DEFAULT_AUTOAPPROVAL_RULES` |
| [`ai/reminder_sessions.py`](../src/safwa/ai/reminder_sessions.py) | Setup mini-session для расписаний Reminder | `resolve_schedule` |
| [`ai/subagents.py`](../src/safwa/ai/subagents.py) | Определение routed subagent: его промпт, tools и общий блок персоны | `RoutedSubagent`, `PERSONA` |
| [`ai/board.py`](../src/safwa/ai/board.py) | Board subagent: промпт и список mutation tools, которыми он владеет | `BOARD_PROMPT`, `BOARD_TOOLS` |
| [`ai/diary.py`](../src/safwa/ai/diary.py) | Diary subagent: единственный читатель и автор Diary — промпт, `read_day` и часы дня | `DIARY_PROMPT`, `day_read_tool`, `diary_clock`, `READ_DAY_TOOL` |
| [`ai/prepare.py`](../src/safwa/ai/prepare.py) | Проверка одного mutation change против живых данных до записи proposal | `ChangePreparer`, `PreparedChange`, `ToolPreparationError`, `ENTITY_MODELS` |
| [`ai/service.py`](../src/safwa/ai/service.py) | Main agent loop, read tools, proposals, continuation и apply | `AIAdvisor`, `AgentSession`, `AIOutcome`, `ProposalService`, `_run_agent_loop`, `_materialize` |

### Main advisor context

```mermaid
flowchart LR
    SYS["Static SYSTEM_PROMPT"] --> PLAN["Planning state + memory.md"]
    PLAN --> DIALOGUE["Canonical bounded dialogue"]
    DIALOGUE --> CLOCK["Trailing local clock"]
    CLOCK --> PROVIDER["Provider turn"]
```

`query_safwa` и `route` выполняются немедленно. Mutation tools создают отдельные proposal
screens. Если provider смешал immediate tools и mutations в одном response, reads выполняются, а
mutations получают retryable error и повторяются следующим response после появления read results.

### Routed subagent

```mermaid
flowchart LR
    ROUTE["route(name)"] --> SESSION["Своя AgentRun-сессия: PERSONA + промпт сабагента,<br/>свой бюджет истории и planning state по объявлению"]
    SESSION --> READ["read_day(date) + query_safwa над ai_diary/ai_card_events/ai_checks"]
    SESSION --> PROSE["Проза: возвращается вызвавшему в receipt.text, не в чат"]
    SESSION --> WRITE["diary(mode, date, pov, ai_comment, feeling_score)"]
    WRITE --> PREPARE["prepare: create или update по ai_diary; delete проверяет, что день есть"]
    PREPARE --> SCREEN["Save/Discard screen: pov + ai_comment → DiaryEntry (одна на дату)"]
    SCREEN --> RESUME["Approve/Discard восстанавливают ту же сессию, её receipt — вызвавшего"]
    SCREEN --> INTERRUPT["Слова поверх экрана: экран заморожен, черновик ждёт один ход Advisor,<br/>вызвавшая сессия отменена"]
    RECEIPT["Receipt: дата, score и длина — без текста дня"]
    SCREEN --> RECEIPT
```

## Telegram package

Пакет разделён на foundation, чистое представление, I/O, renderers и handlers. Зависимости направлены
вниз по этой схеме:

```mermaid
flowchart TD
    HANDLERS["commands.py / callbacks.py / dialogue.py"] --> FEATURES["cards.py / checks.py / items.py / diary.py / reminders.py / sprint.py / proposals.py / screens.py"]
    HANDLERS --> TEXT_INPUT["text_input.py"]
    FEATURES --> TEXT_INPUT
    TEXT_INPUT --> IO
    TEXT_INPUT --> CORE
    FEATURES --> IO["_messaging.py"]
    FEATURES --> VIEW["_presentation.py"]
    IO --> CORE["_core.py"]
    VIEW --> CORE
    ESC["escalation.py"] --> PROPOSALS["proposals.py"]
    ESC --> CORE
    INIT["__init__.py"] --> HANDLERS
```

| Файл | Ответственность | Ключевые символы |
|---|---|---|
| [`telegram/_core.py`](../src/safwa/telegram/_core.py) | Shared services, router, generation coordination и owner middleware | `Services`, `GenerationGuard`, `OwnerAndWritingMiddleware`, `CallbackContext` |
| [`telegram/_presentation.py`](../src/safwa/telegram/_presentation.py) | Чистые labels, text formatting, markup и paging | `Page`, menu helpers, proposal summaries, `split_telegram_text`, `name_from_about_me` |
| [`telegram/_messaging.py`](../src/safwa/telegram/_messaging.py) | Все send/edit/delete operations и semantic registration | `send_registered`, `edit_registered_message`, `dismiss_prior_ui`, `send_owner_turn`, `owner_display_name`, queue materialization |
| [`telegram/text_input.py`](../src/safwa/telegram/text_input.py) | Общий редактор обычного текста: Current value, Back, повторный рендер ошибки и custom validation | `TextInputScreen`, `render_text_input`, `validate_text_input`, `reject_text_input` |
| [`telegram/cards.py`](../src/safwa/telegram/cards.py) | Card overview, creation editor, selectors и общий список Cards | `card_list_rows`, `render_dashboard`, Card render/start/sanitize functions |
| [`telegram/sprint.py`](../src/safwa/telegram/sprint.py) | Today, Sprint dashboard и старт Sprint | `render_today`, `render_sprint`, `render_sprint_criteria_prompt`, `render_sprint_confirm` |
| [`telegram/checks.py`](../src/safwa/telegram/checks.py) | Check screens и answer UI | Check renderer и pending-check gate screens |
| [`telegram/items.py`](../src/safwa/telegram/items.py) | Shared Tag/Value editors и Saved Request screens | item renderer, list screens, text prompts |
| [`telegram/reminders.py`](../src/safwa/telegram/reminders.py) | Manual Reminder list/detail/text screens | Reminder renderers и schedule presentation |
| [`telegram/diary.py`](../src/safwa/telegram/diary.py) | Read-only экран одного дня Diary | `render_diary` |
| [`telegram/screens.py`](../src/safwa/telegram/screens.py) | Deep links из advisor prose в domain screens | `render_citations`, `open_citation`, `open_item_screen` |
| [`telegram/proposals.py`](../src/safwa/telegram/proposals.py) | Read-only proposal UI, queue progression и agent continuation | `render_proposal`, `continue_agent_approval`, `render_ai_outcome` |
| [`telegram/escalation.py`](../src/safwa/telegram/escalation.py) | Due Reminder → bounded dialogue → main advisor | `ReminderRuntime`, `format_escalation` |
| [`telegram/commands.py`](../src/safwa/telegram/commands.py) | Slash-command handlers | dashboard, memory, summary, settings, status и cancellation commands |
| [`telegram/callbacks.py`](../src/safwa/telegram/callbacks.py) | Single-use `cb:` action handlers | item/card/check/proposal callbacks |
| [`telegram/dialogue.py`](../src/safwa/telegram/dialogue.py) | Ordinary owner text → advisor loop → queued turns → summary | `ordinary_text` |
| [`telegram/__init__.py`](../src/safwa/telegram/__init__.py) | Импорт handler-модулей ради router registration | package exports |

Только `commands.py`, `callbacks.py` и `dialogue.py` регистрируют router handlers. Удаление их imports
из `telegram/__init__.py` молча отключит соответствующие маршруты.

## Ключевые runtime-потоки

### Обычный advisor turn

```mermaid
sequenceDiagram
    actor U as Owner
    participant MW as Middleware
    participant H as TelegramHistorySource
    participant A as AIAdvisor
    participant T as Telegram UI
    participant C as PersonaContinuity

    U->>MW: Обычный текст
    MW->>MW: foreground guard
    MW->>H: dialogue в пределах token budget
    H-->>A: canonical DialogueMessage list
    A-->>T: answer или proposal
    opt Пока шла генерация пришли новые сообщения
        MW->>T: queued placeholders
        T->>A: объединённый следующий user turn
    end
    T->>C: maybe_summarize под background guard
```

### Голосовое сообщение

Голосовое не несёт текста, поэтому Telethon его не видит: транскрипт — единственный след сказанного,
и он попадает в чат как bot message с меткой `DIALOGUE_USER`, ровно как отложенный текст владельца.

```mermaid
flowchart LR
    V["voice / audio / video_note"] --> G["duration и size guards"]
    G --> D["bot.download"]
    D --> TR["Transcriber.transcribe"]
    TR -.->|"faster_whisper: сегменты"| P["STATUS-сообщение с процентами, затем удаляется"]
    TR --> L["lease: background отменяется, чужой foreground ждёт или отменяется"]
    L --> S["send_owner_turn: owner_display_name + split_telegram_text + DIALOGUE_USER"]
    S --> RT["run_dialogue_turn"]
    L -.->|guard занят и очередь открыта| Q["queue_owner_text"]
```

### Proposal queue

```mermaid
flowchart LR
    CALLS["Mutation tool calls"] --> MATERIALIZE["AIAdvisor._materialize"]
    MATERIALIZE --> ROWS["ChangeProposal + ProposalChange + approval_batch + AgentRun.state_json"]
    ROWS --> UI["Один Save/Discard screen"]
    UI --> APPLY["ProposalService.apply"]
    APPLY --> DOMAIN["domain.py"]
    UI --> NEXT["Следующий proposal или continuation"]
    NEXT --> RECEIPT["Saved / Discarded / Failed receipt"]
```

### Reminder escalation

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant G as GenerationGuard
    participant H as TelegramHistorySource
    participant A as Main AIAdvisor
    participant T as Telegram

    S->>G: reserve_background
    S->>H: dialogue(owner_id)
    H-->>S: token-bounded dialogue
    S->>A: dialogue + synthetic escalation user turn
    Note over A: Для упомянутых Safwa items<br/>сначала query_safwa в том же turn
    A-->>T: REMINDER answer или proposal
    T-->>S: delivered
    S->>S: settle one-shot/repeating state
    S->>G: release background
```

### History reconstruction

```mermaid
flowchart TD
    CHAT["Telethon messages"] --> MARK["Read kind + event UUID marker"]
    MARK --> FILTER["Exclude UI, approvals, receipts, errors и commands"]
    FILTER --> STOP{"Первый достигнутый край"}
    STOP -->|"Token budget"| WINDOW["Newest messages, cut на границе сообщения"]
    STOP -->|"Summary"| SUMMARY["Summary + до 20 older messages + newer dialogue"]
    STOP -->|"Oldest registration"| WINDOW
    WINDOW --> TURNS["Merge canonical user/assistant turns"]
    SUMMARY --> TURNS
```

### Memory maintenance

```mermaid
flowchart LR
    HISTORY["New canonical dialogue"] --> RETELL["Chunk retelling"]
    RETELL --> RECONCILE["Reconcile durable facts"]
    RECONCILE --> VALID{"Valid JSON и current lease?"}
    VALID -->|"Нет"| KEEP["Не менять file/cursor"]
    VALID -->|"Да"| FILE["Atomic memory.md replace"]
    FILE --> CURSOR["Advance processed_until"]
```

## Модели по подсистемам

| Подсистема | Модели |
|---|---|
| Workspace и profile | `Workspace`, `UserProfile` |
| Planning tree | `Card`, `CardValue`, `CardTag`, `CardCheck`, `Check`, `Value`, `Tag`, `CardEvent` |
| Sprint | `Sprint`, `SprintCommitment`, `CardCategory`, `CardEnergyType` |
| Saved queries | `SavedRequest` |
| Agent и approvals | `AgentRun`, `AgentStep`, `ChangeProposal`, `ProposalChange` |
| Diary | `DiaryEntry`, `DiaryStamp` |
| Telegram continuity | `TelegramMessage`, `SummaryState`, `UiSession`, `CallbackToken` |
| Persona memory | `MemoryFactCache`, `MemorySyncState` |
| Proactive work | `Reminder`, `FeedbackQueue` |

## Карта тестов

| Область | Основные tests |
|---|---|
| Domain/Card/Check invariants | `test_domain.py`, `test_checks.py`, `test_card_creation.py` |
| SQL и Saved Requests | `test_ai_sql.py`, `test_saved_requests.py` |
| Telegram history и event markers | `test_history.py` |
| UI, callbacks, proposal receipts, queue | `test_telegram_item_ui.py` |
| Summary и memory | `test_continuity.py`, `test_memory.py` |
| Reminder arithmetic и scheduler | `test_reminders.py`, `test_reminder_flow.py`, `test_scheduler.py` |
| Provider/config/infrastructure | `test_provider.py`, `test_config.py`, `test_infrastructure.py`, `test_backup.py` |
| Голосовой ввод | `test_asr.py`, voice-тесты в `test_telegram_item_ui.py` |
| Subagents и Diary | `test_subagents.py`, `test_diary.py`, `tests/e2e/test_subagent_e2e.py`, `tests/e2e/test_diary_e2e.py` |
| End-to-end agent behavior | `tests/e2e/test_advisor_flow_e2e.py`, `test_reminder_e2e.py`, `test_checks_e2e.py`, `test_startup_e2e.py` |

## Куда вносить изменение

| Изменение | Начать здесь | Затем проверить |
|---|---|---|
| Новый domain invariant | `domain.py` | AI contract, manual UI, proposal apply, domain/e2e tests |
| Новое поле schema | `models.py` | domain, renderers, AI views/context; fresh DB до v1 |
| Новый read-only AI view | `db.py` | `ALLOWED_VIEWS`, `SYSTEM_PROMPT`, SQL tests |
| Новый mutation tool | `ai/contracts.py` | `ai/service.py`, proposal presentation, prompt и e2e tests |
| Новый subagent | Модуль рядом с `ai/diary.py` | roster в `SYSTEM_PROMPT`, wiring в `main.py`, subagent tests |
| Новый Telegram screen | Feature renderer | `_presentation.py`, `_messaging.py`, callback token routing |
| Новая slash-команда | `telegram/commands.py` | bot command registration в `main.py`, history classification |
| Изменение истории | `history.py` | marker/send sites, continuity, history tests и diagrams |
| Изменение Reminder flow | `scheduler.py` + `telegram/escalation.py` | reminder sessions, guard, history window и scheduler tests |
| Изменение памяти | `memory.py` + `continuity.py` | watcher, commands, cursor atomicity и backup |
| Новый ASR backend | `asr.py` | `ASRProvider`, `ASR_DEFAULTS`, wiring в `main.py`, `.env.example` |

Связанные документы: [ARCHITECTURE.md](ARCHITECTURE.md),
[MEMORY_HISTORY_USAGE.md](MEMORY_HISTORY_USAGE.md),
[REMINDERS_PLAN.md](REMINDERS_PLAN.md) и [диаграммы](diagrams/README.md).
