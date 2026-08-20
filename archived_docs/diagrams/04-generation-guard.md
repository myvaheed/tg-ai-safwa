# Guard и очередь во время генерации

`GenerationGuard` выдаёт один lease на AI-работу. Foreground lease принадлежит исходному Telegram
`message_id`; background lease использует `BACKGROUND_SOURCE_ID`. `dialogue_revision` позволяет
отбросить результат уже отменённой работы.

## Новое сообщение во время foreground-ответа

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant MW as Middleware
    participant G as GenerationGuard
    participant TG as Telegram
    participant D as ordinary_text
    participant L as LLM

    U->>D: Первый запрос
    D->>G: acquire(message_id, queue_messages=true)
    D->>L: Генерация ответа
    U->>MW: Второй обычный текст
    MW->>G: begin_queue(text)
    MW->>TG: Сразу удалить owner message
    MW->>TG: Generating response... /cancel for cancelling.<br/>Queued: текст
    MW->>G: finish_queue(placeholder_id)
    L-->>D: Ответ на первый запрос
    D->>TG: Опубликовать ответ
    D->>G: drain_queue()
    D->>TG: Удалить все placeholders
    D->>TG: Name Surname: объединённый текст
    Note over D,TG: Несколько запросов разделены строкой ----
    D->>L: Следующий turn с объединённым запросом
```

Placeholder имеет `MessageKind.UI_INPUT` и не попадает в LLM-историю. Восстановленный текст
отправляется ботом с `MessageKind.DIALOGUE_USER`, поэтому в истории остаётся ровно один owner-side
turn. Заголовок — `User <отображаемое имя в Telegram>`, либо просто `User`.

Если генерация завершилась ошибкой, очередь всё равно материализуется и placeholders удаляются —
текст не теряется. `/cancel` отменяет текущий lease и также восстанавливает очередь.

## Проверка устаревшего результата

```mermaid
sequenceDiagram
    participant D as ordinary_text
    participant G as GenerationGuard
    participant DB as Workspace
    participant L as LLM
    participant TG as Telegram

    D->>G: Зафиксировать dialogue_revision
    D->>DB: Зафиксировать workspace.revision
    D->>L: Генерация
    L-->>D: Outcome
    D->>G: Сверить dialogue_revision
    D->>DB: Сверить workspace.revision
    alt Любая revision изменилась
        D-->>TG: Не публиковать устаревший outcome
    else Snapshot актуален
        D->>TG: render_ai_outcome
    end
```

Summary дополнительно перечитывает каноническую историю перед публикацией и сравнивает snapshot.
Memory maintenance проверяет lease после каждого provider-вызова и перед записью.

## Foreground и background

```mermaid
stateDiagram-v2
    [*] --> Free
    Free --> Foreground: owner request
    Free --> Background: summary / memory / reminder
    Foreground --> Foreground: queued owner text
    Foreground --> Free: answer / failure / cancel
    Background --> Free: success / failure
    Background --> Free: owner event отменяет background
```

Reminder получает background lease **до** чтения истории и вызова main advisor и держит его до
delivery/settle. Owner event инвалидирует lease: готовый позже результат не публикуется, due row не
продвигается и следующий tick повторяет Reminder уже с актуальной историей и planning context.
Summary и daily memory используют тот же lease вместо независимой одноразовой проверки `busy`.

Код: [_core.py](../../src/safwa/telegram/_core.py),
[dialogue.py](../../src/safwa/telegram/dialogue.py),
[_messaging.py](../../src/safwa/telegram/_messaging.py),
[continuity.py](../../src/safwa/continuity.py),
[scheduler.py](../../src/safwa/scheduler.py),
[escalation.py](../../src/safwa/telegram/escalation.py).
