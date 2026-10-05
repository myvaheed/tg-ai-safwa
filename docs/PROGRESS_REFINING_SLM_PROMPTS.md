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
Scenario: AG-OPEN-055 — An item Safwa cannot find is left to the subagent that opens its kind
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
- AG-OPEN-055 (`tests/e2e/test_retro_e2e.py`): `open` корневой сессии по несуществующему retro —
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

### M1. Нет правила выбора типа — решено

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

**Решено владельцем 2026-10-05.** Определения — как в черновике.

1. Граница — частота: больше 5 раз в день — Check, не Action. `ACTION_DAILY_EXECUTIONS_MAX`
   становится 5 вместо 10; SCH-LIMIT-015 меняется по слову владельца.
2. «Напомни завтра в 9 позвонить маме» — Reminder.
3. Тип неясен — Mutator выбирает сам, по правилам.

### M2. Тул `card`: 23 поля, 7 modes, 7.4K символов — решено

Ссылку можно задать тремя способами: `value_id`, `value_ids`, value_query; так же для Tag
(`tag_id`, `tag_ids`, tag_query) и Check (`check_id`, `check_ids`, check_query). Родителя —
двумя: `parent_id` и parent_query, причём во втором поле принимается SQL. Для малой модели три
способа сказать одно и то же — источник ошибок. Кандидат: один список id на тип ссылки и один
`parent_id`.

**Обсуждение 2026-10-05.** Владелец: лучше по имени, чем по id, — универсальнее; так же для
родителя. Убрать `done` из `stage` — решено (M5). `blocked`: v9.108 упростил только view
`ai_cards` (`blocked_description` NULL — не заблокирована), тул и колонка Card остались вдвоём;
в туле оставить одно `blocked_description`. Описания полей — только где смысл не виден из имени
(M3). Открыто: ссылки «имя или id» в одном поле; `schedule` и Deadline — одно поле
schedule_or_deadline или два.

**Решено владельцем 2026-10-05.** Тул `card` делится на два: `goal` (Goal и Subgoal) и
`action`. Views не делятся: `ai_cards` остаётся одним.

- `goal`: `mode` (create, update, complete, reopen, link, unlink), `id`, `title`, `note`,
  `priority`, `deadline`, `values`, `tags`, `checks`, `parent`. Поля вида нет: без `parent` —
  Goal, с `parent` — Subgoal.
- `action`: те же `mode` и `move`; `id`, `title`, `note`, `stage` (backlog, sprint, today — без
  `done`), `priority`, `schedule`, `blocked_description` (текст блокирует, null снимает),
  `effort_points`, `tracked_mins`, `categories`, `energy_types`, `values`, `tags`, `checks`,
  `parent`.
- Ссылки и родитель — одно поле на тип, каждый элемент — имя или id. SQL в поле родителя уходит.
  Тул `check` получает то же поле `values`.
- Описание типа из M1 — в описании каждого тула.
- Проверки по виду Card в подготовке proposal, до которых из тулов больше не дойти, удаляются.
- **Blocked — решено владельцем:** у Goal и Subgoal понятия blocked нет вовсе. Blocked бывает
  только у Action и выводится из `blocked_description`: текст — заблокирована, пусто — нет.
  Колонка `blocked` у Card удаляется (снапшот схемы, таблица `cards`); обход предков больше не
  считает blocked родителя; экран Goal не перечисляет заблокированные Actions под ней — каждая
  Action показывает свой ⛔ сама. Сценарии: CD-BLOCKED-019 выводится из обращения;
  CD-BLOCKED-018 и CD-FIELD-007 переформулируются; строка `ai_cards` о пустом описании у
  родителя уходит. Тул, экран и use cases принимают только `blocked_description`. Отдельный
  батч перед батчем Mutator.

### M3. У большинства полей `card` нет description — решено

Без описания: `id`, `kind`, `title`, `note`, `stage`, `priority`, `blocked`,
`blocked_description`, все поля ссылок с id. Модель угадывает смысл по имени.

