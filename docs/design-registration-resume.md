# Дизайн: возобновляемая анкета PartyRadar

## Решение и инварианты

**Начатая анкета** — одна запись в `registration_sessions` для незарегистрированного пользователя, созданная при первом входе в визард и содержащая введённые данные и текущий шаг. Пустое состояние до явного нажатия «Создать сигнал» не является начатой анкетой. Состояния редактирования — ключи `STEPS`; `preview` — сохранённый черновик на экране предпросмотра. Запросы и редактирование уже опубликованного профиля используют собственные состояния и не являются черновиками регистрации.

Инварианты:

1. Любой успешный ввод/выбор сохраняется до выдачи следующего экрана. Ошибка проверки не сбрасывает ни поля, ни шаг.
2. «Выйти»/«Отмена» — остановить визард, сохранить черновик, перейти в меню/вне визарда. «Начать заново» — явное подтверждение/действие, которое удаляет черновик и создаёт пустой. Никакая команда навигации не сбрасывает данные.
3. Черновик принадлежит только `user_id`; он не является `profiles` и никогда не участвует в выдаче, счётчиках, фильтрах колоды и аналитике опубликованных профилей.
4. Публикация — единственный переход, который создаёт/обновляет профиль. Успешная публикация и явный сброс удаляют черновик; выход, ошибка, рестарт процесса и неполная публикация — нет.
5. Согласие 18+ остаётся независимым разовым событием `consent_18`; выход из анкеты его не отменяет.

## Пользовательские переходы

| Точка входа / действие | Незарегистрирован, consent есть, действующий черновик | Черновика нет / истёк | Зарегистрирован (`is_registered=1`) |
|---|---|---|---|
| `/start` | Экран возврата: «Продолжить» → текущий/нормализованный шаг, «Начать заново» → подтверждение/сброс и шаг 1 | Welcome с «Создать сигнал» | Текущая логика возвращения в меню; черновик игнорируется и не предлагается |
| `/menu` | Показать меню, сохранив черновик; добавить пункт/баннер «Продолжить анкету» (и «Начать заново») | Обычное меню | Обычное меню без изменений |
| «🎮 Создать сигнал» (`onb:start`) | Тот же экран «Продолжить / Начать заново», не вызывать безусловный `reg.start` | Начать пустую анкету | Не запускать регистрацию: вернуть меню/«Моя анкета»; не трогать профиль |
| `/cancel` | Выйти из визарда, черновик сохранить, отправить меню с доступом к продолжению | Обычное меню | Обычное меню |
| Кнопка «🛑 Отмена» | То же, что `/cancel`; не `clear_draft` | Обычное меню | Не трогать профиль |
| «Начать заново» | Только после явного выбора: очистить старый draft и создать новый в одной атомарной операции | Начать пустую анкету | Не изменять профиль через визард |
| «Выйти»/«Не сейчас» | Сохранить, сообщить, что прогресс хранится 30 дней; продолжение через `/start`, `/menu` или «Создать сигнал» | Просто выйти | Без действия над профилем |

В `screen_welcome` и `screen_need_profile` кнопка остаётся «Создать сигнал», но маршрутизируется через проверку состояния. Экран возврата показывается при `/start` и явном входе в регистрацию; `/menu` остаётся меню, чтобы команда сохраняла смысл. Экран меню должен показывать возобновление, если есть действующий черновик. Ошибки ввода остаются на текущем шаге с сообщением валидации; они не считаются выходом. Ошибка публикации показывает первый отсутствующий/нормализованный шаг, сохраняет draft и предлагает продолжить, не «начать заново».

Для экрана выбора определить callback-и: `onb:resume`, `onb:restart`, `onb:restart_confirm`, `onb:restart_keep` (либо один callback `onb:restart` с подтверждением, если нужна защита от случайного нажатия). Только подтверждённое действие сбрасывает данные. Существующие `onb:cancel` трактовать как выход/сохранение.

## Срок жизни и совместимость

