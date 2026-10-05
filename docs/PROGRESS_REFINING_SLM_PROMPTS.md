# Промпты под малые локальные модели (4–12B)

Начат 2026-10-05. Здесь все замечания к system prompts и схемам тулов каждого агента, сабагента
и мини-сессии, решения владельца по ним и журнал работы. Работа идёт по одному агенту за раз:
сначала обсуждение замечаний, потом план правки, потом код. Замечание закрывается, когда правка
сделана и проверена; тогда его статус и журнал внизу меняются в том же коммите.

## Зачем

Runtime будет работать на локальной модели 4–12B (по умолчанию `SAFWA_AI_BASE_URL` в
`.env.example` указывает на локальный сервер). Такая модель хуже держит длинные инструкции,
чаще путает похожие инструменты и поля, буквально понимает метафоры и не умеет считать даты.
Цель: каждый контекст чёткий, короткий, однозначный. Требования к стилю уже записаны в
[CLAUDE.md](../CLAUDE.md): короткие повелительные строки, по одной инструкции на строку, точные
имена, без объяснений «почему» и без повторов правил.

## Как читать статус

- **открыто** — замечание записано, решения нет;
- **решено** — владелец согласился, есть черновик или план правки;
- **отклонено** — владелец не согласился, причина записана;
- **снято** — замечание оказалось неверным;
- **сделано** — правка в коде, снапшот промптов обновлён.

## Что видит модель: инвентарь

Размер — символы system prompt вместе со схемами тулов, которые получает агент; токены — грубо,
символы / 4. Всё это стоит перед диалогом при каждом вызове.

| Агент | Где | Промпт + тулы | ≈ токенов | Приоритет |
|---|---|---|---|---|
| Advisor | [advisor/agent.py](../src/safwa/features/advisor/agent.py) | 14.2K + 4.4K | 4.7K | 1 |
| Workspace Mutator | [workspace_mutator/agent.py](../src/safwa/features/workspace_mutator/agent.py) | 0.8K + 8.4K + 15K | 6.1K | 1 |
| Hook-запросы к Advisor | `cards/hooks.py`, `planning/hooks.py`, `checks/hooks.py` | ~12 текстов | — | 1 |
| Onboarding | [onboarding/agent.py](../src/safwa/features/onboarding/agent.py) | 0.8K + 18.8K + 0.4K, история 100 сообщений | 5K | 2 |
| Heavy analyzer helper | [heavy_analyzer/agent.py](../src/safwa/features/heavy_analyzer/agent.py) | 5.9K + 0.9K | 1.7K | 2 |
| Sprint | [planning/agent.py](../src/safwa/features/planning/agent.py) | 0.8K + 4.7K + 1.5K | 1.8K | 3 |
| Retro | [retro/agent.py](../src/safwa/features/retro/agent.py) | 0.8K + 2.5K + ~5K (6 тулов) | 2.1K | 3 |
| Diary | [diary/agent.py](../src/safwa/features/diary/agent.py) | 0.8K + 3.1K + 3.5K | 1.9K | 3 |
| Profile | [profile/agent.py](../src/safwa/features/profile/agent.py) | 0.8K + 1.7K + 2.9K | 1.4K | 3 |
| Мини-сессии (14 штук) | см. раздел ниже | 0.2–1.1K каждая | < 0.3K | 3 |

Первые 0.8K у каждого сабагента — общий блок `PERSONA`. Перечень промптов и тулов, которые
стоят перед диалогом, собирает `test_rule_i_prompt_prefix_is_byte_stable`; мини-сессии в него не
входят.

## Advisor

`SYSTEM_PROMPT` собирается из шаблона в [advisor/agent.py](../src/safwa/features/advisor/agent.py):
список маршрутов `{routes}` заполняется из поля `purpose` каждого сабагента, каталог views — из
описаний views фич (`cards/views.py`, `checks/views.py`, `foundation/log_events.py` и другие).
Каталог views общий для Advisor, Workspace Mutator, Sprint и Heavy analyzer: правка в нём меняет
все четыре промпта.

### A1. Изменения — только по явной просьбе — сделано

**Что не так.** Раздел Routing перечисляет, что делает каждый сабагент, но не говорит, когда
маршрут можно выбрать. Ничто не запрещает Advisor отправить совет или идею в
`workspace_mutator` по своей инициативе. Раздел Reminders прямо приглашает к этому: «Then respond
or propose changes normally».

**Решение владельца.** Advisor вызывает `workspace_mutator` только тогда, когда пользователь
явно просит что-то создать или изменить.

**Решено владельцем 2026-10-05.**

1. Правило действует для каждого маршрута, который что-то меняет: `workspace_mutator`, `diary`,
   `profile`, `sprint`, `onboarding` (остановка). Исключение одно: фото уходит в `diary` сразу
   (AD-PHOTO-005).
2. Текст сработавшего Reminder — слова самого пользователя и считается его просьбой. Отдельной
   строкой в промпт не идёт: это уже говорит первая строка Reminders («answer it exactly as you
   would answer the user»).
3. Ответ пользователя на вопрос hook-а («сколько времени заняло?» — «40 минут») — это «да»
   на предложенное изменение.

Текст правки и сценарий — в разделе «Батч 1» ниже.

### A2. Переставить Routing в начало — отклонено

**Было предложено.** Routing стоит на строке ~150, после каталога views на ~60 строк; поднять
его выше.

**Возражение владельца.** Каталог views так же нужен, как маршруты. Весь system prompt — это
начало контекста; конец контекста — диалог. Перестановка внутри начала ничего не даёт.

