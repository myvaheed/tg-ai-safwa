# Память `memory.md`

`data/memory.md` — авторитетная долговременная persona-память. Формат: UTF-8, одна непустая
устойчивая мысль на строку, общий лимит около 4000 токенов. `memory_fact_cache` в SQLite — только
перестраиваемое зеркало.

## Чтение, ручные изменения и prompt

```mermaid
flowchart TD
    F["data/memory.md"] --> SYNC["MemoryFileStore.sync"]
    W["Watcher каждые 5 секунд"] --> SYNC
    START["Запуск приложения"] --> SYNC
    PROMPT["Перед advisor prompt"] --> SYNC
    CMD["/mem, /syncmem или правка файла"] --> SYNC

    SYNC --> V{"UTF-8, нет пустых строк,<br/>токены в бюджете?"}
    V -->|"Да"| SNAP["MemorySnapshot valid"]
    SNAP --> CACHE["Перестроить memory_fact_cache"]
    SNAP --> LLM["Вставить после planning state"]
    V -->|"Нет"| BAD["Файл не изменять;<br/>memory injection и AI writes отключены"]
    BAD --> WARN["Дедуплицированное предупреждение"]

    MISSING["Файл отсутствует"] --> EMPTY["Пустая валидная память"]
    EMPTY --> CACHE
```

Приоритет в prompt: явные настройки профиля и активные Values выше, чем выводы из `memory.md`.

## Автоматическая синхронизация из диалога

```mermaid
sequenceDiagram
    participant C as Команда или daily loop
    participant G as GenerationGuard
    participant H as TelegramHistorySource
    participant M as PersonaContinuity
    participant L as LLM
    participant F as memory.md
    participant DB as SQLite

    C->>G: Получить background lease
    alt Safwa занята
        G-->>C: Пропустить до следующей проверки
    else Свободна
        C->>M: maintain_memory(chat_id)
        M->>F: sync и сохранить expected_hash
        M->>DB: Прочитать processed_message_id
        M->>H: recent(require_boundary=true)
        H-->>M: Только новые канонические сообщения
        M->>M: Разбить примерно по 2K токенов с overlap
        loop Для каждого chunk
            M->>L: RETELL_PROMPT + chunk
            L-->>M: Компактный retelling
            M->>L: MEMORY_PROMPT + текущие facts + retelling
            L-->>M: JSON facts
            alt JSON и schema валидны, lease актуален
                M->>M: Принять новый список facts
            else Ответ невалиден или lease отменён
                Note over M,DB: Остановить run; cursor не продвигается
            end
        end
        M->>F: replace_facts(facts, expected_hash)
        alt Hash не изменился
            F-->>M: Atomic replace выполнен
            M->>DB: Обновить processed_message_id и file_hash
        else Было локальное изменение
            F-->>M: MemoryFileError; локальная версия сохранена
            Note over M,DB: processed_message_id не продвигается
        end
    end
```

`/syncmem` запускает этот поток вручную. Плановый поток включается через `/setmemtime HH:MM`,
проверяется раз в минуту и выполняется не чаще одного успешного раза за локальный календарный день.
Пятисекундный watcher не вызывает LLM: он только импортирует локальные изменения файла.

## Атомарная запись

```mermaid
flowchart TD
    A["Получить новые facts и expected_hash"] --> B{"Текущий hash совпадает?"}
    B -->|"Нет"| E1["Sync актуального файла и MemoryFileError"]
    B -->|"Да"| T["Записать memory.md.tmp, flush, fsync"]
    T --> C{"Исходный файл всё ещё имеет expected_hash?"}
    C -->|"Нет"| E2["Удалить tmp; сохранить локальную правку"]
    C -->|"Да"| R["os.replace tmp на memory.md"]
    R --> S["Sync файла и SQLite mirror"]
```

Изменение маркера `processed_message_id` происходит только после успешной атомарной записи. Это
гарантирует повторную обработку диалога после сбоя и не позволяет AI затереть более свежую ручную
правку.

Код: [memory.py](../../src/safwa/memory.py),
[continuity.py](../../src/safwa/continuity.py),
[commands.py](../../src/safwa/telegram/commands.py),
[main.py](../../src/safwa/main.py).
