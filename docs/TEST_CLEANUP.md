# Сессия аудита и очистки тестов

## Объём и критерий завершения

Запрос владельца: выработать критерии полезного теста и очистить ненужные, повторяющиеся
и перекрытые проверки. Рабочая политика — [TEST_QUALITY.md](TEST_QUALITY.md).
Утверждённые сценарии и поведение приложения сохраняются.

Аудит всего набора завершён, когда для каждой группы поведения рассмотрены проверки всех
уровней, решения записаны, у каждого удаления названо оставшееся покрытие, а итоговые
проверки проходят. Зелёный набор и уменьшение количества тестов сами по себе этого не доказывают.
Завершены инвентаризация, пилот и батчи 1–4 из 10. Батчи 5–10 ещё не выполнены.

## Исходная точка — 28 октября 2026

- Исходная ревизия: 33ac59e9ffa0059c57c6a52729eb96b09d771370.
- Отдельный worktree: test-quality-cleanup; ветка codex/test-quality-cleanup.
- 180 файлов, 1747 функций, 3325 собранных pytest-случаев с учётом параметризации.
- Полный исходный прогон: 3119 passed, 206 skipped; 76.80 секунд на этой машине.
  Пропуски соответствуют проверкам, требующим явного opt-in.

| Группа по расположению | Собранных случаев |
|---|---:|
| Документация: tests/test_docs.py | 636 |
| tests/e2e/, включая opt-in | 435 |
| tests/shell/ | 65 |
| Остальные tests/test_*.py | 2189 |
| Всего | 3325 |

В последней группе находятся также 152 архитектурные проверки и 58 проверок связей BRD.
Итого документация, архитектура и traceability дают 846 случаев. Многие остальные тесты
работают с настоящей БД, поэтому считать все 3325 юнит-тестами неверно.
Количество случаев и длительность — исходная справка, не квота на удаление.

Инвентаризация AST сравнила тела тестов, но не выносила решение об удалении.
Например, проверки отказа расписаний в [test_months.py](../tests/test_months.py),
[test_reminder_ends.py](../tests/test_reminder_ends.py) и
[test_reminders.py](../tests/test_reminders.py) имеют одинаковое тело, но разные входы.
Это не доказанные дубли; их предметный аудит остаётся в очереди.

## Пилот — управление фоновой работой

Прочитаны AG-TURN-024 и связанные сценарии в
[agents.feature](../tests/brd/tg_agent_shell/agents.feature), раздел Concurrency в
[AGENT_ARCH.md](AGENT_ARCH.md), [TurnManager](../src/tg_agent_shell/turn/manager.py),
его потребители и тесты. Код приложения и сценарии не изменены.

Удалены шесть атомарных проверок из [test_reminder_flow.py](../tests/test_reminder_flow.py).
Это история пилота: при измерении в батче 1 один из шести тестов возвращён,
поскольку только синхронные вызовы проверяли работу без event loop (см. ниже).
Во всех строках таблицы удалённая проверка цитировала AG-TURN-024. Имена слева исторические;
имена справа существуют в текущем наборе.

| Удалённый тест | Что продолжает обнаруживать ту же ошибку |
|---|---|
| test_a_background_lease_is_marked_background | `test_background_gate_invalidates_currentness_after_dialogue_revision_changes` в [test_summary.py](../tests/test_summary.py) проверяет текущесть внутри запущенной операции; `test_proposal_ui_gives_up_the_turn_before_after_turn_work` в [test_interruptions_ui.py](../tests/test_interruptions_ui.py) проверяет фон при продолжении после показа предложения |
| test_an_owner_lease_is_not_background | `test_ag_turn_010_nothing_that_arrives_during_an_answer_joins_it` в [test_interruptions_ui.py](../tests/test_interruptions_ui.py) проверяет, что следующий текст и голос не отменяют текущий ответ как фоновые операции |
| test_a_background_lease_never_steals_from_the_owner | `test_background_gate_does_not_start_work_while_foreground_is_active` в [test_summary.py](../tests/test_summary.py) проверяет отказ запуска, отсутствие вызова операции и сохранение хода владельца; последнее утверждение перенесено из удаляемой проверки |
| test_releasing_a_background_lease_gives_the_turn_back | `test_the_summary_hook_writes_after_a_turn_whatever_the_stored_switches_say` в [test_summary.py](../tests/test_summary.py) и `test_proposal_ui_gives_up_the_turn_before_after_turn_work` в [test_interruptions_ui.py](../tests/test_interruptions_ui.py) проверяют освобождение после настоящей работы хука |
| test_cancelling_a_background_lease_bumps_the_dialogue_revision | `test_ag_turn_024_a_save_takes_the_turn_from_background_work` в [test_proposals_e2e.py](../tests/e2e/test_proposals_e2e.py) проверяет смену ревизии, сохранённое изменение, ответ и свободный ход после Save |
| test_ending_background_work_cannot_take_the_owners_turn | `test_new_owner_turn_cancels_the_hook_without_losing_its_lease` в [test_summary.py](../tests/test_summary.py) проверяет, что завершение отменённого хука не публикует старый ответ и не отбирает ход нового запроса |

Оставлены отдельные проверки старой фоновой аренды против новой фоновой аренды,
остановки задачи, сохранения вызывающего цикла и устаревания результата. У этих случаев
разные условия и последствия; обычный успешный E2E их не заменяет.
Проверка AG-TURN-010 также является свидетельством для удалённой атомарной проверки:
одна содержательная проверка может защищать несколько связанных контрактов, её docstring
по-прежнему называет собственный сценарий по правилам BRD.

### Подтверждение перекрытия

Без шести кандидатов затронутая группа прошла: 48 passed, 6 deselected.
Затем отдельные процессы временно подменяли методы и свойства в памяти; файлы приложения
не менялись. Во всех семи пробах оставшиеся тесты падали на проверках поведения:

| Подставленная ошибка | Как её замечает оставшийся набор |
|---|---|
| Фон никогда не распознаётся как фон | Нарушены текущесть операции и запуск работы после предложения |
| Ответ владельцу ошибочно считается фоном | Следующий текст больше не удаляется во время текущего ответа: AG-TURN-010 падает |
| Фон захватывает занятый ход | Запускается запрещённая операция; проверка gate падает |
| Фон отказывается запускаться, но сбрасывает ход владельца | Перенесённая проверка принадлежности хода обнаруживает потерянный запрос |
| Завершение фона не освобождает ход | После хука ход остаётся активным |
| Отмена не меняет ревизию | E2E Save обнаруживает неизменившуюся ревизию |
| Старый фон освобождает любой текущий ход | Отменённый хук отбирает ход нового владельца |

Эти пробы подтверждают конкретные основания удаления, а не доказывают полноту защиты
от всех возможных ошибок. Временные скрипты и журналы запусков не входят в код проекта.

### Итоговая проверка пилота