**Вывод.** Владелец прав. Эффект «lost in the middle» относится к позиции во всём контексте,
а system prompt целиком (~4.7K токенов) стоит в его начале. Надёжных данных, что порядок разделов
внутри него меняет следование правилам на 4–12B, нет. Если проверка на целевой модели покажет
ошибки маршрута, есть другой рычаг: короткое напоминание правила маршрута в конце, рядом со
state-блоком. Это решение на потом, а не сейчас.

### A3. `enum` для имён в `route`, `forward`, `call_helper` — отклонено

**Было предложено.** Поле `name` — свободная строка «spelled as listed»; добавить `enum` с
именами сабагентов.

**Возражение владельца.** Это лишние токены. При ошибке в имени тул возвращает ошибку с `hint`,
где перечислены все сабагенты, и модель исправится.

**Вывод.** Владелец в основном прав. Ошибка действительно чинится: `unknown_subagent` в
`ai/tools.py` возвращает `hint` со списком имён. Цена ошибки — один лишний вызов модели и один
вызов из бюджета тулов (отказанный вызов тоже списывается, см. `agent_runtime/loop.py`).
Сам `enum` стоит дёшево: ~20–30 токенов на тул, и имена уже перечислены в Routing. Реальная
польза от `enum` есть только тогда, когда сервер ограничивает декодирование по схеме: тогда
неверное имя физически не может появиться. Делает ли это локальный сервер для аргументов тулов,
зависит от сервера и модели; не проверено. Вернуться, если проверка на целевой модели покажет
ошибки в именах.

### A4. Убрать объяснения «почему» — сделано

Строки, где объяснение можно убрать, а правило оставить:

| Где | Сейчас | Предложение |
|---|---|---|
| Agile structure | «Effort is approximate, because recovery does not add up: a Sprint total is a load signal of the right order, never a number to take a percentage of.» | «A Sprint EP total is a rough load. Never turn it into a percentage.» |
| Reminders | «Some questions Safwa asks on its own — about a blocked Action, later others — are automatic reactions the user switches off in Profile.» | «Questions Safwa asks on its own are automatic reactions. To switch one: `route("profile")`.» |
| Diary | «Nothing else in Safwa records how anything felt; the rest of the data only says what was done. So read the Diary whenever…» | «Read `ai_diary` when the question is about mood, energy, a stretch of time ("how was my week") or a pattern.» |
| Diary | «the link opens the whole day, so never retell it» | «Never retell a cited day.» |
| Memory | «Persistent memory is what the retro analysis of each Sprint left; nothing else writes it.» | «Memory is what the retro analysis left. You cannot change it.» |
| Memory | «Nothing checked the experiment's result. It is about that Sprint, not a durable fact about the user.» | «Its experiment is unchecked. Use it for the current Sprint only, never as a fact about the user.» |
| Каталог views, `ai_cards` | «to total effort or time always add `WHERE kind = 'action'`, or each action is counted again inside every parent» | «To total effort or time, add `WHERE kind = 'action'`.» |
| Каталог views, `ai_checks` | «…`card_series_id` is the series of its card, so one query counts every answer across every copy of a repeating action» | «…`card_series_id` is the series of its card: count answers across all copies of a repeating action by it.» |
| Каталог views, `ai_log_events` | «archiving that happened on its own writes no row, so `archive` is always the user's own» | «`archive` rows are always the user's own.» |

Ограничения:

- Строки Memory описаны сценарием AD-MEMORY-002; новые слова должны сохранить все его исходы:
  гипотеза и паттерн, противоречивый паттерн не правило, эксперимент не проверен, это не
  постоянный факт о пользователе.
- Правки каталога views меняют промпты Workspace Mutator, Sprint и Heavy analyzer тоже.

### A5. Шкала Effort Points в Advisor не нужна — снято

**Было предложено.** Advisor не записывает effort, значит шкала ему не нужна.

**Возражение владельца.** Advisor effort читает: на вопрос «какие у меня действия больше 8 EP»
он должен правильно написать `query_data`.

**Вывод.** Владелец прав. Значения 0.5…13 для запроса есть в каталоге views, но смысл ступеней
нужен для совета о нагрузке («два Action по 8 EP в Today — много»). Шкала остаётся. Объяснение
в соседней строке убирается по A4.

### A6. Маршруты пересекаются — сделано

Пересечение — это когда на один и тот же вопрос есть два верных пути: Advisor отвечает сам из
state или `query_data`, либо отправляет в сабагент. Для малой модели это развилка без правила.
Ответ через сабагент стоит три вызова модели вместо одного: Advisor, сабагент, `forward`.

Что Advisor уже имеет каждый ход (state-блок, `workspace_mutator/state.py`): режим, About me,
Advisor instructions, Effort Points on/off, активные Values, Tags, номер и даты Sprint, Success
criteria, Priority Goals, Today Actions, локальное время. Длины Sprint и capacity в нём нет.

