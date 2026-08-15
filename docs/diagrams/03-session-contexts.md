# Контексты LLM и окно истории

«Сессия» здесь означает отдельный вызов LLM со своим узким prompt: main advisor, reminder
mini-session, subagent, summary или memory maintenance. Видимых Telegram-веток нет: окно диалога ограничено
token budget, ближайшим Summary и самой старой регистрацией.

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
    E --> SUB["Diary subagent"]
    E --> SUM["Summary"]
    E --> RETELL["Memory retell + reconcile"]

    MAIN --> MC["SYSTEM_PROMPT + planning + memory<br/>+ bounded Telegram dialogue + clock<br/>+ все Safwa tools"]
    SETUP --> SC["SETUP_PROMPT + when + instruction<br/>+ local time/timezone<br/>+ только terminal tools"]
    SUB --> SBC["DIARY_PROMPT + сегодняшняя дата и request advisor;<br/>нужный день subagent определяет сам и читает<br/>через read_day(date) и query_safwa (ai_diary и др.)<br/>+ один terminal diary_report"]
    SUM --> SUC["SUMMARY_PROMPT + предыдущий Summary<br/>+ unsummarized canonical dialogue"]
    RETELL --> MEC["RETELL_PROMPT + chunk;<br/>затем MEMORY_PROMPT + facts + retelling"]
```

Reminder escalation использует тот же main advisor и тот же `history.dialogue(owner_id)`, что обычный
запрос. Сначала `history.py` ограничивает окно, затем synthetic
текст сработавших Reminders добавляется последним user turn. Поэтому модель получает обычный
system/planning/memory/dialogue/clock context и все Safwa tools.

Если Reminder упоминает Safwa item, synthetic turn просит main advisor сначала проверить его через
`query_safwa`; отдельной relevance-сессии нет.

Mini-sessions не создают proposals и approval queue. Они обязаны завершиться валидным terminal tool
call; проза, неизвестный tool и невалидные аргументы возвращаются модели как retryable ошибка.
Reminder setup не пишет `AgentRun`; subagent пишет свой собственный и ограничен wall-clock deadline
вместо cap на число вызовов.

Код: [context.py](../../src/safwa/ai/context.py),
[service.py](../../src/safwa/ai/service.py),
[mini.py](../../src/safwa/ai/mini.py),
[subagents.py](../../src/safwa/ai/subagents.py),
[diary.py](../../src/safwa/ai/diary.py),
[reminder_sessions.py](../../src/safwa/ai/reminder_sessions.py),
[history.py](../../src/safwa/history.py),
[continuity.py](../../src/safwa/continuity.py).