- Затронутые тесты, E2E предложений, BRD и документация: 796 passed.
- Полный локальный набор: 3117 passed, 206 skipped; 63.40 секунд.
- Ruff: все проверки прошли. Архитектурный сканер: 0 нарушений. Проверка diff: без ошибок.
- Осталось 1741 тестовая функция в 180 файлах и 3323 собранных случая.
  Удалены 6 функций/случаев; два новых документа автоматически добавили 4 случая проверки
  документации. Поэтому общий счётчик уменьшился на 2, а не на 6.
- Длительности двух запусков приведены для воспроизводимости исходной точки; по одному
  запуску нельзя приписывать очистке ускорение набора.

## Дальнейший процесс: ровно 10 батчей

Прежняя очередь заменена [полным пайплайном](TEST_PIPELINE.md) по уточнению владельца:
аудит и очистка всего набора должны уложиться в десять крупных батчей.
[Распределение файлов](test_batches.json) охватывает все 180 тестовых модулей ровно по одному разу.
Внутри батча решения применяются сразу по семействам проверок; полный прогон с покрытием
выполняется после всего изменения, а не после удаления каждого теста.

Пилот не засчитывается как законченный батч. Сравнение его покрытия с шестью возвращёнными
тестами входит в батч 1. По состоянию на подготовку пайплайна завершено 0 из 10 батчей.

Для каждой записи завершённого батча использовать поля из раздела «Отчёт и критерий завершения»
пайплайна. Итоги подготовки инструмента не считать содержательным аудитом назначенных файлов.

### Проверка подготовленного пайплайна

- Инвентаризация: 180 файлов, 3326 случаев, ровно 10 батчей, пропусков и повторных назначений нет.
- Полный прогон с покрытием на четырёх процессах: 3120 passed, 206 skipped, 335.84 секунды.
  Предварительный запуск на 32 процессах превысил таймаут импорта в subprocess;
  таймаут теста сохранён, для замеров ограничено число процессов.
- Контексты сохранены в базе покрытия. Убрано их дублирование в JSON: отчёт сокращён
  с 3.6 ГБ до 2.74 МБ без изменения измеренного выполнения.
- Пробы compare обнаруживают потерю строки и ветви, возвращают прежний контекст строки,
  принимают одинаковое покрытие и отвергают разные исходники. Проверено соответствие
  имён файлов настоящего отчёта базе coverage и чтение настоящего компактного отчёта.
- Документация и BRD: 701 passed; финальные проверки трёх документов: 6 passed.
  Ruff проходит, архитектурных нарушений 0, diff без ошибок форматирования.
- Проверочный замер инструмента не является исходной точкой пилота: в батче 1 всё ещё
  требуется сравнение с возвращёнными шестью тестами по описанному порядку.

## Батч 1 — инфраструктура и структурные контракты

Статус: завершён 29 октября 2026. Проверены все 14 файлов батча; принятый snapshot — batch01-final.
Исходный snapshot baseline-original возвращает обе версии тестовых файлов до пилота
из ревизии 33ac59e9. Приложение, настройки и зависимости совпадают с итоговым замером.
Исходный полный прогон: 3126 passed, 206 skipped (3332 случая с шестью возвращёнными тестами).
Проверка области после изменений, включая wallet restart: 944 passed.

### Рассмотренные семейства — все 14 файлов батча

| Файлы | Решение и защищаемые ошибки |
|---|---|
| [test_architecture.py](../tests/test_architecture.py) | Сохранить сканер, его положительные/отрицательные примеры, проверку циклов, словарь общих пакетов, prompt/schema snapshots и динамическое положение volatile-контекста: они проверяют разные контракты. Проверку создания схемы сделать единственным местом проверки таблиц и индекса |
| [test_brd_traceability.py](../tests/test_brd_traceability.py) | Сохранить все направления связи сценариев, файлов, префиксов, формата и ссылок; удалить повтор проверки неизвестного идентификатора в docstring |
| [test_docs.py](../tests/test_docs.py) | Сохранить проверки ссылок, имён и путей в документах и docstring, а также примеры ошибок самого сканера. Сотни параметров относятся к разным файлам; это не сотни одинаковых юнит-тестов |
| [test_database.py](../tests/test_database.py), [test_backup.py](../tests/test_backup.py), [test_security.py](../tests/test_security.py) | Сохранить шифрование файла/WAL, неверный ключ без записи, ограничения SQL, Unicode LIKE, отмену подключения, утечки потоков, passphrase, блокировки другого процесса, реальное восстановление пары и остановку на каждом шаге. Негативные случаи и шаги замены не эквивалентны happy path. CLI проверяет собственные запросы и отказ до запроса passphrase |
| [test_config.py](../tests/test_config.py), [test_qa.py](../tests/test_qa.py) | Сохранить defaults, явные overrides, нормализацию имени и provider, privacy и изоляцию QA: неправильная конфигурация достигается реальным вводом владельца |
| [test_feature_modules.py](../tests/test_feature_modules.py) | Сохранить регистрацию трёх частей proposals, всех tools, views в настоящей БД, полное перечисление route/sub и отказ неизвестной entity; убрать повтор уже обязательной проверки опубликованных tools при построении prompt |
| [test_infrastructure.py](../tests/test_infrastructure.py) | Сохранить импорт entrypoint в свежем процессе, жизнеспособность poll, снятие claim прерванной работающей сессии и чтение настоящих views с обычным результатом без notice. Убрать второе создание схемы и одиночный случай awaiting approval, покрытый реальным restart |
| [test_shell_wiring.py](../tests/test_shell_wiring.py) | Сохранить сканеры кнопок/редакторов и их регистраций, удаление/dispatched команд, Command-фильтры, два независимых Router, menu/layout, каталог open и start payload. Сканер строк не доказывает доступность реального маршрута; его ограничение не делает его дублем проверок исполнения |
| [test_registry.py](../tests/shell/test_registry.py), [test_standalone.py](../tests/shell/test_standalone.py) | Сохранить разные конфликты регистраций, запрет замены cancel и целый путь второго приложения без импортируемой Safwa. Статическая независимость импортов не заменяет этот путь |
| [test_startup_e2e.py](../tests/e2e/test_startup_e2e.py) | Сохранить запуск до polling/cleanup, отказ до отправки наружу при недоступном PII finder, seed-once после удаления и привязку модели к единому поисковому индексу |

### Удаления и остающееся покрытие