| № | Вопрос пользователя | Путь 1 | Путь 2 | Рекомендация |
|---|---|---|---|---|
| 1 | «Сколько дней осталось в Sprint?» | Advisor: даты из state + текущее время, сам считает | `route("sprint")`: «answer about the running Sprint: its dates, … its days left» | Считать даты малая модель не умеет. Положить в state готовую строку «day N of M, K left»; Advisor отвечает сам; из `purpose` Sprint убрать «answer about … dates, days left». |
| 2 | «Какая у меня длина Sprint?» | `route("sprint")`: «its length» | `route("profile")`: «answer what it holds: … Sprint length and capacity» | Прямой конфликт внутри списка маршрутов. Длина и capacity — поля Profile: оставить только `profile`, из `purpose` Sprint убрать «its length». |
| 3 | «Сколько EP я взял в Sprint?» | Advisor: `query_data` по `ai_current_sprint_metrics` | `route("sprint")`: «its effort» | Advisor сам: view у него есть. Из `purpose` Sprint убрать «its effort». После 1–3 Sprint только меняет: start, finish, Success criteria. |
| 4 | «Что у меня в About me?» | Advisor: About me есть в state | `route("profile")`: «answer what it holds» | Простое правило для малой модели: вопрос о поле Profile — `profile`. Либо: Advisor отвечает сам о полях, которые есть в state. Решает владелец. |
| 5 | «Что я сделал за прошлый Sprint?» | Advisor: `ai_log_events` по `sprint_id` | `route("retro")`: «a question about the Sprints that ended» | Sprint по номеру или «прошлый Sprint» — `retro`. Лог — только для дня или диапазона дат. |
| 6 | «Как работают Checks?», «Как поставить Sprint?» | Advisor: в его промпте есть описание домена | `route("onboarding")`: «what a part of it is for or how to do something in it» | Кнопки и экраны знает только onboarding. Вопрос «как сделать» или «что это» — всегда `onboarding`; Advisor сам не объясняет интерфейс. |
| 7 | «Напоминай писать дневник в 21:00» | `route("workspace_mutator")`: Reminder | `route("profile")`: Diary time | Время Morning, Diary и Summary — поля Profile: `profile`. Остальные «напомни в …» — Reminder через `workspace_mutator`. |
| 8 | «Покажи retro Sprint 26.09-01» | Advisor: `open` с `item_type` retro (тип есть в его enum) | `route("retro")`: показывает retro | У Advisor нет view, где найти id retro, а угадывать нельзя. Retro открывает только `retro`; Advisor открывает retro лишь по ссылке из диалога. |

Итог рекомендаций: Sprint только меняет; профильные поля и времена — `profile`; закрытые Sprint —
`retro`; интерфейс — `onboarding`; всё, что есть в state или в views Advisor, он отвечает сам.

**Решено владельцем 2026-10-05.** Общий принцип: в промпт Advisor не добавлять строк-исключений;
пересечение снимается описанием сабагента (`purpose`) или ошибкой, которая возвращает Advisor на
верный маршрут.

- Случаи 1 и 3 — приняты: готовые числа в state, Advisor отвечает сам, Sprint только меняет.
- Случай 2 — снят переносом длины и capacity Sprint из Profile в Planning: «Батч 2» ниже.
  До него длина идущего Sprint — его собственные даты, а не поле Profile: поле могли поменять
  после старта (PL-ASK-026).
- Случай 4 — как есть: оба пути дают верный ответ.
- Случай 5 — явно прописать в `purpose` retro. Уточнение: `get_retro_data` отдаёт только числа
  закончившегося Sprint (взято, сделано, EP, время, Success criteria), списка items в нём нет.
  Поэтому retro — числа и итоги; «какие items я сделал» остаётся логом Advisor.
- Случай 6 — интерфейс (экраны, кнопки, команды) упомянуть в `purpose` onboarding.
- Случай 7 — если Advisor отправил время Morning, Diary или Summary в `workspace_mutator`, тот
  ничего не предлагает и отвечает, что это поле Profile. Advisor уже обязан маршрутизировать
  заново, когда результат не тот («if something is missing or wrong, route again»).
- Случай 8 — когда `open` у Advisor не находит item того типа, который открывает сабагент,
  ошибка называет маршрут этого сабагента.

### A7. Формат ответа — сделано

**Что не так.** Правила о форме ответа разбросаны по пяти разделам: `route` один в ответе
(Routing), `forward` один (Routing), `open` для одного item и «one short line» (Answering),
ссылки (Answering), квитанции (Answering), «never retell» (Diary), «concise, warm» (первая
строка). Вдобавок промпт не говорит, какая разметка дойдёт до Telegram. `telegram_llm/text.py`
переводит в HTML только жирный (двойные звёздочки или подчёркивания), курсив (одинарные),
зачёркнутый, inline-код, блоки кода и ссылки. Заголовки `#` и таблицы, которые малые модели пишут охотно, придут
пользователю как сырой текст.

Как runtime обрабатывает ответ (`agent_runtime/loop.py`): если в ответе есть вызовы тулов, текст
рядом с ними пользователю не идёт; `route` и `forward` не делят ответ с другими вызовами; ответ
без вызовов — это ответ пользователю.

**Черновик** (заменяет раздел Answering и разбросанные строки о форме):

```text
# Your response
A response is one of these:
- Read tools: `query_data`, `get_scheduled`, `relook`. Several at once is fine.
- `route(name)` alone.
- `forward(name)` alone.
- `open(item_type, id)`, then one short line.
- Your answer to the user.

# Your answer
- The user's language.
- Short: the answer first, then at most a few lines.
- Cite every item you name: `[Go to the market](card:12)`, `[Milk](check:14)`, `[Health](value:3)`, `[home](tag:7)`, `[Stale Actions](request:2)`, `[04.03.2026](diary:12)`. Real IDs only, from the context or `query_data`.
- Name a Stage, Priority, Category or Energy in the user's words, never its code.
- Markup: plain lines, "- " lists, **bold**, *italic*. No headings, no tables.
- Never write the Saved/Discarded/Failed receipt. Call a change saved only when `did` says so.
- Report an `error` plainly.
```

**Решено владельцем 2026-10-05.** Жёсткого предела длины ответа нет.

## Батч 1 — Advisor: A1, A4, A6, A7 — сделано

Сделан 2026-10-05 по второй редакции плана. Отличие от плана одно: сценарий про подсказку
`open` записан не в screens.feature, а в agents.feature рядом с AG-OPEN-052 — это правило тула
сессии, а не экрана.

### Что меняется

**1. `SYSTEM_PROMPT_TEMPLATE`** в [advisor/agent.py](../src/safwa/features/advisor/agent.py).

Routing целиком:

```text
# Routing
You read; you never write. Only a subagent changes anything.
`route(name)` hands the work to one subagent and brings back what it did. Send `route` alone in a response.
Route a change only when the user's newest message asks for it. One exception: a photo, below.
- Asks: create, add, change, rename, move, finish, link, delete, write; or "yes" to a change you offered.
- Does not ask: a question, advice, a plan to discuss, a wish, a complaint.
- When it does not ask: answer in words. To offer a change, end with one question, like "Create it?"
{routes}
- A photo alone, or a photo with words about their day: `route("diary")` at once. Never ask what to do with it.
- The result carries `did` (already saved), `text` (the subagent's answer) and `error`. Check it against the request: if something is missing or wrong, route again.
- `text` answers the request: call `forward(name)` alone. The user gets it as it is; never retell it.
- Otherwise answer in your own words, using `text` as data.
- If the user answers a proposal with words instead of a button, those words come to you. If they are about that proposal, route back to the same subagent on this response.
```

Reminders: первая строка остаётся; «Then respond or propose changes normally» уходит; строка об
automatic reactions — по A4:

```text
# Reminders
A Reminder is a trigger the user set: instruction text plus a schedule. When it fires, that text arrives as an ordinary request from the system — answer it exactly as you would answer the user.
When a fired Reminder names Safwa-items, read them with `query_data` first and check the Reminder still applies.
Questions Safwa asks on its own are automatic reactions. The user switches them off in Profile.
To switch one off or on: `route("profile")`. To stop onboarding: `route("onboarding")`.
```

Answering заменяется тремя разделами:

```text
# Advice
- Start advice and planning from the Priority Goals in their listed order. Judge every recommendation against those Goals, the Sprint Success criteria and the active Values.
- When the question is about balance or burnout, read recent Done Actions and their energy with `query_data` first.

# Your response
A response is one of these:
- Read tools: `query_data`, `get_scheduled`, `relook`. Several at once is fine.
- `route(name)` alone.
- `forward(name)` alone.
- `open(item_type, id)`: only when the user asked to see or open one item. Then one short line.
- Your answer to the user.
Obey the `hint` on a tool error and the `notice` on a capped query.

# Your answer
- The user's language.
- Short: the answer first, then a few lines at most.
- Cite every item you name: `[Go to the market](card:12)`, `[Milk](check:14)`, `[Health](value:3)`, `[home](tag:7)`, `[Stale Actions](request:2)`, `[04.03.2026](diary:12)`. Real IDs only, from the context or `query_data`.
- Name a Stage, Priority, Category or Energy in the user's words, never its code.
- Markup: plain lines, "- " lists, **bold**, *italic*. No headings, no tables.
- Never write the Saved/Discarded/Failed receipt. Call a change saved only when `did` says so.
- Report an `error` plainly.
```

Остальные строки A4 — по таблице в A4: Agile structure, Diary, Memory.

**2. Каталог views** — три строки A4 в `cards/views.py`, `checks/views.py`,
`foundation/log_events.py`. Меняет также промпты Workspace Mutator, Sprint и Heavy analyzer.

**3. `purpose` сабагентов** — из них собирается список маршрутов Advisor.

- Sprint, [planning/agent.py](../src/safwa/features/planning/agent.py): «start or finish the
  Sprint, or write the next Sprint's Success criteria.» Промпт самого Sprint («You also answer
  questions about the Sprint») не трогается: это раунд Sprint.
- Retro, [retro/agent.py](../src/safwa/features/retro/agent.py): «a question about a Sprint that
  ended — its number, dates, Success criteria, how many Actions or EP it took and finished, totals
  and averages over several — or showing its retro, its charts, or Life in weeks: the weeks of the
  user's life as one picture.»
- Onboarding, [onboarding/agent.py](../src/safwa/features/onboarding/agent.py): «the user asks
  what Safwa is, what a part of it is for, or how to do something in it with its screens, buttons
  and commands; wants a tour; asks to stop onboarding; or a request starts with "Onboarding."»

**4. `MUTATOR_PROMPT`**, раздел Reminders, одна строка:

```text
The Morning, Diary and daily summary times are Profile fields, not Reminders: propose nothing and say so.
```

**5. Shell, `open`** в `ai/tools.py`. Когда `open` корневой сессии не находит item, а тип этого
item объявлен в `opens` какого-то сабагента, `hint` называет маршрут:
`route("retro") finds a retro by the user's words and opens it.` Имя берётся из реестра
сабагентов, shell не знает слова retro. Ошибка сабагента остаётся прежней.

**6. State-блок** в `workspace_mutator/state.py`. Строка Sprint получает день, остаток и capacity:

```text
Sprint 26.10-01: 2026-10-01 – 2026-10-14, 14 days. Today is day 5. Days left after today: 9.
Sprint capacity: 20 EP.
```

Вторая строка — только при включённых Effort Points и заданной capacity. В Planning к строке о
Success criteria добавляется:

```text
A Sprint started today runs 2026-10-05 – 2026-10-18, 14 days.
```

Счёт дня — `sprint_day` (planning api). Длина в Planning — оттуда же, откуда её берёт
`sprint_now`; после Батча 2 источник сменится, строка останется.

Руководство onboarding не меняется: правило просьбы — внутреннее, пользователю не интересно.

### Сценарии

Новый, в [advisor.feature](../tests/brd/advisor.feature):

```gherkin
Scenario: AD-ASK-007 — Safwa hands over a change only when the owner asks for it
  Given the owner asks a question, asks for advice, or talks over a plan
  Then Safwa answers in words and hands nothing over to be changed
  And when a change would help, it asks whether to make it
  When the owner asks for a change, or says yes to one Safwa offered
  Then Safwa hands it to the part that makes it
  And a photo still goes to the Diary at once (AD-PHOTO-005)
```

