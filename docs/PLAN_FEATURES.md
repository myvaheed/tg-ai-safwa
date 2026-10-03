# План: Sprint, Retro и Profile — в словах и на экране

Начат 2026-09-28. Здесь записано, что делается в следующих батчах, в каком порядке, и где работа
стоит сейчас. Батч закончен, когда выполнена его проверка; тогда его строка в таблице и журнал
внизу меняются в том же коммите.

## Зачем

Сейчас Sprint, Retro и Profile меняются только кнопками, а о прошедших Sprint модель не может
прочитать ничего, кроме memory. Цель:

1. `route("sprint")` — сабагент, который начинает и заканчивает Sprint и пишет Success criteria
   следующего, а на вопросы о длине, датах и днях, которые остались, отвечает сам.
2. Экран списка Retro — все закончившиеся Sprint, из списка открывается экран retro каждого.
3. `route("retro")` — сабагент, который по номеру или дате находит закончившийся Sprint,
   открывает его экран retro и отвечает на вопросы по записям нескольких Sprint, с суммами и
   средними, которые считает код.
4. `route("profile")` — сабагент, который меняет любое поле Profile, кроме переключателей
   автоматических реакций, и отвечает про их текущие значения.

## Прогресс

| Батч | Что | Статус |
|---|---|---|
| 1 | Экран списка Retro | ✅ сделан |
| 2 | `route("sprint")` и блок текущих значений сабагента | ✅ сделан |
| 3 | `route("retro")` и `open` у сабагента | ✅ сделан |
| 4 | `route("profile")` | ✅ сделан |

Владелец попросил сделать все четыре одним батчем, и они сделаны одним батчем. Разделы ниже
остались по батчам, чтобы было видно, что к какому сценарию относится.

## Решения для всех батчей

**Каждый сабагент живёт в пакете своей фичи.** Sprint — в `planning`, Retro — в `retro`,
Profile — в `profile`. Один `.feature` на пакет ([tests/brd/README.md](../tests/brd/README.md)),
поэтому новых пакетов нет. Имя route — то, которое назвал владелец: sprint, retro, profile.

**Текущие значения идут в блок сразу после диалога, а не в текст системного промпта.** Для
модели это то же самое, что промпт: она читает этот блок на каждом шаге, и он читается заново
(AG-SESSION-041). В тексте промпта значения ломают правило «The prompt prefix is byte-stable»
и снапшот `tests/snapshots/prompt_prefix.json`: одно новое значение обнуляло бы кэш на каждом
ходе. Раньше на этом месте стояла строка часов Diary — синхронная и без базы. Теперь это одно
поле `AgentSpec.current`: асинхронное чтение, которое фича пишет сама, с базой через
`AgentContext`, и `ContextBuilder.routed` добавляет его последним. Часы Diary — такой же блок, по
смыслу они не изменились. Второго механизма рядом с этим нет.

**Все три сабагента отвечают сами.** Каждый объявлен `shown_as_is`, как онбординг: слова
сабагента доходят до владельца как есть, внутри сообщения Advisor (AG-RECEIPT-044), и Advisor
их не пересказывает.

**Изменение — всегда proposal.** На экране ревью только Save и Discard, а Save вызывает те же
операции, что и кнопки: `set_sprint_success_criteria`, `start_sprint`, `finish_sprint`,
`set_profile_field`. Автоодобрения у Sprint и Profile нет: каждое такое изменение проходит
через экран.

**Что по-прежнему не делается словами:**

- переключатели автоматических реакций в Profile. Исключение — онбординг, его уже выключает
  `stop_onboarding`;
- отметка Met / Not met (RT-CRIT-004: её не ставит ни одна модель);
- запуск анализа retro — по кнопке;
- запись memory;
- даты Sprint, пауза, продление, возврат закончившегося Sprint (PL-END-012) — ни словами, ни
  кнопками.

**Каждый батч в своих пределах:**

- сначала сценарии — черновики ниже, формулировку утверждает владелец до кода;
- потом код и тесты, которые их цитируют;
- manual онбординга ([onboarding/agent.py](../src/safwa/features/onboarding/agent.py));
- строки промптов Advisor и workspace mutator, которые сейчас говорят «нет инструмента»;
- снапшот промпта, если он изменился: обновляется своим тестом
  (`test_rule_i_prompt_prefix_is_byte_stable`);