| Удалённый pytest ID | Остающееся покрытие |
|---|---|
| tests/test_brd_traceability.py::test_every_cited_identifier_names_an_approved_scenario | `test_a_citation_names_the_feature_file_that_carries_the_scenario`: неизвестный ID даёт отсутствие ожидаемого файла и отказ; неправильный формат тоже отвергается. Временная проба с неизвестным ID действительно вызвала AssertionError |
| tests/test_infrastructure.py::test_startup_bootstraps_a_new_database_from_the_models | `test_the_declared_schema_is_what_a_fresh_database_gets` в [test_architecture.py](../tests/test_architecture.py). Утверждение об индексе перенесено сюда; проверяются таблицы обоих metadata, повторное создание той же БД больше не нужно |
| tests/test_infrastructure.py::test_startup_closes_every_session_waiting_on_a_process_local_screen | AG-SESSION-009: `test_a_button_drawn_before_a_restart_is_refused_rather_than_claimed` в [test_wallet_restart.py](../tests/shell/test_wallet_restart.py) создаёт настоящее ожидающее предложение, перезапускает приложение, проверяет abandoned у сессий и отказ старой кнопки без записи Entry. Прямой случай running со снятием claim сохранён отдельно |
| tests/test_feature_modules.py::test_a_subagent_only_declares_tools_a_feature_publishes | `test_the_routing_rules_are_generated_from_the_roster` плюс импорт [bootstrap/modules.py](../src/safwa/bootstrap/modules.py): обязательный вызов routes отвергает неопубликованные tools до создания prompt. Временная проба неверной декларации действительно вызвала RuntimeError |

Удалены только импорты, осиротевшие после этих удалений. Файлы приложения и утверждённые
сценарии не изменены. Четыре удаления батча относятся к его собственным файлам;
сохранённые проверки других батчей используются как доказательство перекрытия.

### Исходно непокрытый код

Исходный замер прочитан отдельно от потерь. В recovery нет пропущенных ветвей.
Непокрытые ветви registry — отказы на конфликтующих views/screens/callbacks/text-inputs,
несовместимых presenters и tools/helpers; это нужные проверки входного контракта второго бота,
а не доказанно ненужный код. Текущие декларации не создают такие конфликты.
В backup остаются проверки повреждённого ZIP/manifest/checksum/integrity; в key_file —
неподдерживаемого формата/невалидного файла, неверной длины ключа и платформенного lock.
В security — обработка ошибок CLI; в QA — недостающих реквизитов и интерактивная авторизация.
В database — недопустимый ESCAPE, длина ключа, fallback relevance, repr и другой отказ драйвера.
Это исходные пробелы или платформенные/интерактивные пути; они не возникли от удаления тестов.
Код сохранён: исполнение на Windows с локальными сетевыми заменами не доказывает его ненужность.

### Сравнение покрытия и коррекция пилота

Первый итоговый прогон: 3116 passed, 206 skipped. Сравнение с baseline-original
выявило потери в трёх файлах, вместо предположения о полном перекрытии всех шести тестов.

- В [TurnManager](../src/tg_agent_shell/turn/manager.py) исчезла ветка отсутствующего event loop.
  Возвращён test_cancelling_a_background_lease_bumps_the_dialogue_revision из пилота:
  синхронные отмена и переход к владельцу должны работать и без asyncio task.
  Его комментарий теперь указывает самостоятельную причину сохранения.
- В [onboarding hooks](../src/safwa/features/onboarding/hooks.py) потерянные строки повторно
  выполнились на неизменённых проверках onboarding в контрольном прогоне. Контроль не является
  полным snapshot и не используется для подмены полного результата.
- В [Cards API](../src/safwa/features/cards/api.py) потерянный ранний возврат пустого списка
  относится к сводке Sprint без оставшихся Actions. Исходный контекст указывает неизменённые
  time-tracking E2E; удалённые тесты эту функцию не вызывают. Код сохранён, потеря проверяется
  повторными полными прогонами после восстановления синхронного теста.

Контрольная группа Sprint/onboarding/Reminders: 89 passed; ветка без event loop снова
выполняется. Количество проверок в контрольной группе не является количеством случаев батча.

Повторный полный прогон после возвращения синхронного теста: 3117 passed, 206 skipped.
Ветка TurnManager восстановлена; два прочих различия остались. Они не закрываются
исключениями из измерения или объявлением «ненужного кода». Для надёжного контроля:

- `test_pl_end_015_an_ended_sprint_is_handed_to_safwa` в [test_sprint.py](../tests/test_sprint.py)
  теперь проверяет два содержательно разных итога: незавершённые Actions остались либо
  завершены все. Во втором случае проверяются правильные суммы, нулевой остаток и отсутствие
  названий незавершённых Actions. Добавлен один параметризованный случай PL-END-015.
- `test_ob_tip_002_each_item_is_one_line_with_its_state_as_it_is_now` в
  [test_onboarding.py](../tests/test_onboarding.py) дополнен настоящим созданным Tag в общей
  подборке изменений и проверкой его цитирования. Отдельный тест на внутреннюю функцию не добавлен.

Эти точечные усиления соседних областей входят в исправление потерь батча 1; полный аудит
двух файлов по-прежнему принадлежит батчу 9. Уточнённые сценарии и восстановленный тест:
4 passed. Заключительный полный snapshot batch01-final сравнен с baseline-original.

### Принятый результат батча 1

- Полный набор: 3118 passed, 206 skipped, 222.87 секунды. Всего 3324 случая.
- Сравнение baseline-original → batch01-final: 0 потерянных строк и 0 потерянных ветвей;
  losses.json пуст. Source/config/dependency hashes совпадают, код приложения не изменён.
- В области батча: 944 → 940 случаев, все 14 файлов рассмотрены. В пилоте теперь остаются
  удалёнными пять из шести функций; одна возвращена после проверки покрытия.
- Дополнен один существующий onboarding-тест и добавлен один параметризованный случай Sprint.
  Относительно исходного замера с возвращённым пилотом: 3332 → 3324 случая
  (9 удалённых функций и 1 новый параметризованный случай). Относительно коммита пайплайна:
  3326 → 3324. Оптимизация счётчика не подменяет сохранение проверяемых результатов.
- Ruff проходит, архитектурных нарушений 0, ссылки и имена журнала проверены, diff чист.
- Завершено 1 из 10 батчей. Следующий — runtime, ходы, доступ и hooks; его исходная точка
  batch01-final. Проверки и решения по остальным батчам не объявляются выполненными.

## Батч 2 — runtime, ходы, доступ и hooks

Область: все 19 файлов второго батча, 291 исходный pytest-случай. Исходная точка —
принятый batch01-final; исходники приложения, настройки измерения и зависимости совпадают.
Прочитаны AG-ROUTE, AG-WORDS, AG-NOTHING, AG-BUDGET, AG-TURN, AG-CUE, AG-HOOK,
AG-SESSION и AG-SUB в [agents.feature](../tests/brd/tg_agent_shell/agents.feature),
TG-LOCK-031 и TG-SUB в [telegram_history.feature](../tests/brd/tg_agent_shell/telegram_history.feature),
[CHAT_ACCESS.md](CHAT_ACCESS.md), разделы сессий и конкуренции в [AGENT_ARCH.md](AGENT_ARCH.md),
эффекты и подписки в [HOOK_ARCH.md](HOOK_ARCH.md), соответствующие реализации и тесты.