Новый, в [agents.feature](../tests/brd/tg_agent_shell/agents.feature):

```gherkin
Scenario: AG-OPEN-054 — An item Safwa cannot find is left to the subagent that opens its kind
  Given a subagent declares that it may open one kind of screen
  When Safwa asks to open an item of that kind that does not exist
  Then it is refused, and told to route the request to that subagent
  But when that subagent cannot find one, it is told to look the id up with its own tools
```

Переформулировка без смены исходов, PL-ASK-026 в
[planning.feature](../tests/brd/planning.feature): Safwa отвечает из state, который ей дают в этот
ход, — даты Sprint, сегодняшний день и остаток; в Planning — длина и даты Sprint, начатого сегодня.

### Тесты

- AD-ASK-007: промпт несёт правило просьбы и исключение для фото.
- AG-OPEN-054 (`tests/e2e/test_retro_e2e.py`): `open` корневой сессии по несуществующему retro —
  `hint` с маршрутом retro; у сабагента — прежний `hint`.
- PL-ASK-026: к проверкам `sprint_now` добавлены проверки `workspace_context`: день, остаток,
  capacity идущего Sprint и даты Sprint, начатого сегодня.
- Поправить проверки фраз, которые меняются: `test_cards.py` (CD-BLOCKED-034, «switches off in
  Profile»), `test_subagents.py` (AD-MEMORY-002).
- Обновить снапшот промптов именованным тестом `test_rule_i_prompt_prefix_is_byte_stable`.
- Полный набор тестов и Ruff: правка широкая.

## Батч 2 — длина Sprint на экране Sprint — сделано

Идея владельца. Сделан 2026-10-05 по плану ниже; отличия — в «Как сделано».

**Что.** Убрать длину Sprint из Profile. Длина следующего Sprint живёт рядом с его черновиком
Success criteria (они уже хранятся в Workspace), задаётся на экране Sprint в Planning и словами
через сабагент Sprint. Идущий Sprint держит свою длину в своих датах, как сейчас.

**Что это даёт.** Пропадает пересечение `sprint` и `profile` (A6, случай 2); Profile, его промпт
и тул становятся меньше; всё о следующем Sprint — Success criteria, план, длина — в одном месте.

**Что затронет.** Поле Profile и снапшот схемы; экран Profile, тул `profile`, `PROFILE_PROMPT`,
сценарии Profile; экран Planning (новая кнопка и ввод числа), тул `sprint`, `SPRINT_PROMPT`,
сценарии Planning (PL-START-005, PL-ASK-026 и другие); руководство onboarding (разделы Profile и
Sprint — это пользователю интересно); источник длины в `sprint_now` и state.

**Решено владельцем 2026-10-05.**

1. Capacity переезжает туда же: Sprint хранит capacity, с которой стартовал (PL-CAPACITY-027).
   В Profile остаются личные поля и переключатели.
2. Ввод на экране — число с клавиатуры, тот же приём, что сейчас в Profile.
3. Словами: `sprint` с `mode="update"` принимает длину и capacity, только в Planning.

### План

**Хранение.** В Workspace, рядом с черновиком `sprint_success_criteria`, две колонки следующего
Sprint: длина в днях (по умолчанию 14) и capacity в EP или пусто. Из профиля обе колонки и оба
`ProfileField` уходят. Константа длины по умолчанию переезжает в `foundation/workspace.py`
(это default колонки); `SPRINT_LENGTH_MIN_DAYS` и `SPRINT_LENGTH_MAX_DAYS` из `constants.py` —
в Planning: после переноса их читает одна фича.

**Дверь.** `sprint_length_days(session)` и `capacity_effort_points(session)` переезжают из
`profile/api.py` в `planning/api.py` с теми же именами и смыслом: capacity при выключенных Effort
Points — None. Обёртка в `planning/use_cases.py` удаляется.

**Операции** в `planning/use_cases.py`: задать длину следующего Sprint (целое 2–60) и его capacity
(положительное число или off). Обе — только в Planning; capacity — только при включённых Effort
Points. Экран и Save вызывают их же.

**Экран Planning** (`planning/telegram/sprint.py`):

```text
Planning
Success criteria: …
Length: 14 days, 05.10 – 18.10
Planned: 6 Actions · 18 EP · capacity 20 EP
```

Кнопки: «🏁 Length: 14 days» и, при включённых Effort Points, «⚖️ Capacity: 20 EP». Каждая
открывает ввод числа тем же приёмом, что Success criteria; формулировки ввода — из нынешнего
Profile. Строка «Planned» уже показывает capacity, отдельной строки не нужно.

**Словами.** Тул `sprint`: два поля — `length_days` (целое) и `capacity_effort_points` (число;
null выключает, тот же приём, что был в Profile). С `mode="update"` задают их для следующего
Sprint, с `mode="create"` — в том же Save, что и старт. Отказы — до экрана, как PS-AI-019: длина
вне 2–60, capacity не больше нуля, Sprint уже идёт, capacity при выключенных Effort Points.
Экран review для update показывает каждое изменённое поле: было → станет.

**Промпты.**

- Sprint, `purpose`: «start or finish the Sprint, or set the next Sprint's Success criteria,
  length or capacity.»
- `SPRINT_PROMPT`: строки о тул `sprint` — про два новых поля; «Change the Sprint length or the
  capacity: they are in the Profile» → «Change a running Sprint's length or capacity»; «the
  Profile's Sprint length» → «the next Sprint's length». Остальное — в раунде Sprint.
- `sprint_now`: «Sprint length in the Profile: N days» → «Next Sprint's length: N days».
- Profile, `purpose`: без «Sprint length and capacity»; `PROFILE_PROMPT`: без строк о них; тул
  `profile`: без двух полей.