- снапшот схемы, если изменилась колонка: с названием таблицы в описании батча.

Проверка каждого батча — `uv run pytest -q`, `uv run pytest tests\e2e -q`,
`uv run pytest tests\shell -q`, `uv run ruff check .` и
`uv run python scripts/architecture_metrics.py` с нулём нарушений.

## Решено владельцем

Ответы от 2026-09-28.

1. **Sprint запоминает capacity на старте.** Capacity — настройка Profile, и её могут поменять
   после конца Sprint. Поэтому `start_sprint` записывает в строку Sprint capacity из Profile на
   момент старта: колонка в `sprints`, батч 3. Retro-данные отдают её рядом с взятыми и
   сделанными EP, и вопрос «сколько было капасити в среднем за последние 3 Sprint» получает
   ответ.
2. **Старт словами — ровно как кнопкой.** Sprint начинается сегодня и идёт столько дней,
   сколько задано в Profile (PL-START-005). Другой длины на один Sprint и старта с завтрашнего
   дня нет, хотя параметры для этого у `start_sprint` есть. Proposal старта показывает Success
   criteria, даты начала и конца и длину в днях. Изменить длину — это Profile, и Advisor может в
   одном запросе сначала передать ход profile, потом sprint (AG-ROUTE-003).
3. **Success criteria идущего Sprint не меняются.** У кнопок их нет: после старта они
   зафиксированы. Просьба поменять их, пока Sprint идёт, получает ответ с ошибкой, и proposal не
   появляется. Писать можно только criteria следующего Sprint, в Planning.

## Батч 1 — экран списка Retro

**Что владелец получает.** Кнопка меню «📊 Retro» и команда /retro. Экран — список закончившихся
Sprint, от нового к старому, по RETRO_LIST_PAGE_SIZE = 10 на страницу (SC-PAGE-007). В кнопке
каждого — номер, даты, отметка Met (✅, ❌ или «—») и 🔎, если Sprint проанализирован. Нажатие
открывает существующий экран retro (`open_retro`) на месте списка, с кнопкой назад на ту же
страницу. Экран, открытый по ссылке, остаётся как сейчас. Идущий Sprint в список не входит.
Пустой список говорит, что ни один Sprint ещё не закончился и retro появится после первого.

**Где.** Пакет `retro`:

- `ScreenCommand` с nav retro, command retro и названием «📊 Retro»;
- страница списка и кнопка назад в `RETRO_CALLBACK_ACTIONS`;
- новое название — в порядке меню, который держит Home (HM-MENU-001).

Сущностей и proposals нет, схема не меняется.

**Сценарий (черновик).**

```gherkin
  Scenario: RT-LIST-012 — Every Sprint that ended is one tap from the menu
    Given three Sprints have ended and one is running
    When the owner opens Retro from the menu or with /retro
    Then the three are listed newest first, ten to a page (RETRO_LIST_PAGE_SIZE = 10)
    And each shows its number, its dates, its Success criteria mark and whether it was analysed
    And the running Sprint is not listed
    When the owner taps one
    Then its retro screen replaces the list, with a way back to the same page
    When no Sprint has ended yet
    Then Retro says so, and that a retro is written when a Sprint ends
```

**Проверка.** RT-LIST-012 проходит. В меню есть кнопка, `/retro` опубликована, manual называет и
то, и другое. Ни один сценарий, который уже прошёл, не изменился.

## Батч 2 — `route("sprint")`

**Механизм оболочки.** Строка часов у `AgentSpec` становится асинхронным блоком текущих значений
(см. «Решения»). Diary переходит на блок без изменения текста. Пример на оболочке в
[examples/wallet](../examples/wallet/app.py) собирается без Safwa.

**Сабагент sprint** (planning, рядом с `ONBOARDING_AGENT` по форме):

- **Назначение для Advisor:** начать или закончить Sprint, написать Success criteria следующего,
  ответить на вопрос об идущем Sprint — его датах, длине и днях, которые остались. Перенос Action
  в Sprint или Today остаётся за workspace mutator (инструмент `card`).
- **Views:** `ai_current_sprint`, `ai_current_sprint_metrics`, `ai_cards`.
- **Инструмент** `sprint`. Его `mode` называется как у всех инструментов, по `ChangeAction`:
  - update — Success criteria следующего Sprint, текстом;
  - create — старт; может нести Success criteria, тогда один Save сначала пишет их, потом
    начинает Sprint;
  - complete — конец идущего Sprint.

  Criteria и старт в одном вызове — одно изменение и один экран. Экран старта показывает
  criteria из вызова, а если их нет — черновик из workspace.