**TTL: 30 суток с момента последнего содержательного сохранения.** Использовать `updated_at`, который обновляется при принятом вводе, выборе/пропуске, нормализации мигрированного черновика и переходе шага. Не обновлять срок простым просмотром экрана/командой: иначе забытый черновик может жить бесконечно. Проверка срока — `updated_at >= datetime(now, '-30 days')`; время хранится/сравнивается в одном формате, как текущие `FMT`. Некорректная или отсутствующая дата считается истёкшей (fail closed). Истёкший draft удаляется лениво при чтении/проверке и не показывается на экране возврата; можно показать «Срок хранения истёк» и начать новую анкету. Новые колонки не нужны.

Добавить `Store._migrate_registration_sessions()` вызов после `schema.sql` (без добавления колонок). Здесь обеспечить совместимость существующей таблицы при необходимости, но TTL опирается на имеющиеся `created_at`/`updated_at`. Не изменять `CREATE TABLE IF NOT EXISTS` ради уже существующей схемы.

Нормализация неизвестного/устаревшего `state` выполняется чисто в `registration.normalize_draft(state, draft) -> tuple[str, dict]`: не показывать пустоту и не падать. Правило — собрать целостный префикс шагов по присутствующим и валидным данным, затем выбрать ближайший незаполненный шаг в порядке `STEPS`; если все обязательные данные заполнены — `preview`. Проверять поля через текущие валидаторы/допустимые коды конфигурации, а не только truthy. Не выдумывать отсутствующие ответы. Если устаревшее состояние `search` и есть `bio` (старый визард сразу просил биографию), считать биографию сохранённой и перейти к `photo` — оставить текущую compatibility-ветку только для старых записей, у которых `search_configured` отсутствует; для новых записей состояние `search` принимает только кнопки поиска, текст не должен внезапно означать bio.

Схема разрешения пропусков: создать `registration.next_required_state(draft) -> str | 'preview'` — порядок `gender, age, country, city, languages, games, search, bio, photo`; опциональные `city` (пропуск) и `photo` (без картинки) считаются завершёнными только при явном маркере, например `city_skipped=True` и `photo_file_id=None` вместе с `photo_skipped=True`. Для старых черновиков без маркеров и без значения город/фото трактовать поля как пропущенные/необязательные, а не создавать бесконечный обязательный шаг. `search_mode` и `gender_pref` — значения с безопасными дефолтами `city` и `any`; отсутствие `search_configured` означает, что шаг поиска ещё не подтверждён: вернуть на `search`, но сохранить ранее собранные поля. При заходе на старый `search` с биографией compatibility-переход выше действует только для draft, распознанного как старый формат.

В публикации проверять полный набор фактически обязательных данных (сейчас `gender`, `age`, `country`, непустые `languages`, непустые `games`, валидный `bio`; `city` и фото опциональны; search-параметры имеют defaults). При нехватке полей вычислить первый недостающий/невалидный этап и сохранить нормализованный draft. Не сбрасывать его и не просить «начать заново».

## Контракты Store

Все `user_id` — внутренний ID таблицы `users`. Существующие `save_draft/load_draft/clear_draft` сохранить для совместимости вызовов и текущих тестов, но регистрации перевести на более строгие методы. `load_draft` сохраняет старый контракт `(state, draft, attempts)`.

```python
@dataclass(frozen=True)
class DraftSession:
    state: str
    draft: dict[str, Any]
    attempts: int
    created_at: str
    updated_at: str

Store.load_draft_session(user_id: int, *, now: datetime | None = None) -> DraftSession | None
Store.save_draft_session(user_id: int, state: str, draft: dict[str, Any], *,
                         attempts: int = 0, expected_updated_at: str | None = None,
                         now: datetime | None = None) -> bool
Store.restart_draft(user_id: int, initial_state: str = "gender", *,
                    initial_draft: dict | None = None, now: datetime | None = None) -> DraftSession
Store.expire_draft(user_id: int, *, now: datetime | None = None) -> bool
Store.clear_draft(user_id: int) -> None  # прежний контракт; только публикация/явный restart/expiry
```