### Решения по всей области

| Файлы / семейство | Решение и самостоятельная ошибка |
|---|---|
| [test_access.py](../tests/test_access.py), [test_access_dispatcher_e2e.py](../tests/e2e/test_access_dispatcher_e2e.py), [test_access_e2e.py](../tests/e2e/test_access_e2e.py), [test_access_shell.py](../tests/shell/test_access_shell.py) | Сохранить. Точное слово, похожая попытка, команда и голос имеют разные правила. Гонки до/после проверки слова, повторная доставка последнего сообщения, повторная блокировка и Reminder при закрытом доступе не заменяют друг друга. Shell отдельно проверяет подключение второго приложения |
| [test_agent_runtime.py](../tests/test_agent_runtime.py), [test_mini_plan.py](../tests/shell/test_mini_plan.py) | Сохранить. Независимость runtime, токен продолжения, отдельные бюджеты, ошибки чтения, предел повторов, отмена на четырёх этапах и обязательный план имеют разные условия отказа. In-memory хранилище проверяет контракт runtime, SQL/E2E — его другой адаптер |
| [test_subagents.py](../tests/test_subagents.py), [test_subagent_e2e.py](../tests/e2e/test_subagent_e2e.py) | Удалить две проверки деклараций с более сильным оставшимся покрытием. Сохранить реальные наборы tools, доступ к views, передачу слов владельца, собственный transcript, исправление отказанного предложения, завершение/новую сессию, forward, пустой roster и ошибки подагента. Проверки всех деклараций дополняют E2E с несколькими выбранными агентами |
| [test_wallet_subsession.py](../tests/shell/test_wallet_subsession.py) | Сохранить. Отказ, Save, истечение review, /cancel, /exit, clear, lock и исчезновение агента по-разному завершают запрос либо весь разговор. Недоставленный результат и отказ Telegram удалить историю — самостоятельные случаи восстановления |
| [test_cues.py](../tests/test_cues.py), [test_cue_turn_e2e.py](../tests/e2e/test_cue_turn_e2e.py) | Удалить два повторения доставки одной записи; переносить уникальные assertions в уже существующий сценарий группы записей. Сохранить закрытый/пустой gate, взятие аренды и устаревание, stamp recovery, повторное использование ID, Shown, срок жизни и реальные отмены до публикации, при автоодобрении и после первого Save |
| [test_hooks.py](../tests/test_hooks.py), [test_initiatives.py](../tests/test_initiatives.py), [test_tool_watchers.py](../tests/test_tool_watchers.py) | Удалить отдельную проверку пустого каталога. Сохранить матрицу недопустимых подписок/эффектов, фильтрацию и переключатели, изоляцию ошибок/результата, отзыв и продолжение helpers, rollback/commit, группировку событий, интервалы и смену дня. Ошибка Refuse прекращает запрос, ошибка Review пропускает вызов: это разные договоры |
| [test_summary.py](../tests/test_summary.py) | Удалить повторную гонку снимков через hook. Сохранить прямую проверку гонки с False и отсутствием публикации; она также проверяет возвращаемый результат для команды. Успех hook, переключатели, предыдущая Summary, принудительный вызов, отмена и устаревшее событие остаются |
| [test_turn_ui.py](../tests/test_turn_ui.py), [test_turn_steps_e2e.py](../tests/e2e/test_turn_steps_e2e.py), [test_interruptions_ui.py](../tests/test_interruptions_ui.py), [test_progress.py](../tests/test_progress.py) | Удалить три повторения успешного вывода. Сохранить отмену без ответа, typing, отказ редактирования/отрисовки/удаления, ограничения списка шагов, шаги реальной модели и checks, занятый ход, снятие аренды перед after-work и идемпотентную очистку progress |

Малый тест открытого Cue gate оставлен: он проверяет текущесть взятой аренды и её потерю
при приходе владельца до запуска ответа. Он диагностирует конкретную границу, в отличие
от повторного успешного вывода. Параметры безопасности, бюджетов и отмены не сокращены
по одному лишь совпадению текста или scenario ID. Независимые сценарии не склеены.

### Удаления и оставшиеся проверки

Имена слева исторические; каждое удаление — одна функция без параметризации.

| Удалённый тест | Оставшееся покрытие и наблюдаемый результат |
|---|---|
| tests/test_subagents.py::test_the_routing_rules_name_every_subagent_that_can_be_routed_to | `test_the_routing_rules_are_generated_from_the_roster` в [test_feature_modules.py](../tests/test_feature_modules.py) проверяет точный пункт каждого маршрутизируемого агента и число пунктов, включая Diary |
| tests/test_subagents.py::test_the_diary_is_written_only_by_its_subagent | Удалённый тест собирал root tools и Diary вручную. `test_the_board_owns_every_mutation_tool` и `test_a_routed_subagent_is_offered_only_its_own_tools` в [test_subagent_e2e.py](../tests/e2e/test_subagent_e2e.py) проверяют реальные переданные модели наборы: root без Diary/mutations, Diary со своим mutation tool. `test_a_routed_subagent_proposes_for_itself` там же действительно создаёт предложение Diary |
| tests/test_cues.py::test_ag_cue_029_a_waiting_cue_is_said_once_and_then_gone | `test_ag_cue_029_one_tick_says_everything_waiting_as_one_request_oldest_first` в [test_cues.py](../tests/test_cues.py): весь список сказан по порядку, event ID имеет нужную длину, очередь пуста. Перенесён повторный tick: нет ни повторной речи, ни повторного освобождения аренды |
| tests/test_cues.py::test_ag_cue_029_a_turn_that_did_not_land_leaves_the_cue_waiting | Тот же существующий сценарий сначала не доставляет ответ и сохраняет все записи, затем доставляет их под новым ID. Перенесено утверждение, что неудачная попытка тоже освобождает аренду ровно один раз |
| tests/test_hooks.py::test_empty_catalogue_has_no_work | `test_ag_turn_053_the_notice_lists_each_step_as_it_starts` в [test_turn_ui.py](../tests/test_turn_ui.py) проходит реальный ход с пустым каталогом hooks, сохраняет изменение и публикует ответ. `test_a_committed_change_reaches_the_hook_of_its_kind_only` в [test_hooks.py](../tests/test_hooks.py) отдельно проверяет отсутствие работы при несовпавшей подписке и выключенном hook |
| tests/test_summary.py::test_summary_hook_keeps_the_snapshot_check | `test_owner_message_wins_race_with_in_flight_summary` в [test_summary.py](../tests/test_summary.py) использует ту же смену снимка во время запроса, проверяет False и отсутствие записи. Перенесено утверждение об одном запросе провайдеру; подключение hook проверяет `test_the_summary_hook_writes_after_a_turn_whatever_the_stored_switches_say` |
| tests/test_turn_ui.py::test_an_autoapproved_change_still_reaches_the_chat | `test_ag_turn_053_the_notice_lists_each_step_as_it_starts` в [test_turn_ui.py](../tests/test_turn_ui.py) использует тот же TurnAdvisor: настоящая операция меняет Sprint и workspace revision внутри хода, после чего проверяется дошедший ответ. Другие проверки этого файла проходят тот же путь, включая отказ after-work |
| tests/test_turn_ui.py::test_ag_turn_022_the_notice_stands_while_the_answer_is_written | Тот же сценарий шагов проверяет начальное notice, его обновления, удаление и дошедший ответ. Перенесено чтение регистрации после удаления: notice отсутствует в истории |
| tests/test_turn_ui.py::test_the_owner_turn_is_headed_by_the_telegram_name | `test_long_transcript_is_split_and_answered_once` в [test_voice_ui.py](../tests/test_voice_ui.py) проверяет настоящее оформление первой части голосового сообщения, отсутствие повторного заголовка, историю и один ответ. Утверждение усилено до того же HTML с жирным полным именем |