- **Prepare** проверяет состояние сейчас и возвращает `ToolPreparationError` с причиной, которую
  модель передаёт владельцу. Причины — `criteria_refusal` и `start_refusal` в
  [planning/api.py](../src/safwa/features/planning/api.py): их же спрашивают
  `set_sprint_success_criteria` и `start_sprint`, так что у кнопки и у Save одни отказы:
  - criteria — только в Planning и не пустые. Пока Sprint идёт, ответ — ошибка: его Success
    criteria зафиксированы на старте; proposal не появляется;
  - start — только из Planning, нужны criteria и хотя бы одна Action в Sprint или Today;
  - finish — только когда Sprint идёт.
- **Apply** вызывает `set_sprint_success_criteria`, `start_sprint` с criteria из workspace (как
  `_on_start`) и `finish_sprint` с той же причиной, что и кнопка.
- **Экран ревью:**
  - criteria — было и стало;
  - start — Success criteria (новые, если criteria идут в том же proposal), даты начала и конца,
    длина в днях, число Action и EP против capacity;
  - finish — номер, какой идёт день из скольких, сколько Action остаются открытыми и сохраняют
    свой stage.
- **Блок текущих значений:**
  - режим;
  - в Planning — черновик criteria, план (число Action и EP), capacity, длина из Profile, даты,
    если начать сегодня, и можно ли начать, а если нельзя — почему;
  - в Sprint — номер, даты, какой идёт день из скольких, последний ли это день, criteria,
    committed, added, removed и done EP, capacity, длина следующего Sprint;
  - сегодняшняя дата и время.

**Что меняется рядом:**

- в промпте Advisor строка «You have no tool for changing Sprint configs… through Profile»
  ([advisor/agent.py](../src/safwa/features/advisor/agent.py)) — ошибочная уже сегодня:
  start, finish и criteria находятся на экране Sprint, а не в Profile. Она заменяется маршрутом;
- в промпте workspace mutator строка «Starting a Sprint and its Success criteria are manual
  screens» говорит, что это не его работа;
- в manual раздел Sprint перестаёт говорить «Not in words».

**Сценарии (черновик).** PL-MODE-002 переписывается по смыслу: сейчас он говорит, что Safwa
Sprint не меняет и proposal о Sprint не бывает. Это решение владельца, оно принято этим планом.

```gherkin
  Scenario: PL-MODE-002 — The Sprint is run on its screen or in words, through the same operations
    Given the owner is talking to Safwa
    When they ask it to start the Sprint, finish it, or write the next Sprint's Success criteria
    Then Safwa shows that change on a review screen with Save and Discard
    And a Sprint about to start is shown with its Success criteria, its first and last day and
      its length in days, which is the Profile's Sprint length (PL-START-005)
    And Save does what the Sprint screen's button does, with the same refusals
    When a Sprint is running and they ask to change its Success criteria
    Then it is refused with that reason, and nothing is proposed
    When they ask it to change a Sprint's dates, pause it, extend it or bring a finished one back
    Then Safwa says there is no way to, in words or on a screen (PL-END-012)
    And the length and the capacity are the Profile's

  Scenario: PL-ASK-026 — A question about the Sprint is answered from the Sprint as it stands
    Given a Sprint is running on its 5th day of 14
    When the owner asks how long the Sprint is or how many days are left
    Then Safwa answers from the Sprint's own dates, read at that moment
    And in Planning it answers with the length a Sprint started today would have, and its dates
```

Для оболочки — сценарий о блоке:

```gherkin
  Scenario: AG-SESSION-051 — A subagent's own current values follow the conversation, read at every step
    Given a subagent declares a block of its own current values
    Then that block comes after the conversation, never inside the part of the prompt that stays the same
    And it is read again at every step, so a value saved in the same request is seen
```

**Проверка.** Словами Sprint начинается и заканчивается, criteria пишутся, и после Save получаются
те же строки `sprints` и `sprint_commitments`, те же хуки и те же отказы, что после кнопки
(тест на обоих путях). Снапшот промпта обновлён своим тестом, а его префикс не зависит от
значений Sprint.

## Батч 3 — `route("retro")`