- Advisor: строка «The Sprint length and capacity are Profile fields: `route("profile")`»
  удаляется — это теперь говорит `purpose` Sprint.
- State Advisor в Planning: к строке о Sprint, начатом сегодня, добавляется capacity при
  включённых Effort Points.

**Руководство onboarding** — это видит пользователь: раздел Sprint (кнопки «🏁 Length» и
«⚖️ Capacity», длина задаётся в Planning), раздел Profile (без длины и capacity), строка «The
length and the capacity are in the Profile», строка EP о Sprint length в Profile.

**Сценарии.**

PS-SPRINT-LENGTH-003 и PS-CAPACITY-004 выводятся из обращения; их содержание переходит в Planning
под новыми номерами:

```gherkin
Scenario: PL-LENGTH-035 — The next Sprint's length is set in Planning, between 2 and 60 days
  Given the workspace is in Planning
  Then the Planning screen shows the next Sprint's length and the dates it would run if started today
  When the owner sets it, on that screen or in words, to a whole number of days from 2 through 60
    (SPRINT_LENGTH_MIN_DAYS = 2, SPRINT_LENGTH_MAX_DAYS = 60)
  Then every Sprint started after that runs that many days, until the length is changed
  But 1 day and 61 days are refused, and the length keeps what it had
  And while a Sprint runs, setting it is refused: a running Sprint keeps its dates (PL-MODE-002)

Scenario: PL-CAPACITY-036 — The next Sprint's capacity is set in Planning, a number of points or off
  Given Effort Points are on and the workspace is in Planning
  Then the Planning screen shows the next Sprint's capacity
  When the owner sets it, on that screen or in words, to a positive number of effort points, a half included
  Then it is accepted, and off means no capacity at all
  But zero and a negative number are refused
  And while a Sprint runs, setting it is refused: the Sprint keeps the capacity it started with (PL-CAPACITY-027)
  And with Effort Points off the screen shows no capacity, and setting it in words is refused (PS-EP-021)
```

Переформулировки без смены исходов: PL-MODE-002 («the Profile's Sprint length» → «the next
Sprint's length»; «the length and the capacity are the Profile's» → их можно задать и словами,
только в Planning), PL-START-005, PL-PLAN-018, PL-CAPACITY-027 («in the Profile» → «of the next
Sprint»), PS-FIELD-002 (полей на два меньше). PS-AI-019 меняет пример: Home after и Morning time
вместо Sprint length и Morning time; отказ «61 day» → отказ Home after вне диапазона.

**Тесты.** Около 60 мест в восьми файлах ставят длину или capacity через профиль: переводятся
на новые операции. Новые: PL-LENGTH-035 и PL-CAPACITY-036 (операции, экран Planning, тул
`sprint`, отказы). Снапшоты: схема (`user_profile` без двух колонок, `workspace` с двумя),
промпты (Advisor, Sprint, Profile, тулы `sprint` и `profile`). Полный набор и Ruff.

**Допущения.** Подписи кнопок «🏁 Length» и «⚖️ Capacity». Владелец согласился и велел удалить
PLAN_FEATURES.md целиком.

### Как сделано

- **Хранение и дверь** — как в плане: `Workspace.sprint_length_days` и
  `Workspace.sprint_capacity_effort_points`; `SPRINT_LENGTH_DAYS` в `foundation/workspace.py`;
  `SPRINT_LENGTH_MIN_DAYS` и `SPRINT_LENGTH_MAX_DAYS` в `planning/api.py`. Там же отказы
  `length_refusal` и `capacity_refusal`, их спрашивают и операции, и подготовка proposal.
- **Операции:** `set_sprint_length` и `set_sprint_capacity` в `planning/use_cases.py`.
- **Экран.** Кнопка Success criteria, «🏁 Length» и «⚖️ Capacity» ведут на один callback
  `sprint_edit` с именем поля; ввод — один поток `sprint` с полем в состоянии, поля описаны
  в `PLANNING_FIELDS`, как `PROFILE_FIELDS` в Profile. «⚖️ Capacity» — только при
  включённых Effort Points.
- **Review.** Update показывает «Next Sprint» и блок на каждое изменённое поле: Now / Becomes.
  Старт показывает длину и capacity из proposal, если они в нём есть.
- **Отличие от плана: `sprint_now` в идущем Sprint** больше не называет длину следующего.
  Её нельзя менять, пока Sprint идёт, а две длины в одном блоке путают малую модель. В
  Planning строка — «Next Sprint's length: N days».
- **Отличие: тул в `mode="create"`** не выключает capacity через null: пустое значение
  сохраняется только в update. Выключить capacity — отдельным update.
- **Сценарии сверх плана:** PL-MODE-002 получил строку о review изменения следующего Sprint;
  PL-REPEAT-031 и PL-ASK-026 (capacity в Planning) переформулированы. PS-FIELD-002 теперь
  «nine fields»: прежнее «ten» не совпадало с кодом, полей было 11.
- **Промпт Profile:** у `effort_tracking` ушло «and their capacity warnings».
- **Доки:** пример ответа двери в FEATURE_MODULES.md, строки о длине в SCHEDULES.md и
  HOME_DASHBOARD.md. PLAN_FEATURES.md удалён.

## Workspace Mutator

Обсуждение после Advisor. Промпт — `MUTATOR_PROMPT`, мутационные тулы — `card`, `check`, `value`,
`tag`, `request`, `reminder`, `remove`.

### M1. Нет правила выбора типа — открыто

Различие Goal / Action / Value / Check описано только по структуре («goal is created
root-level»). Единственный намёк на длительность спрятан в описании поля `effort_points`:
«Work that does not fit one day is a Subgoal with Actions under it».

Требование владельца: Mutator должен твёрдо знать, когда создавать что.