**Решено владельцем 2026-10-05:** описание пишется только там, где смысл не виден из имени.
Для очевидных полей оно — шум.

### M4. `check`: mode `cancel` означает Missed — решено

`complete` — Passed, `cancel` — Missed. Имя `cancel` вводит в заблуждение: малая модель прочтёт
его как «отменить» или «удалить». Кандидат: modes `passed` и `missed`.

**Решено владельцем 2026-10-05:** modes `passed` и `missed`.

### M5. Два пути в Done — решено

Card становится Done через `mode="complete"`, а также через `move` или `update` со
`stage="done"`: подготовка proposal трактует их одинаково. Два пути — лишняя развилка. Кандидат:
убрать `done` из enum поля `stage`, Done только через `complete`.

**Решено владельцем 2026-10-05:** `done` из `stage` убирается.

### M6. Метафоры — решено

«That ladder is how much they have committed, so never climb it for them.» Кандидат: «New Cards
go to `backlog`. Use `sprint` or `today` only when the user says so.»

**Решено владельцем 2026-10-05:** принято.

### M7. Шаги «How a turn goes» спорят — решено

Шаг 2: «write your plan as text». Шаг 4: «Write one short sentence naming what you proposed, and
nothing else». Текст рядом с вызовами тулов пользователю не идёт (см. A7), поэтому шаг 2 —
только место для рассуждения модели. Нужно одно правило.

**Уточнено 2026-10-05.** Шаги говорят о двух разных ответах, но промпт этого не называет. Шаг 2 —
текст в том же ответе, что мутации; его требует хук `proposals.plan` (`PLAN_HOOK`): ответ с
мутациями без текста возвращается модели. Шаг 4 — ответ после review, он идёт Advisor. Малая
модель читает «write your plan» и «one sentence, nothing else» как правила одного ответа.
Предложено: назвать каждый ответ — «with the mutation tools: one line naming what you will
change»; «after the review: one short sentence naming what you proposed». Так же, как уже
сказано в промптах Sprint и Profile.

**Решено владельцем 2026-10-05:** принято.

### M8. Дубли — решено

Описание домена в Mutator повторяет Advisor (Cards, Checks, Inbox, Repeats), плюс своя копия
каталога views. Каждая копия расходится со временем, а малой модели две формулировки одного
правила мешают.

**Предложено 2026-10-05.** Правила видов Card (Goal без родителя, Subgoal под Goal, что бывает
только у Action, Deadline против Schedule) после разделения `card` переезжают в описания тулов
`goal` и `action` — из промпта Mutator уходят. Определения M1 (Goal, Action, Value, Check) и
метки повторов (` [🔄2, live #7]`, ` [📦]`) — один общий фрагмент, как каталог views: Advisor и
Mutator получают одну формулировку. В Mutator остаётся только то, чего нет ни в схемах, ни во
фрагменте: Backlog по умолчанию (M6), Effort Points вкл/выкл, Inbox, Reminders, порядок хода.

**Решено владельцем 2026-10-05:** принято.

### M9. Reminder против Action со Schedule против Check со Schedule — закрыто

Три способа «чтобы что-то случилось в нужное время» без правила выбора. Связано с M1, вопрос 2.

Закрыто решением M1: «напомни» — Reminder; больше 5 раз в день — Check; остальное — Action.

### M10. Отказ стоит лишнего чтения — решено

Первый ход Mutator обязан быть вызовом тула (AG-ANSWER-014). Строки «не моё» в промпте — Sprint,
после Батча 1 ещё время Profile — значат, что модель сначала вызовет какой-нибудь тул чтения и
только потом ответит словами. Для малой модели есть риск, что вынужденным тулом станет мутация.