**Механизм оболочки.** `open` становится доступен сабагенту, который объявил, какие виды экранов
может открывать. У retro это только retro, как views ограничивают `query_data`. Экран, открытый
сабагентом, поднимается в итог корневого хода и приходит после сообщения Advisor, как сейчас
приходит `host_state` open_item корня. Сабагент, который ничего не объявил, `open` не получает.

**Сабагент retro:**

- **Назначение для Advisor:** вопрос о закончившихся Sprint — их номерах, датах, результатах,
  средних — или показать retro одного из них. Идущий Sprint — это sprint.
- **Views:** нет, `query_data` не выдаётся. Сабагент читает только своими инструментами.
- **get_retro_number(date)** — дата в формате YYYY-MM-DD. Возвращает номер и id Sprint, в дни
  которого эта дата входит. Дни считаются от `planned_start_date` до дня, когда Sprint
  закончился, или до `planned_end_date`, смотря что раньше, как дни в RT-STATS-003. Если в этот
  день шёл нынешний Sprint, инструмент говорит, что retro у него ещё нет. Если в этот день был
  Planning, инструмент говорит, что Sprint в тот день не шёл.
- **Выбор Sprint** у get_retro_data и get_aggregate один (RT-ASK-017): `numbers`, или
  `start_date` и `end_date` в формате YYYY-MM-DD, или ничего — тогда все закончившиеся Sprint.
  Номера в формате `yy.MM-xx`, как их пишут экраны (`SPRINT_NUMBER_WIDTH`), например 26.09-01.
  Две даты выбирают каждый Sprint, у которого есть день между ними, целиком — как одна дата
  находит свой Sprint. Номера вместе с датами, одна дата без другой, нечитаемая дата или первая
  дата позже последней — отказ с готовым вызовом, собранным из присланного: только номера,
  сегодня как недостающая последняя дата, первый день первого закончившегося Sprint как
  недостающая первая, формат даты, даты местами наоборот. Даты, в которые ни один Sprint не шёл,
  не ошибка: ответ говорит об этом и называет, когда шли закончившиеся Sprint.
- **get_retro_data** — не больше RETRO_DATA_MAX = 6 Sprint за вызов; если выбрано больше, отказ
  перечисляет их номера и отсылает к get_aggregate. Возвращает соответствие «номер → данные
  retro». Для каждого Sprint:
  - даты, причина конца, Success criteria и отметка Met;
  - EP: взято, сделано, доля, начальный план, добавлено, убрано;
  - Action: всего, закончено, осталось, заблокировано;
  - ключевые Action;
  - время, если оно отслеживалось;
  - capacity на старте, или «выкл», если она была выключена;
  - заголовок анализа и эксперимент, если Sprint проанализирован.

  Строк по дням нет. Неизвестный номер или идущий Sprint — ошибка по этому номеру, остальные
  номера возвращаются.
- **get_aggregate(op)** с тем же выбором Sprint — op это sum или mean. Возвращает один объект с теми же
  числовыми полями, что у get_retro_data, в каждом — сумма или среднее по всем названным
  Sprint. Модели на 4B–12B считают ненадёжно, поэтому складывает и делит только код; get_retro_data
  своих итогов не считает.
  - Суммируются и усредняются EP, число Action, ключевые Action, минуты и capacity.
  - Отметка Met считается как 1, Not met — как 0, так что sum — это сколько Sprint выполнили
    criteria, а mean — какая доля. Sprint без отметки в это поле не входит.
  - Доля сделанного — всегда отношение сделанных EP к взятым в итоговом объекте. Сумма
    процентов смысла не имеет.
  - Поле, которого у части Sprint нет (capacity выключена, время не отслеживалось, нет
    отметки), считается по тем, у кого оно есть, и объект говорит, по скольким.
  - Даты, тексты criteria и анализа не агрегируются.
  - Объект называет, какие Sprint вошли. Неизвестный номер или идущий Sprint — ошибка по
    этому номеру, остальные считаются.
  - Предела на число Sprint нет: ответ — один объект, сколько бы Sprint ни вошло.
- **open** с видом retro — открывает экран, по номеру, найденному инструментами выше.
- **show_charts** с тем же выбором Sprint и необязательным `chart` — сам рисует графики
  RT-CHART-019 (или один, названный в `chart`) и сразу шлёт их в чат через `AgentContext.chat`,
  без своего сообщения со словами и кнопками; модель потом отвечает одной строкой (RT-CHART-021).
  Число Sprint не ограничено. Номер без закончившегося Sprint и график, которого у выбранных
  Sprint нет, — отказ, ничего не отправлено.