Удалены только осиротевшие импорты и тестовые helpers. Две проверки сводки использовали
одинаковые провайдеры; оставшийся сценарий теперь использует уже существующий записывающий
провайдер. Из файла голоса перенесено одно утверждение; его полный аудит остаётся в батче 10.
Код приложения, prompts, утверждённые сценарии и состав десяти батчей не менялись.

### Исходные пробелы покрытия

Исходный отчёт batch01-final прочитан до сравнения. В runtime остаются неисполненные
защиты от повреждённых JSON/transcript, повторного claim, неверного токена, несуществующей
сессии и обработка более глубокой цепочки родителей. В тестовом in-memory store не исполнены
некоторые отказы для отсутствующих/уже занятых записей. Это непроверенные пути сохранённых
контрактов границы; их ненужность не подтверждена.

В Cue/turn/access не исполнены отдельные гонки между проверкой текущести и отправкой,
ошибки отмены review и повторного принятия отложенного сообщения, callbacks без message,
отключённый scheduler, остановка ещё работающей очереди commit, отказ публикации commit,
default prepare без слов и отказ prepare неизвестного hook. В Summary — отказ устаревшего
callback текущести, ответы команды при занятом ходе/пустой истории и fallback заголовка.
Код сохранён: локальный счастливый путь или отсутствие соответствующего теста не доказывает
ненужность защиты. Эти пробелы были до батча и не списываются в его допустимые потери.

### Проверка и результат

Выборочный запуск всех файлов батча одним списком выявил существующий конфликт импорта
одноимённых conftest в shell/E2E. Раздельные процессы прошли: 331 passed для основной области
и свидетелей перекрытия, 20 passed для shell. После окончательного выбора проверки сводки:
8 passed. Штатный полный запуск собирает весь набор без этого конфликта.

Первый полный snapshot batch02: 3109 passed, 206 skipped, 223.92 секунды.
Сравнение с batch01-final обнаружило одну потерянную строку: запись ошибки условия hook
в [cues/initiatives.py](../src/tg_agent_shell/cues/initiatives.py). Контексты прежнего выполнения —
неизменённые тесты группового Action/Check review; удалённые проверки эту строку не вызывали.
Случайное выполнение их фоновых задач не является надёжной проверкой обработки ошибки.

Существующий `test_ag_hook_037_a_commit_hands_the_change_on_and_a_rollback_does_not`
в [test_initiatives.py](../tests/test_initiatives.py) теперь подключает перед рабочим hook ещё один,
чтение которого падает. После настоящего commit очередь рабочего hook всё равно содержит
правильное изменение, а ошибка первого записана ровно один раз. Rollback по-прежнему
не порождает обработку. Это усиление существующего пути, без новой тестовой функции;
уточнённый сценарий прошёл. Ненужным кодом потерянная защита не объявлена.

### Принятый результат батча 2

- Повторный полный snapshot batch02-final: 3109 passed, 206 skipped, 224.69 секунды.
  Всего 3315 случаев; opt-in-пропуски не изменились.
- Сравнения batch01-final → batch02-final и baseline-original → batch02-final:
  0 потерянных строк и 0 потерянных ветвей, losses.json пуст. Контрольные суммы
  приложения, настроек и зависимостей совпадают.
- Все 19 файлов рассмотрены; область батча: 291 → 282 случая. Удалены девять функций,
  добавленных тестовых случаев нет. Уникальные assertions перенесены в существующие
  сценарии; проверка ошибки hook усилена по результату измерения.
- Инвентаризация подтверждает все 180 файлов в десяти батчах без пропусков и повторений.
  Ruff проходит, архитектурных нарушений 0, diff не содержит ошибок пробелов.
- Завершено 2 из 10 батчей. Следующий — Proposals и Save/Discard; его исходная точка
  batch02-final. Неуспешное сравнение первого snapshot batch02 не подменяет принятый результат.

## Батч 3 — Proposals, подготовка и Save/Discard

Область: все 15 файлов третьего батча, 186 исходных случаев. Вход — batch02-final;
контрольные суммы приложения, настроек и зависимостей сверены до изменений.
Прочитаны [proposals.feature](../tests/brd/tg_agent_shell/proposals.feature), связанные
AG-HOOK/AG-SESSION и CD-TREE, раздел Proposals в [AGENT_ARCH.md](AGENT_ARCH.md),
раздел ответственности Proposal в [FEATURE_MODULES.md](FEATURE_MODULES.md), реализация
подготовки, материализации, очереди, транзакций и отображения, все тесты области.

### Решения по всей области