- `load_draft_session`: `SELECT state,draft_json,attempts,created_at,updated_at FROM registration_sessions WHERE user_id=?`; если отсутствует → `None`; если протухла/дата невалидна → условный `DELETE ... WHERE user_id=? AND updated_at=?`, вернуть `None`. Зарегистрированного пользователя не считать кандидатом на возобновление: при вызове сервиса/роутинга это проверяется `is_registered` до запроса; опционально SQL дополнить `NOT EXISTS` по `profiles.status IN ('active','paused')` для защиты от обхода.
- `save_draft_session`: ограничить `attempts` диапазоном `[0, MAX_VALIDATION_ATTEMPTS]`; INSERT/UPDATE с `created_at` при первом INSERT, при conflict сохранять исходный `created_at`, обновлять `updated_at`. Если передан `expected_updated_at`, сделать CAS одним SQL `UPDATE ... WHERE user_id=? AND updated_at=? AND state=?` (state также должен соответствовать загруженному состоянию; при INSERT разрешить только режим без expected). Возвращать `True` только если строка записана. Операция выполняется короткой транзакцией, SQLite busy timeout уже настроен.
- `restart_draft`: одним transaction/UPSERT независимо очистить значения и attempts, установить `state='gender'`, `draft_json` в `{"languages": [], "games": []}`, оба timestamp текущим временем; не делать сначала отдельный clear, чтобы между ними не вклинились callbacks. Возвратить сохранённую запись.
- `expire_draft`: удалить только если `updated_at` истёк, returning bool; условие DELETE должно повторно проверять срок на `now`, чтобы не стереть обновлённый конкурентным действием draft.
- TTL сравнивать по вычисленной границе, не строить SQL из пользовательских данных. Для конкурентных чтений/удалений использовать `BEGIN IMMEDIATE` или условный DELETE/CAS; не держать транзакцию во время Telegram-вызова.

### Гонки, дубли и attempts

`MAX_VALIDATION_ATTEMPTS = 5` подряд на текущем шаге; при валидном принятом ответе обнулять счётчик. После 5-й ошибки продолжать показывать шаг и конкретную подсказку, не блокировать дальнейшую попытку, не сбрасывать данные и не зацикливать автоматическими переходами. `attempts` не использовать как номер шага.

Каждая кнопка/ввод читает `DraftSession`, нормализует её и сохраняет следующим состоянием через CAS по `(user_id, state, updated_at)`. Если CAS проигран (двойное нажатие/параллельные callbacks), не применять старое действие повторно: перечитать текущую запись и отрисовать актуальный экран/сообщить «Анкета уже обновлена». Повторная callback-кнопка не должна откатить шаг или повторно применить выбор. Для действий внутри многошагового шага (`languages`, `search`) разрешать mutation только когда session.state равен этому шагу; CAS сериализует изменения. Все значения и состояния валидировать allowlist-ами. Callback на `publish` повторно проверяет регистрацию/полноту и имеет атомарную границу успешной публикации: создать профиль и удалить draft в транзакции, только если пользователь ещё не зарегистрирован; повторная публикация возвращает текущее состояние без второго события `profile_published`.

Текущий `create_profile` выполняет несколько запросов; публикация требует обернуть профиль, языки/игры/filters, удаление черновика и `profile_published` в одну DB transaction либо обеспечить эквивалентный idempotent transaction-метод Store. Telegram-эффекты выполнять после commit. Для двух независимых процессов CAS на updated_at/state — межпроцессная синхронизация; обновления timestamp должны иметь достаточную точность для CAS: обеспечить монотонный уникальный revision без новой колонки (например `updated_at` с микросекундами только для session-методов), либо добавить колонку `revision INTEGER NOT NULL DEFAULT 0` только аддитивной миграцией. Предпочтение: аддитивная `revision` плюс атомарный `revision=revision+1`, потому что timestamp с точностью до секунды не является безопасным CAS. Если добавляется, `Store._migrate_registration_sessions` делает `PRAGMA table_info` и `ALTER TABLE registration_sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 0`, повторяемо и без изменения `schema.sql` как единственного способа миграции. Тогда `DraftSession` включает `revision`, а CAS использует `WHERE user_id=? AND revision=?`; это предпочтительный контракт.

## Контракты handlers и UI

Все функции ниже — без Telegram API. Они могут читать/записывать Store, как уже принято в `registration.py`; `bot.py` только разбирает update, вызывает их и доставляет `Screen`.