- **Блок текущих значений:**
  - последние RETRO_RECENT_SPRINTS = 5 закончившихся Sprint — номер, ссылка вида
    `[Sprint 26.09-01 retro](retro:12)`, даты, отметка, проанализирован ли;
  - номер идущего Sprint, если он есть;
  - сегодняшняя дата.

**Схема.** В `sprints` добавляется capacity на старте: число EP или пусто, если capacity была
выключена. Её записывает `start_sprint` — и кнопка, и Save из батча 2. Снапшот схемы
обновляется, таблица в описании батча — sprints.

**Сценарии (черновик).**

```gherkin
  Scenario: RT-ASK-013 — A question over several ended Sprints is answered from their records
    Given three Sprints have ended
    When the owner asks for an average over the last three, such as the capacity they started
      with or the effort they finished
    Then Safwa answers with the average the code worked out over the three records
    When the owner asks for a total over them, such as the Actions they finished
    Then Safwa answers with the sum the code worked out, and no number is added up by the model
    And a Sprint with no capacity, no tracked time or no mark is left out of that one number,
      and the answer says over how many Sprints it is
    And each Sprint is named by its number with a link to its retro
    And a running Sprint is never in the answer, since it has no record yet (RT-OPEN-001)

  Scenario: PL-CAPACITY-027 — A Sprint keeps the capacity it started with
    Given the Sprint capacity in the Profile is 20 points
    When a Sprint starts, by the button or by Save
    Then the Sprint keeps 20 points as its capacity
    When the owner later sets the Profile's capacity to 30
    Then that Sprint still says 20
    And a Sprint started with capacity off keeps none, and an average over capacity leaves it out

  Scenario: RT-ASK-014 — A date finds the Sprint whose days it falls in
    Given a Sprint ran from 01.09 to 14.09 and ended on 12.09
    When the owner asks about the Sprint of 10.09
    Then Safwa finds that Sprint
    When the owner asks about 13.09
    Then Safwa says no Sprint was running that day

  Scenario: RT-OPEN-015 — Asked to show a retro, Safwa puts it on screen
    Given the owner names an ended Sprint by its number or by a date in it
    When they ask to see its retro
    Then its retro screen arrives after Safwa's words, as it does from the link (RT-OPEN-002)
```

Для оболочки:

```gherkin
  Scenario: AG-OPEN-052 — A subagent opens only the kinds of screen it declared
    Given a subagent declares that it may open one kind of screen
    When it opens an item of that kind
    Then the screen arrives after Safwa's message, as if Safwa had opened it
    And a subagent that declared no kind is not given the tool
    And a kind it did not declare is refused (SC-OPEN-006)
```

**Проверка.** Вопрос «сколько EP в среднем за последние 3 Sprint» и «сколько Action закончено
всего за них» дают числа из get_aggregate, со ссылками на три retro — в e2e на реальной базе.
Unit-тест get_aggregate: sum и mean по каждому полю, Met как 1 и 0, поле, которого нет у части
Sprint, и доля сделанного как отношение. «Покажи retro Sprint 26.09-01» и «покажи retro
Sprint, который шёл 10 сентября» открывают один и тот же экран. Корневой `open` работает, как
раньше.

## Батч 4 — `route("profile")`

**Сабагент profile:**

- **Назначение для Advisor:** изменить поле Profile или ответить, что там сейчас: About me,
  Advisor instructions, длина и capacity Sprint, время Morning, Diary и дневного итога,
  инструкция Diary, Home after, Time tracking.
- **Views:** нет.
- **Инструмент** `profile` — один вызов с `mode` update, в нём одно или несколько полей из
  `ProfileField`, все десять:
  - время в формате HH:MM;
  - capacity null — значит выключено;
  - длина — целое число;
  - текст заменяется целиком: чтобы дописать, модель пишет старый текст вместе с новым, старый
    она видит в блоке.

  `disabled_hooks` в инструменте нет. `mode` нужен, чтобы null у capacity читался как «выключить»,
  а не как пропущенное поле. Схема с одним значением — `const`, а его не читает Gemini, поэтому
  оболочка пишет такой выбор как enum из одного значения.