| Файлы / семейство | Решение |
|---|---|
| [test_proposal_batch.py](../tests/test_proposal_batch.py), [test_proposal_saves.py](../tests/test_proposal_saves.py) | Удалить повтор пустой head; сохранить таблицу разных переходов, порядок head, notes и поздний press. Три проверки настоящей БД отдельно защищают один commit со всеми фактами, rollback всей очереди и отказ устаревшего Save |
| [test_proposals.py](../tests/test_proposals.py) | Удалить искусственное повторение restart и обычный Save под названием проверки uptime. Сохранить разные отказы archived/not-found, закрытие Sprint, ошибку commit с оставшимся review и отказ автоодобрения, после которого уже нет живой очереди |
| [test_proposals_e2e.py](../tests/e2e/test_proposals_e2e.py) | Удалить два повторения создания Tag и ремонта зависимой ссылки; перенести assertions в существующее создание Tag с двумя ссылками. Сохранить порядок/группировку очередей, атомарность одного item, разные завершения очереди, повторный вызов после решения, ошибки чтения рядом с mutation, продолжение после Save, destructive confirmation и гонки хода |
| [test_autoapproval_e2e.py](../tests/e2e/test_autoapproval_e2e.py) | Удалить два подслучая более сильных смешанных/трёхэлементных сценариев. Сохранить create, update/move/link, явное сомнение и недоступность reviewer, последовательное автоодобрение после ручного Save и между ответами модели |
| [test_review_e2e.py](../tests/e2e/test_review_e2e.py) | Сохранить ReturnProposals/HoldAnswer и review завершённого запроса: ошибка до подготовки не равна ошибке проверки ответа. Усилить выключенный review: теперь есть настоящее одобренное изменение, при котором включённая проверка должна была бы сработать |
| [test_review_edit_e2e.py](../tests/e2e/test_review_edit_e2e.py), [test_proposal_fields.py](../tests/shell/test_proposal_fields.py) | Сохранить. Выбор, текст, несколько ticks, отказ после изменения БД, истечение editor, возврат к первоначальному значению, смена операции и повторное чтение Schedule имеют разные последствия. Tag/Value/Check/Diary/Reminder проверяют разные точки подключения. Поле, сохранённое подготовкой без значения в call, и страница поля — самостоятельные возможности shell |
| [test_proposals_ui.py](../tests/test_proposals_ui.py) | Сохранить разные presenter/operations и чистые границы маркировки; сократить два эквивалентных параметра глубины предков. В тесте метки остаются корневой и вложенный родитель, а также собственные ветви высотой 1 и 3. Проверки глубины дерева не объявляются аудитом этого UI-формата |
| [test_proposal_switch.py](../tests/shell/test_proposal_switch.py), [test_proposal_actions.py](../tests/shell/test_proposal_actions.py), [test_preparation_conversation.py](../tests/shell/test_preparation_conversation.py) | Перенести начальные controls и пустой conversation в уже существующий успешный switch. Сохранить отказ/Discard/непредложенный switch, дополнительный action ровно один раз и передачу исходного conversation обычной подготовке и mini session |
| [test_wallet_flow.py](../tests/shell/test_wallet_flow.py), [test_wallet_restart.py](../tests/shell/test_wallet_restart.py) | Сохранить реальный путь второго приложения, баланс, отдельный экран Entry, разделение views, ручные editors, ошибочный ввод и restart с устаревшей кнопкой. Удалить повторный подсчёт home: Registry проверяет его при каждом настоящем старте |
| [test_workspace_mutator.py](../tests/test_workspace_mutator.py) | Сохранить владение tools, допустимые kinds удаления, opt-in контекста, citations, часы, поиск открытых items и имена views в прозе prompts. Проверка прозы не заменяется проверкой объявленного списка views. Настройка настоящего MUTATOR_AGENT остаётся в WS-JUDGE-002; её повторный assertion убран из проверки общего ContextBuilder |

Первая проверка связей обнаружила, что удаление декларационного теста оставило WS-JUDGE-002
без собственного свидетельства. Тест сохранён; из соседнего WS-CONTEXT-004 убран ровно его
повторный assertion. Последний проверяет механизм opt-in на заданных RoutedSubagent,
а первый — настройку настоящего roster. Повторная проверка Workspace/traceability: 78 passed.
Сценарии и их идентификаторы не менялись; PR-STALE-013 теперь цитирует реальный restart Wallet.

### Удаления и оставшиеся проверки

Имена слева исторические. Все десять удалённых функций были без параметризации.

| Удалённый тест | Оставшееся покрытие и результаты |
|---|---|
| tests/test_proposal_batch.py::test_a_batch_with_nothing_left_to_answer_has_no_head | `test_a_batch_moves_only_the_way_the_table_says` в [test_proposal_batch.py](../tests/test_proposal_batch.py) проверяет отсутствие head после закрытия разными решениями, одновременно с решениями каждого item и effects |
| tests/shell/test_proposal_switch.py::test_a_switch_is_a_button_above_the_decision | `test_saving_the_switched_proposal_writes_the_other_call_and_answers_the_first` в [test_proposal_switch.py](../tests/shell/test_proposal_switch.py) теперь сначала проверяет тот же исходный Ledger entry и точный порядок controls, а затем switch, Save, данные и ответ модели |
| tests/shell/test_proposal_switch.py::test_ag_session_063_a_switched_call_is_prepared_with_no_conversation | Тот же успешный switch проверяет пустой conversation непосредственно после подготовки, до Save. Обычная передача conversation остаётся в [test_preparation_conversation.py](../tests/shell/test_preparation_conversation.py) |
| tests/shell/test_wallet_restart.py::test_the_application_publishes_exactly_one_home_screen | `test_a_read_becomes_a_proposal_and_the_owner_decides_it` в [test_wallet_flow.py](../tests/shell/test_wallet_flow.py) действительно запускает приложение с его Registry; обязательный constructor отвергает отсутствие home и дубли навигации до выполнения flow |
| tests/test_proposals.py::test_startup_clears_what_an_unanswered_review_left_behind | `test_a_button_drawn_before_a_restart_is_refused_rather_than_claimed` в [test_wallet_restart.py](../tests/shell/test_wallet_restart.py) создаёт настоящий review, перезапускает приложение, проверяет abandoned, удалённые tokens и очищенный related_id, затем нажимает старый Save и проверяет отсутствие Entry |
| tests/test_proposals.py::test_process_uptime_does_not_expire_a_proposal_whose_review_is_still_open | Тест не менял время: это был обычный Save с проверкой отсутствия внутреннего created_at. Успех такого Save проверяет `test_single_tag_proposal_save_and_discard_callbacks_resume_agent` в [test_proposals_e2e.py](../tests/e2e/test_proposals_e2e.py), прямого approve — `test_saving_card_proposal_applies_every_editable_field` в [test_proposals_ui.py](../tests/test_proposals_ui.py). `test_pr_expire_029_a_review_nobody_answered_closes_the_request_for_good` отдельно проверяет действительную границу времени показанного review и отсутствие отсчёта для queued proposal |
| tests/e2e/test_autoapproval_e2e.py::test_batch_is_reviewed_head_first_without_a_bulk_block | `test_the_third_in_a_queue_is_not_read_while_the_second_is_on_screen` в [test_autoapproval_e2e.py](../tests/e2e/test_autoapproval_e2e.py) проходит тот же Tag/Value префикс, проверяет отсутствие записи, approved/pending/pending и отсутствие третьего review. Перенесена проверка approval_source=auto |
| tests/e2e/test_autoapproval_e2e.py::test_non_allowlisted_operation_does_not_call_the_reviewer | `test_one_unlisted_change_shows_the_whole_proposal` там же содержит ту же complete рядом с разрешённой update, проверяет отсутствие обращения к reviewer и показ всего proposal. Дополнительно проверяется неизменённый Backlog, как в удалённом тесте |
| tests/e2e/test_proposals_e2e.py::test_ai_approved_tag_proposal_creates_a_reusable_tag | `test_ai_create_tag_and_links_are_reviewed_as_separate_proposals` в [test_proposals_e2e.py](../tests/e2e/test_proposals_e2e.py) теперь создаёт Tag с описанием, проверяет сохранённые имя/описание и действительно использует Tag в двух связях |
| tests/e2e/test_proposals_e2e.py::test_new_tag_and_dependent_card_link_use_one_repair_round | Тот же существующий сценарий проверяет отдельные screens, одну ремонтную итерацию по числу provider calls, результат создания с affected_ids, reference_not_found до создания и настоящие CardTag после Save. Перенесены утверждения о первоначальных tool results |