Черновик определений:

- **Goal** — результат, на который нужно больше одного дня. Может иметь Deadline. Под ней
  Actions.
- **Subgoal** — Goal под другой Goal.
- **Action** — укладывается в один день. Может повторяться по Schedule.
- **Value** — направление без срока, никогда не бывает Done.
- **Check** — наблюдение «да/нет», без продолжительности: пункт на Card или сам по себе со
  своим Schedule.
- **Tag** — метка для поиска. **Reminder** — сообщение в нужное время, а не работа.

Спорные случаи для решения владельца:

1. «Пить воду 8 раз в день» — Action с Schedule или Check? Сейчас в руководстве onboarding:
   Action повторяется не больше 10 раз в день, чаще — Check. Предлагаю другой критерий: стоит
   сил или времени — Action; проверка «да/нет» — Check.
2. «Напомни завтра в 9 позвонить маме» — Reminder или Action с Schedule плюс 🔔 Remind?
3. Просьба явная, но тип неясен («добавь бег»): Mutator выбирает тип по правилам, а ошибку ловит
   review screen, или переспрашивает? Рекомендую: выбирает по правилам.

### M2. Тул `card`: 23 поля, 7 modes, 7.4K символов — открыто

Ссылку можно задать тремя способами: `value_id`, `value_ids`, `value_query`; так же для Tag
(`tag_id`, `tag_ids`, `tag_query`) и Check (`check_id`, `check_ids`, `check_query`). Родителя —
двумя: `parent_id` и `parent_query`, причём во втором поле принимается SQL. Для малой модели три
способа сказать одно и то же — источник ошибок. Кандидат: один список id на тип ссылки и один
`parent_id`.

### M3. У большинства полей `card` нет description — открыто

Без описания: `id`, `kind`, `title`, `note`, `stage`, `priority`, `blocked`,
`blocked_description`, все поля ссылок с id. Модель угадывает смысл по имени.

### M4. `check`: mode `cancel` означает Missed — открыто

`complete` — Passed, `cancel` — Missed. Имя `cancel` вводит в заблуждение: малая модель прочтёт
его как «отменить» или «удалить». Кандидат: modes `passed` и `missed`.

### M5. Два пути в Done — открыто

Card становится Done через `mode="complete"`, а также через `move` или `update` со
`stage="done"`: подготовка proposal трактует их одинаково. Два пути — лишняя развилка. Кандидат:
убрать `done` из enum поля `stage`, Done только через `complete`.

### M6. Метафоры — открыто

«That ladder is how much they have committed, so never climb it for them.» Кандидат: «New Cards
go to `backlog`. Use `sprint` or `today` only when the user says so.»

### M7. Шаги «How a turn goes» спорят — открыто

Шаг 2: «write your plan as text». Шаг 4: «Write one short sentence naming what you proposed, and
nothing else». Текст рядом с вызовами тулов пользователю не идёт (см. A7), поэтому шаг 2 —
только место для рассуждения модели. Нужно одно правило.

### M8. Дубли — открыто

Описание домена в Mutator повторяет Advisor (Cards, Checks, Inbox, Repeats), плюс своя копия
каталога views. Каждая копия расходится со временем, а малой модели две формулировки одного
правила мешают.

### M9. Reminder против Action со Schedule против Check со Schedule — открыто

Три способа «чтобы что-то случилось в нужное время» без правила выбора. Связано с M1, вопрос 2.

### M10. Отказ стоит лишнего чтения — открыто

Первый ход Mutator обязан быть вызовом тула (AG-ANSWER-014). Строки «не моё» в промпте — Sprint,
после Батча 1 ещё время Profile — значат, что модель сначала вызовет какой-нибудь тул чтения и
только потом ответит словами. Для малой модели есть риск, что вынужденным тулом станет мутация.

## Hook-запросы к Advisor

~12 текстов в `cards/hooks.py`, `planning/hooks.py`, `checks/hooks.py` вида «Ask the user in one
message… If they tell you, route to…». Это тоже промпты: они приходят Advisor как запрос от
системы. После A1 нужно сверить каждый с правилом «только по явной просьбе». **Статус:
открыто.**

## Onboarding

Самый большой промпт: `ONBOARDING_PROMPT` ~18.8K символов, полное руководство пользователя, и
`history_messages=100`. В одном промпте три режима: вопрос-ответ, «Onboarding tip» и остановка.
Кандидаты: резать руководство; отдавать его по разделам через тул чтения раздела; развести режимы.
**Статус: открыто.**

## Heavy analyzer helper

Промпт нормальный: один вопрос, `query_data` до ответа, затем `forward_output` или
`report_failure`. Под ним общий вопрос Q1 о свободном SQL. **Статус: открыто.**

## Sprint

- После A6 Advisor больше не отправляет сюда вопросы о Sprint, а промпт всё ещё говорит «You also
  answer questions about the Sprint» и учит отвечать о датах и днях. Убрать.
- Несёт полный каталог `ai_cards` ради редких вопросов про Actions. После A6 Sprint только
  меняет, и каталог, возможно, не нужен.
- «Move Actions into the Sprint or Today: say the Advisor does that with the workspace» —
  пользователь получит странную фразу о внутреннем устройстве.

**Статус: открыто.**

## Retro

Шесть тулов: `get_retro_number`, `get_retro_data`, `get_aggregate`, `show_charts`, `show_life`,
`open`. Способ выбрать Sprint (numbers, или даты, или ничего) описан в четырёх тулах и ещё раз в
промпте. **Статус: открыто.**

## Diary

Структура хорошая: пять шагов, шкала `feeling_score` с полосами. Поле `pov` — жаргон; кандидат —
`text`. **Статус: открыто.**

## Profile

Короткий, мелочи. **Статус: открыто.**