```python
registration.resume_screen(store, user_id) -> Screen
registration.get_or_start(store, user_id) -> Screen
registration.start(store, user_id) -> Screen            # сохранить смысл: новый пустой draft; не вызывать при обычном возврате
registration.restart(store, user_id) -> Screen          # явный сброс, через Store.restart_draft
registration.exit(store, user_id) -> Screen             # не clear; экран меню/подтверждение сохранения
registration.normalize_draft(state, draft) -> tuple[str, dict]
registration.next_required_state(draft) -> str          # шаг или 'preview'
registration.step_screen(store, user_id) -> Screen      # всегда normalise + persistence при нужной миграции
registration.on_button(store, user_id, action, value=None) -> Screen
registration.on_text(store, user_id, text) -> Screen | None
registration.on_photo(store, user_id, file_id) -> Screen | None
registration.publish(store, user_id) -> tuple[Screen, bool]
```

`resume_screen` показывает экран выбора, только если есть действующий совместимый draft и пользователь не зарегистрирован; иначе возвращает welcome/need-profile appropriate. `get_or_start` при наличии записи делегирует `resume_screen`, без записи создаёт первую сессию и возвращает шаг gender. `start` не должен перезаписывать существующий draft: либо сделать alias `get_or_start`, либо оставить его только как внутренний конструктор и обновить все входы; безопаснее — изменить контракт `start` на get-or-resume, а создание пустого перенести в `restart`. При существующей анкете возврат всегда в шаг/preview, не в gender.

Экранные функции в `ui/screens.py`:

```python
screen_registration_resume(step: int, total: int, title: str,
                           progress_summary: str = "") -> Screen
screen_registration_restart_confirm() -> Screen
screen_registration_expired() -> Screen
screen_registration_validation_error(step, total, title, message, rows) -> Screen
```

Экран продолжения содержит две различимые кнопки: `▶️ Продолжить с шага N` (`onb:resume`) и `🔄 Начать заново` (`onb:restart`). При подтверждении сброса — «Отмена» возвращает к продолжению, не стирая draft. Экран не включает личные данные draft, кроме названия шага/номера.

`bot.py`:

- `cmd_start`: сохранить текущие проверки/referral/consent; после consent и до welcome проверить регистрацию, затем `reg.resume_screen` если незарегистрированный draft существует, иначе welcome.
- `cmd_menu`: убрать `clear_draft`; открыть меню, а для пользователя без профиля дополнить `screen_need_profile`/home экраном «Продолжить анкету» при draft.
- `cmd_cancel`: убрать `clear_draft`; `reg.exit` либо home + возобновление.
- обработчик `onb:start`: вызвать `reg.get_or_start`; `onb:resume` — `reg.step_screen`; `onb:restart` — подтверждение; подтверждённый restart — `reg.restart`; `onb:cancel` — `reg.exit` без удаления.
- публикация: через атомарный Store-контракт; неполнота ведёт на правильный шаг; успех очищает draft.
- единообразно проверить `is_registered` перед registration callbacks, не пускать в визард существующего профиля. Не менять существующие edit-пути `callbacks._profile_action`, `on_edit_text`, `on_edit_photo`: регистрации не должны очищать их состояние или обновлять профиль.

Меню/need-profile UI можно расширить отдельной кнопкой «▶️ Продолжить анкету» и обычным «Начать заново». Не переиспользовать draft регистрации для edit_bio/edit_games/edit_photo и request_*_pending: текущий Store общий по таблице; навигационные методы должны различать namespace state и не удалять/перезаписывать чужое состояние. Практический контракт: `load_draft_session` возвращает только registration states; существующий `load_draft` остаётся совместимым для request/edit. `clear_registration_draft` удаляет только state из множества состояний регистрации (`STEPS` + `preview`), а обычный `clear_draft` остаётся точным удалением для текущих edit/request-потоков. Все регистрационные `save` используют `save_draft_session` с allowlist-состояниями. Внедрить это различие до общей очистки `/cancel`, чтобы отмена регистрации не ломала request/edit-состояние.

## Публикация и приватность

