# Контексты LLM и границы сессий

В проекте слово «сессия» используется в двух смыслах:

- видимая ветка Telegram, ограниченная `/newsession` и завершаемая `/endsession`;
- отдельный вызов LLM со своим узким prompt: main advisor, reminder mini-session, summary, memory
  maintenance или сжатие subsession.

## Main advisor context

Сообщения всегда расположены от наиболее стабильных к наиболее изменчивым. Часы находятся после
диалога, чтобы не разрушать кэшируемый prefix.

```mermaid
flowchart LR
    A["1. SYSTEM_PROMPT<br/>правила Safwa и tools"] --> B["2. Planning state + memory.md<br/>profile, Values, Tags, Sprint, critical, Today"]
    B --> C["3. Канонический Telegram dialogue<br/>уже ограниченный history.py"]
    C --> D["4. Current local time<br/>последний system message"]
    D --> L["Provider complete_turn"]

    A -.->|cache breakpoint| CA["stable cache"]
    B -.->|cache breakpoint| CB["state cache"]
    C -.->|cache breakpoint после последнего turn| CC["dialogue cache"]
```

`planning_context` содержит режим workspace, About Me, инструкции advisor, активные Values,
доступные Tags, текущий Sprint с его Success criteria (или сообщение о Planning), до десяти critical
Cards и — только при запущенном Sprint — Actions в Today. Каждый элемент записан как citation
`[name](kind:id)`. Остальные данные, кандидаты и метрики модель читает через `query_safwa`.

## Какие данные получает каждый LLM-вызов

```mermaid
flowchart TD
    E{"Тип LLM-сессии"}
    E --> MAIN["Main advisor"]
    E --> SETUP["Reminder setup mini-session"]
    E --> SUM["Summary"]
    E --> RETELL["Memory retell + reconcile"]
    E --> SUB["Subsession compression"]

    MAIN --> MC["SYSTEM_PROMPT + planning + memory<br/>+ bounded Telegram dialogue + clock<br/>+ все Safwa tools"]
    SETUP --> SC["SETUP_PROMPT + when + instruction<br/>+ local time/timezone<br/>+ только terminal tools"]
    SUM --> SUC["SUMMARY_PROMPT + unsummarized canonical dialogue"]
    RETELL --> MEC["RETELL_PROMPT + chunk;<br/>затем MEMORY_PROMPT + facts + retelling"]
    SUB --> SBC["SUBSESSION_RESULT_PROMPT<br/>+ transcript ветки + optional instruction"]
```

Reminder escalation использует тот же main advisor и тот же `history.dialogue(owner_id)`, что обычный
запрос. Сначала `history.py` применяет ближайшую `/newsession` или Summary boundary, затем synthetic
текст сработавших Reminders добавляется последним user turn. Поэтому модель получает обычный
system/planning/memory/dialogue/clock context и все Safwa tools.

Если Reminder упоминает Safwa item, synthetic turn просит main advisor сначала проверить его через
`query_safwa`; отдельной relevance-сессии нет.

Mini-sessions не создают `AgentRun`, proposals и approval queue. Они обязаны завершиться валидным
terminal tool call; проза, неизвестный tool и невалидные аргументы возвращаются модели как retryable
ошибка.

## Telegram subsession

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant TG as Telegram
    participant H as History
    participant A as AIAdvisor

    U->>TG: /newsession Начальный запрос
    Note over TG: Команда остаётся видимой как SESSION_START
    U->>TG: Диалог внутри изолированной ветки
    TG->>H: История обрывается на этом /newsession
    H-->>A: Initial request + только сообщения ветки
    U->>TG: /endsession optional instruction
    TG-->>U: Экран подтверждения
    U->>TG: Confirm
    TG->>H: Прочитать канонический transcript ветки
    H->>A: compress_subsession transcript + instruction
    A-->>TG: Компактный Subsession result
    TG->>TG: Удалить сообщения ветки
    Note over TG: Оставить видимый SUBSESSION_RESULT
    TG->>H: Следующий запрос родительского диалога
    H-->>A: Subsession result как user-side context
```

Отмена `/endsession` ничего не удаляет. Завершение ветки не удаляет Cards, Sprint, Values, Reminders
или `memory.md`: удаляется только соответствующий диапазон Telegram-сообщений, а итог остаётся частью
родительского контекста.

Код: [context.py](../../src/safwa/ai/context.py),
[service.py](../../src/safwa/ai/service.py),
[mini.py](../../src/safwa/ai/mini.py),
[reminder_sessions.py](../../src/safwa/ai/reminder_sessions.py),
[history.py](../../src/safwa/history.py),
[commands.py](../../src/safwa/telegram/commands.py).