## Мини-сессии

Один вызов модели, один терминальный тул. Короткие и в основном уже в нужном стиле.

| Промпт | Где |
|---|---|
| `COMPILER_PROMPT`, `DEADLINE_PROMPT` | `schedules/agent.py` |
| `SETUP_PROMPT` | `reminders/agent.py` |
| `OVERVIEW_PROMPT`, `DAYS_PROMPT`, `REVIEW_PROMPT`, `CROSS_PROMPT`, `ANALYSIS_PROMPT` | `retro/analysis.py` |
| `PATTERN_PROMPT` | `memory/agent.py` |
| `KEY_ACTIONS_PROMPT` | `planning/key_actions.py` |
| `MOTIVATION_PROMPT` | `home/motivation.py` |
| `SUMMARY_PROMPT` | `summary/agent.py` |
| `DESCRIBE_PROMPT`, `RELOOK_INSTRUCTIONS` | `media/library.py` |
| `REQUEST_REVIEW_PROMPT`, `AUTOAPPROVAL_PROMPT` | `proposals/hooks.py` |

Замечания:

- **S1. Время разбирается двумя парсерами по-разному.** `COMPILER_PROMPT`: «every morning» —
  period=day, count=1, время не ставится. `SETUP_PROMPT`: «every morning» не определяет
  расписание, вызвать `not_clear_enough`. Одна фраза пользователя даёт разный результат для
  Action и для Reminder. **Открыто.**
- **S2. Объяснения в `AUTOAPPROVAL_PROMPT`.** «A wrong autoapprove changes the user's data behind
  their back; a needless require_review costs them one button press.» Под A4. **Открыто.**

## Общие фрагменты

- `PERSONA` — преамбула каждого сабагента (AD-VOICE-001).
- Каталог views — один текст в четырёх промптах, 2.5–5K символов в каждом.
- Строки `hint` и `next` в результатах тулов: `proposals/api.py`, `ai/sql.py`, `ai/tools.py`
  и тулы фич. Это тоже инструкции модели.

## Общие вопросы

- **Q1. Свободный SQL на 4–12B.** `query_data` требует от модели написать правильный SQLite по
  каталогу views. Для малой модели это самое хрупкое место. Оставить или заменить частично
  фиксированными read-тулами с фильтрами? Самый крупный дизайн-вопрос; решать после проверки на
  целевой модели.
- **Q2. Целевая модель.** От неё зависят формат tool-calling, окно контекста и то, ограничивает ли
  сервер декодирование по схеме (см. A3). Нужно зафиксировать одну-две модели.
- **Q3. Проверка.** Критерий успеха должен быть проверяемым. Предложение: набор из 15–20
  кейсов на выбор маршрута (A1, A6) и выбор типа (M1), прогон на целевой модели до и после
  правки.
- **Q4. Повторяемый дамп промптов.** Сейчас дамп всех промптов и схем делается разовым
  скриптом. Если смотреть контексты после каждой правки, скрипт стоит положить в `scripts/`.

## Журнал

- **2026-10-05.** Инвентарь всех агентов, сабагентов, мини-сессий и их тулов; документ создан.
  Advisor, первый круг: A1, A4, A7 решены, черновики записаны; A2 и A3 отклонены владельцем,
  выводы записаны; A5 снято; A6 расписан по случаям, ждёт решения.
- **2026-10-05.** Владелец ответил: A1 — для всех маршрутов, текст Reminder — просьба
  пользователя; A6 — рекомендации приняты, случай 4 без изменений; A7 — без предела длины.
  Записан план «Батч 1 — Advisor».
- **2026-10-05.** Владелец сократил Батч 1: в промпт Advisor не добавлять строк-исключений по
  маршрутам и строку о Reminder; пересечения снимаются через `purpose` retro и onboarding, строку
  в Mutator и подсказку ошибки `open`. Руководство onboarding не трогать. Длину Sprint — перенести
  из Profile на экран Sprint: записан «Батч 2», ждёт ответов.
- **2026-10-05.** Батч 1 сделан. Промпт Advisor: правило просьбы в Routing, Reminders без
  «propose changes normally», разделы Advice / Your response / Your answer, строки A4. Каталог
  views: три строки A4. `purpose` Sprint, Retro, Onboarding. Mutator: строка о временах Profile.
  Shell: подсказка маршрута при `not_found` у `open` корневой сессии. State Advisor: день, остаток
  и capacity идущего Sprint, даты Sprint, начатого сегодня. Сценарии: новые AD-ASK-007 и
  AG-OPEN-054, PL-ASK-026 переформулирован. Снапшот промптов: Advisor, Heavy analyzer, Mutator,
  Sprint. `SYSTEM_PROMPT` 14237 → 14032 символов. Полный набор: 1949 passed, 4 skipped; Ruff и
  сканер архитектуры чистые. Батч 2: владелец ответил — capacity переезжает тоже, ввод числом,
  словами через `sprint`.
- **2026-10-05.** Батч 1 закоммичен (v9.125). Записан план Батча 2.
- **2026-10-05.** Батч 2 сделан: длина и capacity следующего Sprint — в Workspace, на экране
  Planning и в тул `sprint`; из Profile ушли. Новые сценарии PL-LENGTH-035 и PL-CAPACITY-036;
  PS-SPRINT-LENGTH-003 и PS-CAPACITY-004 выведены. Снапшоты: схема (`user_profile`,
  `workspace`), промпты (Advisor, Onboarding, Profile, Sprint, тулы `profile` и `sprint`).
  PLAN_FEATURES.md удалён по слову владельца. Полный набор: 1947 passed, 4 skipped — на два
  меньше, чем после Батча 1: тесты документов шли и по PLAN_FEATURES.md. Ruff и сканер
  архитектуры чистые.