Незавершённые данные лежат исключительно в JSON с FK на владельца. Не создавать временный `profiles(status='draft')`: текущие `candidate_pool`, `candidate_view`, `stats`, `count_available` работают по `profiles` и их условиям. Черновик не записывается в `profiles`, `profile_languages`, `profile_games` и не увеличивает счётчики. Публикация создаёт активный/видимый профиль и дочерние записи только после проверки обязательных полей; транзакция удаляет registration session в том же commit. Сохранить существующие условия колоды, порядок, скоринг и политику видимости без изменений.

## Матрица тестов

Предлагаемые имена тестов (адаптировать к текущим test-модулям). Проверять наблюдаемое поведение и persisted DB, не детали SQL:

### Сохранение после четырёх стирающих путей

- `test_registration_cancel_button_preserves_draft_and_step`
- `test_cmd_cancel_preserves_draft_and_returns_menu`
- `test_cmd_menu_preserves_draft_and_exposes_resume`
- `test_registration_start_with_existing_draft_does_not_overwrite_it`

Для каждого: пройти как минимум до `games`/`bio`, выйти указанным способом, перечитать Store, проверить state и все ранее введённые поля неизменны; затем нажать продолжение и получить тот же шаг. Проверить, что только явный restart очищает поля.

### Возврат / экран

- `test_registration_resume_survives_store_reopen_process_restart`
- `test_start_with_draft_shows_resume_and_restart_choices`
- `test_resume_callback_renders_saved_step`
- `test_restart_confirmation_cancel_keeps_original_draft`
- `test_restart_confirmation_resets_draft_to_gender`
- `test_menu_with_draft_has_resume_action_without_auto_start`
- `test_registered_user_never_sees_registration_resume`
- `test_registered_user_start_action_keeps_profile_and_edit_flow_intact`

Рестарт тестировать закрытием одного `Store` и созданием нового на том же SQLite-файле, а не mock-кешем. Проверить сохранение `consent_18` и отсутствие повторного consent.

### Ошибки, полнота, идемпотентность/гонки

- `test_invalid_age_keeps_draft_and_current_step`
- `test_invalid_city_keeps_draft_and_current_step`
- `test_invalid_games_keeps_draft_and_current_step`
- `test_invalid_bio_keeps_draft_and_current_step`
- `test_validation_attempts_are_capped_and_valid_answer_resets_them`
- `test_publish_missing_field_returns_first_missing_step_and_preserves_draft`
- `test_publish_defaults_legacy_search_mode_and_gender_pref`
- `test_duplicate_step_callback_does_not_advance_or_undo_twice`
- `test_stale_callback_loses_cas_and_renders_current_session`
- `test_parallel_draft_updates_only_one_cas_wins`
- `test_duplicate_publish_creates_one_profile_and_one_publish_event`
- `test_registration_callbacks_do_not_mutate_registered_profile`

### TTL и совместимость

- `test_draft_is_resumable_before_30_day_ttl`
- `test_draft_expires_at_30_day_boundary`
- `test_invalid_updated_at_expires_draft_safely`
- `test_expiry_delete_does_not_remove_concurrently_refreshed_draft`
- `test_unknown_state_normalizes_to_first_missing_valid_step`
- `test_unknown_state_with_complete_draft_normalizes_to_preview`
- `test_legacy_search_state_with_bio_resumes_at_photo`
- `test_legacy_draft_without_search_mode_resumes_at_search_and_preserves_fields`
- `test_legacy_optional_city_and_photo_do_not_block_publish`
- `test_known_state_with_missing_required_field_repairs_to_that_step`

### Изоляция колоды и регресс

- `test_unpublished_registration_draft_creates_no_profile_or_profile_children`
- `test_unpublished_draft_is_absent_from_candidate_pool_and_counts`
- `test_published_profile_is_visible_through_existing_pool_rules`
- `test_request_and_profile_edit_drafts_are_not_cleared_by_registration_resume_or_cancel`
- `test_existing_135_tests_remain_green`

Проверять колоду на обычном query path; не менять ни фильтры, ни скоринг для прохождения этих тестов. Перед объединением прогнать полный набор (`./.venv/Scripts/python.exe -m pytest -q`) и убедиться, что существующие 135 тестов остаются зелёными вместе с новыми.