Из `test_cd_tree_002_the_review_screen_shows_the_goal_becoming_a_subgoal` в
[test_proposals_ui.py](../tests/test_proposals_ui.py) удалены параметры (2, 0, 🎯) и (5, 0, 🎯).
Вместе с (1, 0, 🎯) это одна группа для отображения: у родителя есть предки, у переносимого
Goal нет собственных Goal-детей. Метка считается снизу, а не от глубины родителя.
Оставлены (0, 0, 🎯), (1, 0, 🎯), (0, 1, 🎯²), (0, 3, 🎯⁴), проверка родителя на экране
и реально сохранённая связь. Это сокращение двух случаев, не удаление проверки CD-TREE-002.

### Усиления слабых проверок

- `test_independent_mutations_are_reviewed_in_order_before_one_resume` раньше проверял
  all(approved) над списком, про который тут же утверждал, что он пуст. Теперь читается
  фактический запрос возобновлённого подагента: ровно два approved, в порядке Tag/Value.
- `test_request_review_off_reads_no_answer` раньше не делал изменений и проходил бы также
  с включённой проверкой. Теперь реально одобряется Action в запросе владельца, после чего
  проверяются дошедший ответ, четыре model calls и отсутствие request-review call.
  Уточнённый сценарий прошёл; новая тестовая функция не добавлена.

Удалены только осиротевшие импорты. Продуктовый код, prompts, зависимости, настройки покрытия
и распределение файлов не менялись. Независимые сценарии не объединялись ради счётчика.

### Исходные пробелы покрытия

Исходный batch02-final содержит непроверенные отказы edit/switch для исчезнувшего review,
поля или switch; повторный press/claim и закрытие отсутствующей очереди; исключения commit,
отличные от DomainError; невалидный результат ReturnProposals; fallback рендера без presenter
или item и ошибки построения receipt после уже совершённого Save. В registry Proposal остаются
отказы для пустых, неизвестных и неоднозначных ссылок; в marks — bool и пустые строки;
в Workspace state — unestimated нагрузка и повторно встреченный предок DAG.

Это непроверенные варианты существующих границ shell и отображения, а не доказанно ненужный
код. Fallback/защиты сохранены; отсутствие выполнения при нынешних декларациях Safwa не
доказывает, что они не нужны второму приложению. Все строки reducer/store выполнялись;
пропущенные переходы также просмотрены. Исходные пробелы не считаются допустимыми новыми потерями.

### Проверка и результат

Первый выборочный прогон: основная область 210 passed и один отказ traceability, shell —
18 passed. Отказ исправлен сохранением WS-JUDGE-002, а не ослаблением проверки ссылок.
Повторная проверка Workspace/traceability — 78 passed; усиленный выключенный review — 1 passed.
Окончательная проверка области: 213 passed для основной области и BRD, 18 passed для shell.

### Принятый результат батча 3

- Полный snapshot batch03: 3097 passed, 206 skipped, 144.28 секунды. Всего 3303 случая;
  opt-in-пропуски не изменились.
- Сравнения batch02-final → batch03 и baseline-original → batch03: 0 потерянных строк
  и 0 потерянных ветвей, losses.json пуст. Контрольные суммы приложения, настроек
  и зависимостей совпадают; повторный полный замер не потребовался.
- Все 15 файлов рассмотрены; область батча: 186 → 174 случая. Удалены десять функций
  и два эквивалентных параметра; новых тестовых случаев нет. Две слабые проверки усилены,
  полезные assertions перенесены в существующие сценарии, повтор настройки контекста убран.
- Все 180 файлов назначены десяти батчам без пропусков и повторений. Ruff проходит,
  архитектурных нарушений 0, diff не содержит ошибок пробелов.
- Завершено 3 из 10 батчей. Следующий — Cards и иерархия; исходная точка — batch03.

## Батч 4 — Cards, иерархия, порядок и учёт

Рассмотрены все 18 файлов, 292 исходных случая. Вход — batch03 на неизменённых исходниках.
Прочитаны контракты CD-KIND/TREE/STAGE/DELETE/ORDER, завершения, Effort/Time и hooks в
[cards.feature](../tests/brd/cards.feature), связанные сценарии Profile/Retro/Schedules,
реализация hierarchy, операций Card, проверки proposal и сортировки.

### Решения по области

| Семейство и файлы | Решение |
|---|---|
| [test_cards.py](../tests/test_cards.py), [test_card_hierarchy.py](../tests/test_card_hierarchy.py), [test_parent_completion.py](../tests/test_parent_completion.py) | Удалить три старых повторения стадий/удаления. Сохранить ограничения kinds/полей, ошибки создания/ссылок, разные состояния закрытого/открытого Goal, rollback, архив, общих потомков DAG, все пути к родителям, цикл и предел глубины |
| [test_card_creation.py](../tests/test_card_creation.py), [test_goal_markers.py](../tests/test_goal_markers.py) | Сохранить границу Pydantic отдельно от domain move, реальный тип timestamp и запрет старой копии repeat. Удалить повтор короткой цепочки меток, сохранив glyphs каждой высоты, изменение структуры, SQL и предел дерева |
| [test_cards_ui.py](../tests/test_cards_ui.py) | Удалить старое повторение обзора и переходов. Сохранить создание/редактирование, paging, compact/full, разные controls по kind, блокировку, ссылки/Back, повторение и destructive delete. Draft и сохранённая Card имеют разные пути, их проверки не взаимозаменяемы |
| [test_card_ordering.py](../tests/test_card_ordering.py) | Убрать шесть значений внутри уже проверяемых групп. Сохранить границы 1/2 и 7/8 дней, undated, местную дату, окна Sprint/Planning, 90/91, Priority/Value/key, общих предков, минимальное расстояние Value и собственный SQL order/offset |
| [test_card_closing.py](../tests/test_card_closing.py), [test_effort_tracking.py](../tests/test_effort_tracking.py), [test_time_tracking.py](../tests/test_time_tracking.py), [test_repeat_times.py](../tests/test_repeat_times.py) | Сохранить настоящие Save/Back, переключатели, Pending Checks, старые кнопки, пустую оценку, время на законченной копии, следующую копию, limits и исторический retro. Сократить декартово произведение отказов accounting с 36 до 12 по одному правилу множества допустимых полей |
| [test_parent_hooks.py](../tests/test_parent_hooks.py), [test_parent_completion_hook_e2e.py](../tests/e2e/test_parent_completion_hook_e2e.py), [test_parent_hooks_e2e.py](../tests/e2e/test_parent_hooks_e2e.py) | Сохранить. Выбор предметных объектов, повторная проверка перед доставкой, commit/rollback и подключение реестра защищают разные ошибки. Новый Action, reopen, закрытие Goal, delete и выключение hook не являются эквивалентными параметрами |
| [test_blocker_hook_e2e.py](../tests/e2e/test_blocker_hook_e2e.py), [test_empty_parents_hook_e2e.py](../tests/e2e/test_empty_parents_hook_e2e.py), [test_time_tracking_e2e.py](../tests/e2e/test_time_tracking_e2e.py), [test_done_words_live.py](../tests/e2e/test_done_words_live.py) | Сохранить реальные пути от операции через commit/Cue к словам и следующему Save. Live-набор проверяет выбор настоящей модели, языки, контекст Reminder и times; scripted tests его не заменяют. Live не запускался |