- **Prepare** проверяет каждое поле той же проверкой, что `set_profile_field`, и до экрана
  называет ошибку.
- **Apply** вызывает `set_profile_field` для каждого поля. Одно сохранённое поле — одно
  изменение (PS-REVISION-011).
- **Экран ревью:** для каждого поля — было и стало.
- **Блок текущих значений:**
  - все десять полей;
  - часовой пояс, только для чтения (PS-TIMEZONE-010);
  - включена или выключена каждая реакция, по названию, только для чтения, чтобы ответить
    «это переключатель в Profile». Список реакций сабагенту даёт `AgentContext.switches`, его
    заполняет композиционный корень из реестра хуков.

**Что меняется рядом.** В manual раздел Profile «Buttons only. The Advisor cannot change the
Profile» становится таким: поля — и кнопками, и словами, переключатели — только кнопками,
кроме онбординга. PS-FIELD-002 говорил «nine fields», а полей десять — формулировка исправлена.
WS-SCOPE-001 говорил, что каждый способ что-то изменить принадлежит workspace mutator или Diary;
это было неверно ещё с `stop_onboarding`, и теперь он говорит «части, которой принадлежит то, что
меняется».

**Сценарии (черновик).**

```gherkin
  Scenario: PS-AI-019 — A Profile field is set in words too, through the same check
    Given the owner asks Safwa to set the Sprint length to 10 days and the Morning time to 08:00
    Then one review screen shows both fields, each with what it was and what it becomes
    And Save stores each the way the Profile screen does, one change per field
    When the owner asks for a Sprint of 61 days
    Then it is refused before any screen, with the range (PS-SPRINT-LENGTH-003)

  Scenario: PS-AI-020 — The switches stay on the Profile screen
    When the owner asks Safwa to turn an automatic reaction off or on
    Then Safwa says whether it is on now and that its switch is in the Profile
    And nothing is proposed, except turning the onboarding off (OB-STOP-005)
```

**Проверка.** Словами меняется каждое из десяти полей. Save даёт ту же строку `user_profile`, ту же
ревизию и тот же отказ на неверное значение, что экран Profile. Просьба выключить реакцию ничего
не предлагает. Onboarding выключается словами, как раньше.

## Журнал

- 2026-09-28 — план составлен.
- 2026-09-28 — владелец ответил на три вопроса: capacity записывается на старте, старт
  словами — как кнопкой, с Success criteria, датами и длиной на экране proposal, criteria
  идущего Sprint не меняются, просьба получает ошибку. Черновики сценариев ждут одобрения.
- 2026-09-28 — retro получает get_aggregate(numbers, sum или mean): итоги по retro-данным
  считает только он, get_retro_data отдаёт записи без итогов.
- 2026-09-29 — владелец попросил всё одним батчем, и оно сделано: список Retro, сабагенты sprint,
  retro и profile, `AgentSpec.current`, `AgentSpec.opens`, capacity на старте Sprint. Сценарии
  приняты в `.feature` файлы в формулировке черновиков. Проверка прошла: весь `pytest`, e2e,
  shell, ruff, архитектурные метрики без нарушений.
- 2026-10-01 — get_retro_data и get_aggregate выбирают Sprint по номерам, по двум датам или все
  сразу, а неверный выбор получает готовый вызов вместо себя (RT-ASK-017).
- 2026-10-02 — графики ретро по просьбе в чате: `show_charts` шлёт их сам, а `AgentContext`
  получил `chat` и `bot` (RT-CHART-021, AG-RECEIPT-006).
- 2026-10-03 — исправления графиков после ревью: «📈 Charts of recent Sprints» в списке рисует
  последние CHART_SPRINTS_READABLE = 12 Sprint и говорит, из скольких; числа каждого Sprint
  пишутся только до 12 Sprint; подпись Check — название над Values, левее столбиков; текст
  владельца без эмодзи, которых нет в шрифте; пока рисуется, чат показывает отправку фото
  (RT-CHART-018, RT-CHART-019, RT-CHART-021).
- 2026-10-03 — после ребейза на v9.107 (расписания и повторяемые Actions): когда Sprint не знал
  количеств своих расписаний, графики того, что взято, пишут «at least», без доли выполненного,
  и говорят, что итоги — нижняя граница; Effort Points считаются, только когда количества
  известны, а график плана и capacity такой Sprint пропускает (RT-CHART-019, RT-STATS-003).
