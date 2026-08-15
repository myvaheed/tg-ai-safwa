# История Telegram и история для LLM

## Два разных представления

Полная переписка физически живёт в приватном чате Telegram. SQLite не хранит текст persona-диалога:
таблица `telegram_messages` содержит идентификаторы, направление, `MessageKind` и связь с объектом.
Перед каждым обычным запросом Safwa заново читает чат через Telethon и строит отфильтрованный список
`DialogueMessage` для LLM.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant TG as Telegram
    participant B as aiogram / Bot API
    participant DB as SQLite
    participant H as TelegramHistorySource
    participant L as AIAdvisor / LLM

    U->>TG: Обычный текст
    TG->>B: Message с Bot API message_id
    B->>DB: register_message DIALOGUE_USER
    B->>H: dialogue(chat_id, source_message)
    H->>TG: Telethon iter_messages с newest к oldest
    H->>DB: Ищет исходящие по Safwa event UUID
    Note over H,DB: Текст сообщений в SQLite не копируется
    H->>H: Восстанавливает inline kind, event UUID и citations
    H->>H: Останавливается на budget, Summary или самой старой регистрации
    H->>H: Отбрасывает UI, команды, approvals и ошибки
    H->>H: Только owner source дедуплицирует по ID/времени
    H->>H: Добавляет source_message ровно один раз
    H-->>B: Канонические user / assistant turns
    B->>L: system + planning/memory + dialogue + clock
    L-->>B: Ответ или tool calls
    B->>TG: Ответ с невидимыми MessageKind + event UUID
    B->>DB: Регистрирует event UUID и классификацию ответа
```

У каждого исходящего сообщения в Telegram-тексте есть невидимые `MessageKind` и immutable event UUID.
Тот же UUID хранится в SQLite, поэтому изменение видимого текста не ломает связь, а временная эвристика
для исходящих сообщений не нужна. SQLite — восстанавливаемый индекс: marker остаётся авторитетным.

## Что попадает в контекст

```mermaid
flowchart TD
    A["Сообщение из реального Telegram-чата"] --> S{"Кто отправил?"}
    S -->|"Другой пользователь"| X["Исключить"]
    S -->|"Safwa bot"| BK{"Тип сообщения"}
    S -->|"Owner"| UK{"Текст и регистрация"}

    BK -->|"DIALOGUE_ASSISTANT или REMINDER"| IN["Включить как assistant"]
    BK -->|"DIALOGUE_USER"| UIN["Включить как user<br/>(восстановленная очередь)"]
    BK -->|"SUMMARY"| BD["Дальний край окна и user-side context"]
    BK -->|"UI, APPROVAL, RECEIPT, ERROR и прочее"| X
    BK -->|"Нет marker"| X

    UK -->|"DIALOGUE_USER"| UIN["Включить как user"]
    UK -->|"Незарегистрированный обычный текст"| UIN
    UK -->|"Slash-команда или UI input"| X

    BD --> OLDER["Добавить до 20 более старых сообщений"]
    OLDER --> OUT["Сформировать DialogueMessage[]"]
    IN --> OUT
    UIN --> OUT
```

В контекст допускаются только `DIALOGUE_USER`, `DIALOGUE_ASSISTANT`, `REMINDER` и `SUMMARY`.

Команды, callback-действия, dashboards, редакторы, prompts для ввода поля, proposals, receipts,
SQL/tool payloads, ошибки и PNG ретроспективы исключаются. Все slash-команды удаляются из чата —
именно поэтому уцелевший текст owner считается диалогом.

## Край окна и сбор turns

```mermaid
flowchart LR
    N["Новые сообщения, newest-first"] --> B{"Первый достигнутый край"}
    B -->|"Token budget"| R["Cut на границе сообщения"]
    B -->|"Summary"| S["user: Summary"]
    B -->|"Самая старая регистрация"| R
    S --> C["До 20 более старых сообщений"]
    C --> R
    R --> M["Соседние user-side элементы объединяются"]
    M --> T["Соседние assistant replies объединяются"]
    T --> D["DialogueMessage role/content"]
```

Локальный timestamp добавляется раз в час разговора, а не к каждому сообщению. Текущее сообщение
пользователя передаётся как `source_message`: если Telethon-регистрация уже сопоставлена, дубликат не
добавляется; иначе оно дописывается в конец. Узкая ID/time-корреляция остаётся только здесь, потому что
бот не может встроить marker в сообщение owner. Немаркированные bot-сообщения не считаются диалогом:
версия 1 не поддерживает legacy fallback.

Код: [history.py](../../src/safwa/history.py),
[dialogue.py](../../src/safwa/telegram/dialogue.py),
[_messaging.py](../../src/safwa/telegram/_messaging.py),
[models.py](../../src/safwa/models.py).