**Идея владельца 2026-10-05:** тул nothing_to_do(reason). Первый ход остаётся обязательным
(`tool_choice="required"`), но у модели появляется честный вариант: «не моё» или «уже так».
Вызов завершает сессию, `reason` возвращается Advisor как ответ сабагента. Строки «propose
nothing and say so» в промптах становятся «call nothing_to_do». Тул — в shell, рядом с `route`
и `forward`: его получает каждый сабагент, чей первый ход обязателен. Нужен новый сценарий AG.

## Батч 3 — Blocked только у Action — сделано

Решение M2 (Blocked). Идёт перед батчем Mutator: тул `card` в нём теряет одно поле, а батч 4
его делит. Сделан 2026-10-05 по плану ниже; отличия — в «Как сделано».

**Что.** Колонка `blocked` у Card уходит. Action заблокирована, пока у неё есть
`blocked_description`; пустое описание — не заблокирована. У Goal и Subgoal blocked нет:
обход предков его не считает, экран Goal не перечисляет заблокированные Actions под ней.

**Критерий успеха.** В снапшоте схемы у `cards` нет `blocked`; в `hierarchy.py` нет blocked;
CD-BLOCKED-010, CD-BLOCKED-018, CD-BLOCKED-020, CD-BLOCKED-034, CD-FIELD-007, список Blocked на
экране Sprint и счёт blocked в retro проходят на новом признаке. CD-BLOCKED-019 выведен.

**Модель.** `Card.blocked` становится свойством: есть ли описание. Два SQL-фильтра читают
описание напрямую: хук Blocker follow-up (`hooks.py`) и итог Sprint (`closing.py`).

**Операции** (`cards/use_cases.py`).

- `create_card` и `update_card_fields` принимают только `blocked_description`: текст блокирует,
  пустой снимает. У Goal и Subgoal поле отбрасывается, как effort (CD-FIELD-007).
- `CARD_BLOCKED` пишется, когда описание было пустым и стало непустым. Сейчас — когда blocked
  стал true; для хука это то же событие (CD-BLOCKED-034).
- Проверка «заблокирована без причины» и аргумент `blocked` у `validate_action_fields`
  удаляются: такого состояния больше нет.
- Повтор Action копирует описание, как сейчас.

**Иерархия.** `derived_from_children` и `propagate_ancestors` без blocked; функция перечня
Actions, «из-за которых» родитель заблокирован, удаляется; строка, обнулявшая описание
родителя, тоже.

**View `ai_cards`.** Колонка `blocked_description` — описание или NULL, если оно пустое.
Строка каталога «on a blocked goal or subgoal, `blocked_description` is an empty string; read
reasons from its actions» уходит.

**Тул `card`** (до батча 4). Поле `blocked` уходит. Описание `blocked_description`: «What blocks
the Action, in the user's words. On update, null unblocks it.» Null и пустая строка снимают.
Проверки «a blocked Card needs blocked_description» уходят; автоаппрув update и подсказка
repair — без `blocked`.

**Экраны.**

- Card: «🚧 Blocked» у незаблокированной Action спрашивает причину, как сейчас; у
  заблокированной — снимает причину. «📝 Blocked reason» правит её, как сейчас.
- Создание Card: тот же приём. «🚧 Blocked» сразу открывает ввод причины, повторное нажатие её
  снимает. Флаг `blocked` в черновике и строка «Required» уходят.
- Goal и Subgoal: блок с перечнем заблокированных Actions уходит.
- Review: строка «Blocked» уходит, остаётся строка с причиной.

**Промпты.**

- Advisor: «A Goal and a Subgoal show what the Cards under them add up to.» → «A Goal and a
  Subgoal show the stage, effort and time of the Cards under them.»
- Mutator: «Only an Action carries a live stage, effort, time and blocked. A Goal and a Subgoal
  derive these from their children.» → «Only an Action carries a stage, effort, time and blocked.
  A Goal and a Subgoal show the stage, effort and time of the Cards under them.» Батч 4 этот
  раздел всё равно переписывает.

**Сценарии.** CD-BLOCKED-019 выводится из обращения (решение владельца). Переформулировки:

```gherkin
Scenario: CD-BLOCKED-010 — A blocked Action has to say why, and unblocking takes the reason with it
  Given an Action is being marked blocked on its screen
  When no reason is written
  Then it is refused, and the refusal says a blocked Action needs a reason
  And an Action is blocked exactly while it has a reason: a proposal with an empty reason unblocks it
  When the same Action is later unblocked
  Then the reason goes with it, and the Action no longer shows a warning

Scenario: CD-BLOCKED-018 — Only an Action can be blocked
  Given a Goal, a Subgoal and an Action
  When the Action is marked blocked, with a reason
  Then it is blocked, and the reason is the words that were given
  When anything tries to mark the Goal or the Subgoal blocked
  Then it is refused before anything is written, in a proposal and on a screen alike
  And no screen offers a Goal or a Subgoal a Blocked control
  And a Goal or a Subgoal never reads as blocked, whatever is under it: each blocked Action shows its own warning
```

CD-FIELD-007: «Blocked» → «a blocked reason», исходы те же.

**Тесты.** Около 90 мест в 16 файлах ставят или читают `blocked`: переводятся на описание.
Тесты CD-BLOCKED-019 удаляются. Снапшоты: схема (`cards`), промпты (Advisor, Mutator, тул
`card`, каталог views).

**Доки.** DOMAIN.md: blocked в обходе предков и пустая строка у Goal.

**Допущение.** Экран Goal не перечисляет заблокированные Actions: каждая Action показывает
свой ⛔ сама.

### Как сделано

- **Модель и операции** — как в плане. Отличие: `edit_card_text` больше не правит причину.
  Причину с экрана пишет `update_card_fields`, поэтому блокировка с экрана тоже даёт
  `CARD_BLOCKED`.
- **Экраны.** Экран Card: строки «Blocked» и «Blocked description» — только у Action; у Goal их
  нет. В черновике «🚧 Blocked» ведёт на ввод причины, а при заданной причине — на новый
  callback `card_create_unblock`; прежний переключатель черновика удалён.
- **Review.** Квитанция создания пишет «Blocked», когда в proposal есть причина.
- **Тесты.** Проверки CD-BLOCKED-019, что ещё имеют смысл, перешли в тесты CD-BLOCKED-018:
  Goal и Subgoal не читаются как blocked ни в базе, ни в `ai_cards`, ни на экране; Action
  показывает своё. Новый UI-тест CD-BLOCKED-010: черновик спрашивает причину, пустую
  отказывает, повторное нажатие её снимает. Тест экрана Card дополнен снятием блокировки той же
  кнопкой.

## Батч 4 — Workspace Mutator: M1–M10 — сделано

Все решения M1–M10 одним батчем, после батча 3. Сделан 2026-10-05 по плану ниже; отличия — в
«Как сделано».

**Критерий успеха.** У Mutator нет тула `card`, есть `goal` и `action`. Ни в одном туле нет
полей ссылок с окончаниями _id, _ids, _query. У `check` modes `passed` и `missed`. Ни в одном
промпте нет «propose nothing and say so». Промпт Mutator короче на разделы Cards, Checks и
Repeats. Новый сценарий AG проходит. Полный набор, Ruff и сканер чистые.

### Тулы `goal` и `action` (M2, M3, M5)

Оба пишут изменения сущности `card`: один обработчик `CardProposalHandler`, один экран review,
views без изменений.

- `goal`: mode create, update, complete, reopen, link, unlink; поля `id`, `title`, `note`,
  `priority`, `deadline`, `values`, `tags`, `checks`, `parent`.
- `action`: те же modes и move; поля `id`, `title`, `note`, `stage` (backlog, sprint, today),
  `priority`, `schedule`, `blocked_description`, `effort_points`, `tracked_mins`, `categories`,
  `energy_types`, `values`, `tags`, `checks`, `parent`.

Описания — только где смысл не виден из имени:

```text
goal: Propose one Goal or Subgoal: a result that takes more than one day. With a Goal as `parent` it is a Subgoal.
  mode: complete only when the user asks and all its Actions are Done. link and unlink take `values`, `tags` or `checks`, one per call. Deleting is the remove tool.
  deadline: When it must be done, in the user's words: 'by 20 October'. Never invent a date. On update, null removes it.
  parent: A Goal, by exact title or id.

action: Propose one Action: work that fits in one day.
  mode: move changes only `stage`. complete finishes it, with `tracked_mins` when the user said how long it took; its Goal stays open. reopen brings it back to `stage`, Backlog when omitted, and reopens its closed Goal and Subgoal. link and unlink take `values`, `tags` or `checks`, one per call. Deleting is the remove tool.
  schedule: When it repeats or happens, in the user's words: 'once a week', 'three times a day', 'Tuesday at 15:00', 'after each completion'. Never invent a time. On update, null removes it.
  blocked_description: What blocks it, in the user's words. On update, null unblocks it.
  effort_points, tracked_mins: как сейчас, плюс: For a finished repeat ` [🔄2, live #7]`, send it with that instance's id.
  categories, energy_types: как сейчас.
  parent: A Goal or a Subgoal, by exact title or id. On update, null makes it root-level.

goal и action:
  values: Each an exact Value name or an id.
  tags: Each an exact Tag name or an id.
  checks: Each an exact Check title or an id. A Check with its own Schedule cannot be linked. A Check on another Card: unlink it there first.
```

**Как вызов становится изменением.**

- `goal` с mode create: с `parent` — Subgoal, без — Goal. `deadline` пишется в Schedule карточки:
  у Goal Schedule и есть Deadline, как сейчас.
- Каждый вызов несёт вид, к которому обращается тул. Подготовка отказывает вызову на Card
  другого вида до review: статус wrong_tool, «Card #5 is an Action.», hint «Call action with
  this id.»
- Регистрация: `action` — тул `ProposalContribution` Cards, `goal` — в `mutation_tools` модуля
  Cards. Комментарий поля в `FeatureModule` расширяется: второй тул той же сущности.

**Что удаляется из подготовки proposal.** До этих веток из тулов больше не дойти:

- отказ «A Goal is created root-level» — Goal с родителем теперь Subgoal;
- отказ для нового Subgoal без родителя; снятие родителя у Subgoal по-прежнему отказ;
- отказ stage_is_action_only, отбрасывание полей Action у Goal, «Only an Action carries time spent»:
  у `goal` таких полей нет, а `action` на Goal — wrong_tool;
- пути в Done через `stage`: проверка Pending Checks остаётся только у complete, смена stage
  идёт прямо в `move_card`;
- SQL в родителе (см. ниже).

### Ссылки «имя или id» (M2)

Shell, `ReferenceSpec`: одно поле на тип — `values`, `tags`, `checks`. Элемент — число (id) или
строка (точное имя; у Check — title), без учёта регистра; один элемент или список. Поля с
окончаниями _id, _ids, _query уходят. Отказы — как сейчас: не найдено, неоднозначно (hint: найти через
`query_data` и повторить с id). `parent` — один элемент: id или точный title среди
неархивных Cards. SQL в родителе уходит; `normalize_card_query` остаётся для Requests. Подписи
review (`render.py`) читают новое поле. Подсказка Inbox в Tags: «Use action mode="unlink" with
the Card id and tags=["Inbox"]».

### `check` (M2, M4)

- Modes `passed` и `missed` вместо complete и cancel. Тул переводит их в те же изменения.
- `values` вместо трёх полей. Create, как сейчас, — без Values.
- Описания:

```text
check: Propose one Check: a yes/no observation with no duration. Put it on a Card with `checks` of the goal or action tool.
  mode: passed and missed answer it, only when the user said how it went. link and unlink take `values`. Deleting is the remove tool.
  schedule: When it is asked on its own, in the user's words: 'every evening'. On update, null removes it. A Check with a Schedule stays off Cards.
  values: Values it shows how well the user holds, each an exact Value name or an id. They are its own, not its Cards'.
```

- Hint о Pending Checks: «check(mode='passed'|'missed', id=…)».

### nothing_to_do (M10)

- Shell: схема тула рядом с `route` и `forward`; runtime отвечает на него сам, как на
  `forward`: только один вызов в ответе, вызов завершает сессию, `reason` — ответ сессии.
  `route_receipt`: outcome done, `did` пуст, `text` — reason.
- Получает каждый сабагент, чей первый ход обязателен: Mutator, Diary, bookkeeper из примера
  wallet. Сабагент, который отвечает на вопросы, его не получает.
- Описание: «End the request without changes. Call it alone: the request is not yours, or
  nothing needs to change.» Поле `reason`: «Why, in one short sentence in the user's language.»
- Diary: «If your sources do not make the day writable, say in one sentence what is missing
  instead.» → «If your sources do not make the day writable, call nothing_to_do with what is
  missing.» Wallet: «say which one is missing and propose nothing» → «call nothing_to_do with
  which one is missing».

### Общий фрагмент (M1, M8)

Один текст для Advisor и Mutator. Подставляется в `{items}` при сборке, как `{views}`:
в `SYSTEM_PROMPT` и в `routed_prompt`. Живёт рядом с `PERSONA`. Число — из
`ACTION_DAILY_EXECUTIONS_MAX`.

```text
# Safwa items
- Goal: a result that takes more than one day. It may have a Deadline.
- Subgoal: a Goal under a Goal.
- Action: work that fits in one day. It may repeat on a Schedule, at most 5 times a day.
- Goals, Subgoals and Actions are Cards.
- Check: a yes/no observation with no duration, on one Card or on none. Anything more than 5 times a day is a Check.
- Value: a direction with no deadline. It is never Done.
- Tag: a free label for finding things.
- Request: a saved Card query the user reruns.
- Reminder: a message at a set time, not work.
- A title ending in ` [🔄2, live #7]` is a finished repeat; #7 is the open one.
- ` [🔄2]` with no id: the series has ended. ` [📦]`: archived.
```

**Advisor.** Фрагмент встаёт на место первой строки «Agile structure» (виды Card) и строки
«💎 Values … 💬 Requests». Раздел Checks: определение уходит во фрагмент, остаются примеры,
правило о Card complete и цитата. Строки о метках после `{views}` → «For a finished repeat,
cite and read the open one, unless the user asks about that past instance.» и «An archived
item still counts, and it cannot be changed automatically.»

### Промпт Mutator (M6, M7, M8, M10)

Имена тулов — в обратных кавычках, как в нынешнем промпте:

```text
You keep the user's workspace: their Cards, Checks, Values, Tags, Requests and Reminders.

{items}

# How a turn goes
1. Read what you need with `query_data`. Never in the same response as a mutation tool.
2. With the mutation tools, in the same response: one line of text naming what you will change.
3. After the review: one short sentence naming what you proposed. The interface prints the Saved/Discarded/Failed receipt itself.
Nothing to change, or the request is not yours: call nothing_to_do with the reason.

# Proposing
- Propose only what was asked. When the choice is the user's, cite the item instead of guessing it.
- New Cards go to `backlog`. Use `sprint` or `today` only when the user says so.
- Effort Points on in the workspace state: give every new Action `effort_points`.
- Effort Points off: never send `effort_points`.
- Judge every change against the Sprint's Success criteria and the active Values in your context.
- Fill in what you are sure of; omit the rest. Never invent an id such as 0 or 1.
- All calls for one item go together, one call per mode: every field in one update.
- Starting or finishing a Sprint and its Success criteria are not yours.

# Inbox
An Action can hold a note, captured idea or draft. Use Tag "Inbox" to capture these without extra detail.

# Reminders
- Pass the user's own words through in `when`. Never invent a date or an hour.
- The Morning, Diary and daily summary times are Profile fields, not Reminders.

# Read the data
(как сейчас, с `{views}`)
```

Уходит: «What the workspace is for» с лестницей; Cards и Checks — в описания тулов и во
фрагмент; Repeats — во фрагмент, в результаты тулов (подсказка открытого экземпляра уже там) и в
описания `effort_points` и `tracked_mins`; строки об удалении и архиве — в описании `remove`.

**Решено владельцем 2026-10-05:** строки о закрытом экземпляре в промпте нет. Вызов на него
стоит лишнего хода — отказ с подсказкой, — зато промпт меньше.

### Лимит повторов (M1)

`ACTION_DAILY_EXECUTIONS_MAX` = 5. SCH-LIMIT-015 по слову владельца: «five times a day»; 6 в
день, 36 в неделю и интервал меньше 288 минут спрашиваются. Строка руководства onboarding
берёт константу. Пример в Schedule Action: «three times a day» вместо «five times a day».

### Сценарии

- CD-TREE-002 по решению M2: «When a Goal is proposed with a parent / Then it is refused…» →
  «Then it is created as a Subgoal under that parent». Остальное без изменений.
- CD-TREE-003: «And a Subgoal written with no parent is refused the same way, and so is taking
  its parent away» → «And taking a Subgoal's parent away is refused the same way».
- CD-FIELD-007: Goal пишется тулом `goal`, у которого нет этих полей; вызов `action` на Goal
  отказывается до review. Ручной черновик, переключённый с Action на Goal, отбрасывает их, как
  сейчас.
- SCH-LIMIT-015 — выше.
- Новый:

```gherkin
Scenario: AG-NOTHING-056 — A subagent with nothing to do says so in its first step
  Given a subagent whose first step has to be a tool call (AG-ANSWER-014)
  When the request is not its own, or what it asks is already so
  Then its first step can be nothing_to_do with the reason, alone in its response
  And that ends its session: nothing is proposed, and the reason goes back as the subagent's words
  And a subagent that answers questions is not offered it
```

### Тесты, снапшоты, доки

- Около 200 вызовов `card` в 38 файлах тестов переходят на `goal` и `action`; ссылки — на новые
  поля; родитель — по title или id. Тесты SQL в родителе удаляются: родителя по title и id
  покрывают CD-TREE-003, CD-TREE-004 и CD-TREE-006.
- Новые проверки: wrong_tool; `goal` с родителем — Subgoal; ссылки смесью имён и id;
  `passed` и `missed`; AG-NOTHING-056 (ответ маршрута, один вызов в ответе, нет у сабагента с
  вопросами).
- Снапшот промптов: Advisor, Mutator, Diary, тулы `goal`, `action`, `check`. Схема без изменений.
- Доки: AGENT_ARCH.md (тулы runtime), DOMAIN.md (поле тула `card`).

**Допущения.**

- Вид проверяется по тому, что тул кладёт в изменение; у изменения нет имени тула.
- `goal` регистрируется через `mutation_tools`, а не новым полем `ProposalContribution`.
- Строка «Judge every change…» остаётся: на ней держится WS-JUDGE-002.

### Как сделано

- **Вид в изменении.** Оба тула кладут в изменение `kind`: `action` — action; `goal` — subgoal
  при create с `parent`, иначе goal. У существующей Card подготовка снимает `kind` и сверяет:
  Action только через `action`, Goal и Subgoal только через `goal`. Отказ wrong_tool идёт
  первым, до отказа по закрытому повтору. Изменение, собранное в коде без тула, `kind` не несёт
  и не проверяется.
- **Родитель.** `parent` — id или точный title; подготовка превращает его в `parent_id`, как
  раньше. У `goal` null тоже значимый: снятие родителя у Subgoal даёт отказ parent_required,
  как обещает CD-TREE-003.
- **Ссылки.** У `ReferenceSpec` одно свойство `field` (values, tags, checks) вместо трёх
  ключей; `value_id` остаётся именем колонки связи. Тип элемента — `Reference` в контрактах
  shell: целое — id, строка — имя. Поле принимает один элемент или список.
- **Нормализатор аргументов.** Правила для полей с окончанием _ids удалены: таких полей больше нет ни в
  одном туле. `[0]` в ссылках теперь ошибка проверки, а не молча выброшенный элемент.
- **Сообщения тулов.** Общая проверка modes у `goal` и `action` — одна функция. Create с
  `tracked_mins` по-прежнему говорит «a new Action has no time spent yet».
- **nothing_to_do.** Схема — `NOTHING_TO_DO_TOOL` в контрактах shell, ответ — в цикле runtime
  рядом с `forward`. Пустой `reason` — ошибка с просьбой повторить. Снапшот промптов получил
  схему тула и отдельный хеш `ITEMS`.
- **Advisor.** `{items}` стоит перед разделом «Agile structure»; заголовок раздела потерял
  «Safwa-items», чтобы не повторять заголовок фрагмента.
- **Тесты.** Удалены два e2e-теста SQL в родителе. Тест «Goal с родителем — отказ» стал e2e
  CD-TREE-002: Goal с родителем по title — proposal Subgoal. Новые проверки: wrong_tool
  (CD-STAGE-013, CD-BLOCKED-018, CD-FIELD-007), отказ parent_required (CD-TREE-003), тулы
  `goal` и `action` (`test_ai_sql.py`), одна связь смесью id и имён (`test_cards.py`), два e2e
  AG-NOTHING-056.
- **Размеры.** Промпт Mutator без каталога views и PERSONA: 4986 → 2496 символов с фрагментом.
  Тул `card` (7.4K) → `goal` 2.2K и `action` 4.3K.

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
  AG-OPEN-055, PL-ASK-026 переформулирован. Снапшот промптов: Advisor, Heavy analyzer, Mutator,
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
- **2026-10-05.** Батч 2 закоммичен (v9.126) и влит в main вместе с v9.124. Сценарий Батча 1
  стал AG-OPEN-055: номер 054 в main уже занял AG-HOOK-054.
- **2026-10-05.** Workspace Mutator: владелец решил M1–M8 и M10, M9 закрыт решением M1. Blocked
  остаётся только у Action. Записаны планы: «Батч 3 — Blocked только у Action» и «Батч 4 —
  Workspace Mutator: M1–M10». Батч 3 идёт первым. Владелец: строки о закрытом экземпляре в
  промпте Mutator нет — лишний ход дешевле длинного промпта.
- **2026-10-05.** Батч 3 сделан: колонки `blocked` у Card нет, Action заблокирована, пока есть
  причина; у Goal и Subgoal blocked нет. CD-BLOCKED-019 выведен; CD-BLOCKED-010, CD-BLOCKED-018 и
  CD-FIELD-007 переформулированы. Снапшоты: схема (`cards`), промпты (Advisor, Mutator, тул
  `card`, каталог views). Полный набор: 1952 passed, 4 skipped; Ruff и сканер архитектуры
  чистые.
- **2026-10-05.** Батч 3 закоммичен (v9.127). Батч 4 сделан: тул `card` разделён на `goal` и
  `action`, ссылки и родитель — по имени или id, у `check` modes `passed` и `missed`, тул
  `nothing_to_do` у сабагентов с обязательным первым ходом, общий фрагмент `{items}` у Advisor и
  Mutator, новый промпт Mutator, `ACTION_DAILY_EXECUTIONS_MAX` = 5. Сценарии: новый
  AG-NOTHING-056; CD-TREE-002, CD-TREE-003, CD-FIELD-007 и SCH-LIMIT-015 переформулированы.
  Снапшот промптов: Advisor, Mutator, Diary, Onboarding (лимит повторов), тулы `goal`,
  `action`, `check`, `nothing_to_do`, фрагмент `ITEMS`. Полный набор: 1952 passed, 1 failed,
  4 skipped; упавший тест CD-TIME-039 ждал старый отказ вместо wrong_tool, исправлен и прошёл
  отдельно. Ruff и сканер архитектуры чистые.