Две похожие проверки архива оставлены: одна закрывает Goal явно и сохраняет его Done и
completed effort, другая оставляет Goal открытым и читает архив через SQL view. Это разные
состояния, а не доказанный дубль. Схема timestamp также сохранена: SQLite может вернуть
naive datetime при другом типе столбца, даже когда SQL snapshot выглядит одинаково.

### Удаления и оставшиеся проверки

| Удалённая функция | Оставшееся покрытие |
|---|---|
| tests/test_cards.py::test_parent_stage_propagation_and_reopen | `test_cd_stage_014_a_goal_shows_the_stage_of_the_actions_under_it` и `test_cd_stage_015_a_goal_is_done_only_when_explicitly_closed` в [test_cards.py](../tests/test_cards.py) проверяют переходы открытого Goal; `test_explicit_completion_and_reopen_keep_checks_and_child_state` в [test_parent_completion.py](../tests/test_parent_completion.py) — сохранение закрытых родителей при reopen Action, с Checks и Subgoal |
| tests/test_cards.py::test_an_action_cannot_reach_a_terminal_stage_through_move | `test_cd_stage_016_done_comes_from_finishing_not_from_moving` проверяет тот же отказ move, неизменённую Today, а затем настоящее завершение с Check. Отдельная граница tool validation остаётся в [test_card_creation.py](../tests/test_card_creation.py) |
| tests/test_cards.py::test_cd_delete_025_deleting_a_goal_leaves_its_children_standing | `test_cd_delete_025_children_lose_the_deleted_parent_and_keep_their_others` в [test_card_hierarchy.py](../tests/test_card_hierarchy.py) проверяет сохранение прямых/глубоких потомков, других родителей, unlink audit и пересчёт предка. Перенесено утверждение о неизменённых kinds Goal/Action |
| tests/test_goal_markers.py::test_cd_button_048_goal_subgoal_action_chain_counts_only_goal_levels | `test_cd_tree_053_seven_goals_with_an_action_keep_the_root_marker_seven` в [test_goal_markers.py](../tests/test_goal_markers.py) проверяет ту же невключённость Action в высоту на длинной цепочке, при create и link, с настоящим SQL view. Перенесены проверки glyphs листового Goal и Action |
| tests/test_cards_ui.py::test_card_overview_uses_derived_progress_and_relationship_navigation | `test_cd_link_055_a_cards_relations_are_links_in_its_words` в [test_cards_ui.py](../tests/test_cards_ui.py) проверяет те же totals 3/8 EP, ссылку Children без дублирующей Effort и Back, плюс Value/Tag. Перенесены число, точные кнопки двух прямых детей и отсутствие глубокого потомка. Action→Parent отдельно проверяют `test_cd_view_031_a_card_opens_compact_with_full_editing_one_button_away` и `test_cd_tree_005_no_screen_can_change_a_cards_parent` |

В [test_card_ordering.py](../tests/test_card_ordering.py) сокращены:

- Из test_action_date_windows_have_one_common_order: -30, 30, 31; остаются -1, 0, 1, 2, 7, 8, None.
- Из test_action_time_and_priority_precedence_changes_by_group: день 7; остаются 0, 1 и 2,
  представляющие два разных порядка сравнения. Сам конец окна 7 сохранён в первом тесте.
- Из test_last_action_group_compares_values_before_appointments: 30 и 100; остаются 8 и None,
  то есть поздняя дата и отсутствие даты. Другой границы Action после дня 7 контракт не имеет.

В test_mixed_accounting_update_on_a_finished_action_is_refused_without_changes в
[test_time_tracking.py](../tests/test_time_tracking.py) вместо 2×6×3 оставлены 2×6 сочетаний.
Для каждого repeating=False/True остаются пары extra/accounting:
schedule=null / tracked_mins=30; blocked_description=null / effort_points=5;
title=Run far / оба null; tags=[] / tracked_mins=30; categories=[] / effort_points=5;
stage=today / оба null. Все шесть лишних ключей и все три формы accounting сохраняются.
Отказ зависит от множества ключей, не от пары выбранной формы с каждым лишним ключом.
Сохранены правильный код отказа, hints, неизменность всех столбцов, отсутствие dirty/new
и нетронутая следующая копия. Положительные accounting и очистка null проверяются отдельно.

### Исходные пробелы и проверка

Прочитаны исходные пропуски batch03: защиты отсутствующих/архивных объектов, stale version,
неверный kind/поле selector, неполный draft, истёкший Done editor, недостающий presenter/item,
неизвестное Schedule количество и неполная оценка; также callback-пути создания/возврата
draft, которые нынешние проверки не нажимают. Это исходные непроверенные пути. Код сохранён;
отсутствие выполнения не доказывает ненужность пользовательского callback или защиты.

Затронутая область и BRD: 305 passed, 9 opt-in skipped. Ruff и diff check проходят.
Первый snapshot batch04: 3062 passed, 206 skipped, 145.04 секунды. Сравнение с batch03
нашло две строки: пропуск Action в пересчёте ancestors и passing для неизвестного/не Advise hook.
Предыдущие контексты — неизменённые параметры switch_off и close_parent в parent-completion
E2E, а не удалённые проверки. Это нестабильное выполнение фоновых путей, не доказанное перекрытие.

В существующем DAG-сценарии усилили повторный пересчёт от изменённой Action: сама Action
сохраняет оценку, все Goals уже актуальны и повторный вызов ничего не меняет. В существующем
сценарии слов hook дополнительно проверяется отсутствие passing у удалённого hook и Offer.
Новые функции тестов и исключения покрытия не добавлялись; код приложения не менялся.
Принятый snapshot batch04-final: 3062 passed, 206 skipped, 140.67 секунды.
Сравнения с batch03 и baseline-original: потерянных строк и ветвей нет.
Архитектурный сканер не нашёл нарушений. Батч: 292 → 257 случаев; весь набор: 3303 → 3268.
