# PartyRadar — техническая спецификация (gpt-6-sol)

## matching_algorithm

Модель выдачи — циклическая колода. Для каждой пары viewer → candidate хранится независимое состояние deck_state, а каждое фактическое предъявление фиксируется в view_events. Один cycle_no означает один полный проход по доступной на момент начала колоде.

1. Базовый пул кандидатов:
- candidate_user_id не равен viewer_user_id;
- profile.status = active и is_visible = 1;
- нет взаимной блокировки в user_blocks;
- нет active match;
- профиль не забанен и не просрочен по авто-паузе;
- возраст попадает в [min_age, max_age];
- при require_common_language = 1 есть пересечение profile_languages viewer и candidate;
- при require_common_game = 1 есть пересечение profile_games viewer и candidate;
- при geo_mode = country совпадает country_code;
- при geo_mode = city совпадает country_code и нормализованный город;
- кандидат не находится под действующим cooldown: next_eligible_at IS NULL OR next_eligible_at <= now.

2. Приоритеты сортировки:
- взаимная блокировка и active match исключаются до сортировки;
- новые анкеты с published_at за последние 48 часов получают +25 баллов, но не более 30 процентов выдачи;
- активность: +20 * exp(-age_hours / 72), максимум 20;
- общий язык: +20 за первый общий язык и +5 за каждый следующий, максимум 30;
- общая игра: +15 за первую общую игру и +4 за каждую следующую, максимум 27;
- география: +10 за город, +5 за страну;
- совпадение возрастного диапазона: +8;
- профиль с качественно заполненной карточкой и фото: +5;
- штраф за предыдущие показы в текущем цикле: -30, если карточка уже была показана в этом cycle_no;
- штраф за много пропусков: -min(skip_count * 4, 20);
- штраф за повторное предъявление в последние 24 часа: -50;
- случайный детерминированный tie-breaker от hash(viewer_id, candidate_id, cycle_no, daily_salt), чтобы одинаковые карточки не застревали наверху.

3. Кулдауны:
- карточка показана, но пользователь не нажал действие в течение 5 минут: 6 часов;
- явный пропуск: base_skip = 24 часа;
- повторный пропуск: min(24 часа * 2^(skip_count - 1), 30 суток);
- открытие профиля без решения: 12 часов;
- лайк без взаимного лайка: 7 суток, после чего карточка может вернуться с понижением -15; новый лайк не создаёт вторую строку, а обновляет существующую;
- mutual like: deck_state помечается last_action = mutual, next_eligible_at = NULL, is_exhausted = 1; пара исключается до закрытия match;
- unlike не возвращает карточку немедленно: действует cooldown 24 часа, чтобы не было циклического like/unlike.

4. Антизацикливание:
- один и тот же candidate не может быть показан повторно в том же цикле;
- после каждого ответа предыдущая карточка атомарно получает action и cooldown;
- сервер не отдаёт кандидатуру из последних 5 минут, даже если старое состояние повреждено;
- если осталось меньше 3 кандидатов, выбирается только лучший новый кандидат, но не разрешается показывать одного и того же кандидата два раза подряд;
- если за последние 5 минут не осталось допустимой карточки, показывается экран exhausted, а не повтор последнего профиля;
- кнопка Второй проход доступна только после исчерпания текущего цикла;
- второй проход увеличивает cycle_no и не стирает view_events, поэтому статистика и лимит повторов сохраняются;
- ручной сброс cooldown не должен отменять блокировки, match и исключения по moderation.

5. Ранжирование при малой базе:
- меньше 50 профилей: фильтры применяются строго, затем выдаются все подходящие; интерфейс показывает количество доступных карточек;
- меньше 10: если строгий фильтр дал пустой результат, предлагается ослабить только необязательные фильтры: сначала город, затем страна, затем common_game; обязательный возраст и язык не ослабляются без подтверждения;
- всего 2 пользователя: каждый видит второго, если нет block, match и нарушений, даже при отсутствии общей игры, потому что require_common_game по умолчанию выключен;
- новая анкета получает exploration boost на 48 часов, но максимум один показ одному viewer за 24 часа;
- неактивная анкета после inactivity_days = 30 автоматически получает status = paused, is_visible = 0 и запись profile_pauses; любое действие пользователя в боте обновляет last_active_at и не включает анкету без явного подтверждения;
- если пользователь пересмотрел всю базу, сначала возвращаются карточки с истёкшим cooldown, затем запускается второй проход; при полном отсутствии результата показывается честный экран с кнопками изменить фильтры, включить второй проход и пригласить друзей;
- cooldowns не сбрасываются через 5 минут: 5 минут — только debounce и перевод незавершённого показа в expired; массовый reset разрешён не чаще одного раза в 7 суток либо явно подтверждается пользователем.

Псевдокод выдачи:

function next_card(viewer_id, now):
    BEGIN IMMEDIATE
    ensure_active_profile(viewer_id)
    finalize_stale_impression(viewer_id, now, stale_after=5_minutes)
    current = get_current_open_card(viewer_id)
    if current exists:
        COMMIT
        return current

    filters = load_filters(viewer_id)
    cycle = get_or_create_current_cycle(viewer_id)
    candidates = SQL candidate_pool(viewer_id, filters, now)
    candidates = candidates where not blocked(viewer_id, candidate_id)
    candidates = candidates where not active_match(viewer_id, candidate_id)
    candidates = candidates where eligible_by_cooldown(viewer_id, candidate_id, now)
    candidates = candidates where not shown_in_cycle(candidate_id, cycle)
    candidates = candidates where candidate_id != last_served_candidate(viewer_id)

    if candidates is empty:
        candidates = SQL candidate_pool(viewer_id, filters, now)
        candidates = candidates where cooldown_expired(now)
        candidates = candidates where candidate_id != last_served_candidate(viewer_id)

    if candidates is empty and explicit_second_pass_requested(viewer_id):
        cycle = start_new_cycle(viewer_id)
        candidates = SQL candidate_pool(viewer_id, filters, now)
        candidates = candidates where not mutual_match_or_blocked
        candidates = candidates where not shown_in_cycle(candidate_id, cycle)

    if candidates is empty:
        mark_cycle_exhausted(viewer_id, cycle, now)
        COMMIT
        return EXHAUSTED

    score every candidate using freshness, activity, common language, games, geo, age, repeat penalties
    selected = weighted_top_k(candidates, k=5, exploration_ratio=0.30)[0]
    insert view_events(viewer_id, selected, cycle, 'shown', now)
    upsert deck_state with impression_count + 1, last_shown_at = now
    mark deck session last_served_at = now
    COMMIT
    return selected

Псевдокод ответа на карточку:

function card_action(viewer_id, candidate_id, action):
    BEGIN IMMEDIATE
    verify_open_view(viewer_id, candidate_id)
    if action == 'like':
        upsert likes(viewer_id, candidate_id, active)
        if exists active likes(candidate_id, viewer_id):
            insert or reactivate matches(min(viewer_id,candidate_id), max(...))
            update both deck states to last_action='mutual', is_exhausted=1
            insert view event action='mutual'
            enqueue mutual_notification for both users
        else:
            set cooldown 7 days and like_pending_until = now + 7 days
            insert view event action='like'
    if action == 'skip':
        skip_count += 1
        cooldown = min(24h * 2^(skip_count - 1), 30d)
        set next_eligible_at = now + cooldown
        insert view event action='skip'
    if action == 'pause':
        update profile status='paused', is_visible=0
        insert profile_pauses reason='manual'
    COMMIT
    return next_card(viewer_id, now)

## edge_cases

1. Меньше 50 анкет: не показывать пустой экран только потому, что целевой размер выдачи равен 50. Вернуть все подходящие карточки с обычными cooldown.
2. Меньше 10 анкет: показать точное количество доступных профилей и предложить ослабить необязательные фильтры. Нельзя автоматически ослаблять возраст, блокировки, баны и язык, если язык отмечен обязательным.
3. Всего 2 пользователя: каждый может видеть второго при выполнении базовых условий. При skip применяется обычный cooldown, после его окончания карточка возвращается.
4. Новая анкета: она видна только после успешной валидации, подтверждения согласия и публикации. Новому пользователю не показывать самого себя и не считать собственную регистрацию просмотром.
5. Профиль без действий после показа: через 5 минут событие становится expired, профиль получает 6-часовой cooldown, а следующая выдача не повторяет его немедленно.
6. Пользователь пересмотрел всё: показать exhausted с причинами: все анкеты под cooldown, нет соответствия фильтрам, все пары уже matched или заблокированы. Дать изменить фильтры и запустить второй проход.
7. Авто-пауза неактивных: после 30 дней без входа или действия status = paused, is_visible = 0. Уведомление о паузе отправляется при следующем запуске бота. Возобновление только явной кнопкой.
8. Разные фильтры: результат считается по фильтрам viewer, а не по фильтрам candidate. Если нужен взаимный фильтр, включается отдельная настройка strict_mutual_filters и тогда проверяются оба набора.
9. Изменение анкеты: история просмотров не удаляется. card_revision меняется, текущая карточка закрывается, новый текст не должен мгновенно сбросить cooldown.
10. Повторный like: уникальность from_user_id/to_user_id предотвращает дубли. Если reciprocal like появился позже, создаётся один match.
11. Mutual like и block одновременно: block имеет приоритет; match переводится в blocked, уведомление о контакте не отправляется.
12. Удаление или бан пользователя: профиль исключается из выдачи, открытые заявки переводятся в blocked, активные matches закрываются, статистика обезличивается.
13. Нажатие старой inline-кнопки: сервер проверяет актуальность состояния, не выполняет действие повторно и перерисовывает текущий экран.
14. Два параллельных нажатия: BEGIN IMMEDIATE плюс проверка открытого view и idempotency key не позволяют дважды создать like, match или заявку.
15. Заблокированный ботом пользователь: bot_blocked = 1 после ошибки Telegram Forbidden; фоновые уведомления прекращаются, профиль можно оставить невидимым до возврата пользователя.
16. Нет username: контакт раскрывается только через разрешённый Telegram mention/deep link после consent. Номер телефона не запрашивается автоматически.
17. Большой текст, неподдерживаемое медиа или повторный файл: заявка отклоняется до записи либо получает moderation_status = manual_review; отправитель видит конкретное ограничение.
18. Кулдаун сброшен вручную: reset не трогает view_events, blocks, reports, matches и pending moderation; увеличивается cycle_no и применяется штраф повторного показа.
19. Второй проход: доступен после exhausted, increment cycle_no, сохраняет всю историю. Для каждого candidate действует защита от показа чаще одного раза за 24 часа, если нет явного reset.
20. Деградация SQLite: включить WAL, foreign_keys, busy_timeout и короткие транзакции. При locked повторить операцию с backoff, но не повторять внешнюю отправку Telegram без idempotency key.
21. Некорректный callback: неизвестный namespace, слишком длинное значение, нечисловой ID или ID вне допустимого диапазона отклоняются без исключения Python и записываются в rate-limited security log.

## registration_fsm

Состояние хранится в registration_sessions. Все переходы выполняются в одной транзакции: проверить ввод, обновить draft_json, изменить state. Сессия истекает через 24 часа; повторный /start продолжает незавершённую регистрацию.

Состояния:
START → CONSENT_18 → GENDER → AGE → COUNTRY → LANGUAGES → CITY → GAMES → BIO → PHOTO → PREVIEW → PUBLISHED.
Для редактирования используется EDIT_MENU → EDIT_FIELD_* → PREVIEW.

1. CONSENT_18:
- текст: Анкеты доступны только пользователям 18+;
- кнопки reg:age:yes и reg:cancel;
- без явного подтверждения профиль не создаётся и не показывается;
- пользователь младше 18 лет или отказавшийся получает отказ без сохранения анкеты.

2. GENDER:
- кнопки: male, female, nonbinary, prefer_not_to_say;
- допускается callback и текстовый вариант с нормализацией;
- любое другое значение повторяет шаг с понятной ошибкой;
- значение не используется как обязательный фильтр, если отдельная настройка не добавлена.

3. AGE:
- принимается целое число 18–120;
- дроби, отрицательные числа, даты рождения, пустое значение и текст отклоняются;
- хранится только возраст, не дата рождения, чтобы уменьшить объём персональных данных;
- после сохранения age нельзя понизить ниже 18 без повторного прохождения проверки.

4. COUNTRY:
- предпочтительно inline-кнопки популярных стран и callback country:XX;
- ручной ввод нормализуется в ISO 3166-1 alpha-2 через локальный справочник;
- неизвестное значение отклоняется, исходный текст не публикуется;
- country_code обязателен.

5. LANGUAGES:
- язык общения поддерживает несколько значений;
- минимум один, максимум пять языков;
- кнопки lang:add:ru, lang:add:en и lang:done;
- повторный язык не добавляется;
- варианты хранятся в profile_languages, один язык может быть primary;
- кнопка Далее недоступна, пока список пуст.

6. CITY:
- свободный текст 1–80 символов после trim;
- запрещаются управляющие символы, ссылки и чрезмерное повторение символов;
- можно выбрать Не указывать; тогда city = NULL;
- публично показывается только город, точный адрес запрещён.

7. GAMES:
- минимум одна, максимум десять игр;
- кнопки каталога и ручное добавление;
- ручное название 1–80 символов, Unicode нормализуется, регистр приводится к нижнему виду для уникальности;
- profile_games хранит нормализованное название, для UI сохраняется display-вариант при необходимости;
- кнопка games:done завершает шаг.

8. BIO:
- 1–1000 символов после trim;
- запрещаются Telegram-ссылки, телефоны, email и массовые рекламные шаблоны на этапе первичной модерации;
- Unicode control characters удаляются;
- пустой текст запрещён.

9. PHOTO:
- принимается только Message.photo или при необходимости изображение как document с MIME image/*;
- сохраняется photo[-1].file_id и file_unique_id, файл не скачивается на VPS;
- размер, MIME и наличие фото проверяются Telegram API;
- текст, видео, стикер и голосовое сообщение отклоняются с подсказкой отправить фото;
- фотография обязательна для публикации; замена доступна в редактировании.

10. PREVIEW:
- бот показывает полную карточку и inline-кнопки edit:gender, edit:age, edit:country, edit:languages, edit:city, edit:games, edit:bio, edit:photo, reg:publish;
- перед публикацией пользователь подтверждает согласие на обработку и показ данных другим пользователям;
- publish атомарно создаёт или обновляет profiles, языки, игры, filters и статус active.

11. EDIT после регистрации:
- /edit или кнопка menu:edit открывает EDIT_MENU;
- изменение каждого поля запускает тот же валидатор, что и регистрация;
- после изменения увеличивается card_revision, updated_at и last_active_at;
- смена языка, игр, страны или возраста не удаляет историю показов, но текущий открытый candidate закрывается как expired;
- удаление фотографии запрещено, пока не загружена новая;
- кнопка edit:pause переводит профиль в paused/is_visible=0, edit:resume возвращает active после проверки moderation_status.

Ошибки ввода не меняют состояние и не создают частичную публичную анкету. После пяти ошибок на одном шаге требуется повторное нажатие кнопки или начинается cooldown на 30 секунд против спама.

## handlers

Структура обработчиков для python-telegram-bot v21:

- CommandHandler('start', start_handler): создаёт users, восстанавливает registration_sessions или показывает menu:home.
- CommandHandler('help', help_handler): экран help:main.
- CommandHandler('cancel', cancel_handler): отменяет текущий ввод, не удаляя опубликованный профиль.
- CallbackQueryHandler(callback_router): единый роутер для всех callback_data; callback обязательно answer() даже при ошибке.
- MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler): маршрутизация по registration_sessions.state и message compose-состоянию.
- MessageHandler(filters.PHOTO, photo_input_handler): регистрационное фото или вложение message request.
- MessageHandler(filters.VIDEO, video_input_handler): вложение заявки.
- MessageHandler(filters.VIDEO_NOTE, video_note_input_handler): вложение видео-кружка.
- MessageHandler(filters.CONTACT, contact_handler): только добровольная передача Telegram contact после mutual match; номер не сохраняется без отдельного согласия.
- JobQueue: auto_pause_job, expire_message_requests_job, finalize_stale_views_job, cleanup_registration_sessions_job, moderation_digest_job.

Экраны и примеры callback_data. Формат плоский, ASCII, разделитель colon, все значения ограничены whitelist или числовым ID. Максимальная длина любой строки проверяется перед отправкой и должна быть не более 64 байт.

Главное меню:
- menu:home
- menu:deck
- menu:edit
- menu:filters
- menu:matches
- menu:inbox
- menu:settings
- menu:help

Регистрация:
- reg:start
- reg:age:yes
- reg:cancel
- reg:gender:male
- reg:gender:female
- reg:gender:prefer
- reg:country:RU
- reg:lang:add:ru
- reg:lang:done
- reg:city:skip
- reg:game:add:cs2
- reg:games:done
- reg:bio:done
- reg:photo:skip is forbidden for publication but may be used in draft
- reg:preview
- reg:publish

Карточка:
- card:next
- card:like:4821
- card:skip:4821
- card:open:4821
- card:message:4821
- card:report:4821
- card:block:4821
- card:pause
- card:second-pass
- card:reset-confirm

Фильтры:
- filter:age
- filter:lang
- filter:game
- filter:geo:any
- filter:geo:country
- filter:geo:city
- filter:save
- filter:reset

Редактирование и видимость:
- edit:menu
- edit:gender
- edit:age
- edit:country
- edit:languages
- edit:city
- edit:games
- edit:bio
- edit:photo
- edit:pause
- edit:resume
- edit:save

Обмен и модерация:
- msg:text:4821
- msg:photo:4821
- msg:video:4821
- msg:circle:4821
- msg:send:913
- msg:accept:913
- msg:reject:913
- report:spam:4821
- report:underage:4821
- report:harass:4821
- report:other:4821
- block:confirm:4821
- block:cancel

Inbox и match:
- inbox:open:913
- inbox:accept:913
- inbox:reject:913
- match:open:77
- match:close:77

callback_router никогда не доверяет ID из callback_data: проверяет принадлежность запроса текущему пользователю, доступность объекта и актуальность состояния; устаревшие callback возвращают короткое сообщение и перерисовывают экран.

## message_exchange

Рекомендуемая модель — вариант B: анонимная заявка через бота с согласием адресата.

Кнопка Написать / фото / видео / кружок не может напрямую открыть произвольный composer Telegram из callback query. После нажатия бот показывает экран выбора:
- msg:text:USER_ID — включить режим ожидания следующего текстового сообщения;
- msg:photo:USER_ID — ожидать Message.photo;
- msg:video:USER_ID — ожидать Message.video;
- msg:circle:USER_ID — ожидать Message.video_note.

Состояние ожидания хранится в контексте пользователя с TTL 10 минут. Любое полученное сообщение валидируется, записывается как message_requests и message_attachments, после чего исходное сообщение не пересылается напрямую до решения адресата.

Вариант B, anonymous request:
1. Отправитель может создать заявку только если профиль адресата активен, нет block и не достигнут rate limit.
2. Если нет mutual match, requires_consent = 1. Даже при наличии mutual match можно оставить подтверждение включённым по настройке пользователя.
3. Получатель получает карточку заявки без номера телефона и без раскрытия Telegram ID отправителя: тип контента, текст или медиа, кнопки inbox:accept и inbox:reject.
4. При reject заявка закрывается, отправителю отправляется нейтральное уведомление без причины.
5. При accept бот раскрывает обоим профильный контакт: Telegram username, если он есть, и кнопку с tg://user?id=... либо inline mention. Сам бот не может писать от имени пользователя и не гарантирует открытие чата, если Telegram-клиент ограничивает deep link.
6. Для медиа бот использует сохранённый file_id этого же бота: send_photo, send_video или send_video_note. Нельзя передавать file_id между разными ботами.
7. После accept медиа можно отправить в личный диалог обоим пользователям; telegram_message_id сохраняется для аудита и удаления по жалобе.
8. Заявка истекает через 72 часа. Повторная заявка от той же пары разрешается не чаще одного раза в 24 часа.
9. Для видео и фото выполняются проверка MIME, размера, типа Telegram и лимитов; для ручной модерации содержимое не скачивается автоматически без отдельного согласованного процесса хранения.

Вариант A, прямой контакт только после взаимного лайка:
1. При like в обе стороны создаётся matches.
2. Бот уведомляет обоих и показывает кнопку Написать напрямую.
3. Контакт раскрывается только после mutual like; телефонная карточка Telegram Contact запрашивается отдельной ReplyKeyboardButton(request_contact=True) и только добровольно.
4. Бот не может отправить сообщение в чат как пользователь, поэтому прямой диалог открывается через username или tg://user?id=...; username может отсутствовать или измениться.
5. Медиа не проходит через бота, пользователь отправляет его сам в Telegram.

Сравнение: вариант A проще и лучше защищает приватность, но не позволяет писать человеку без mutual like и зависит от наличия username или корректной работы deep link. Вариант B лучше соответствует кнопке Написать на карточке, даёт контроль согласия, антиспам и модерацию, поэтому рекомендуется как основной. Mutual like может автоматически переводить заявку в режим ускоренного accept, но не должен раскрывать номер телефона без отдельного consent.

## moderation

Возраст и безопасность:
- обязательное подтверждение 18+ при регистрации;
- возрастная декларация не является полноценной верификацией личности, поэтому жалоба underage немедленно скрывает профиль до ручной проверки;
- подозрительные массовые регистрации, одинаковые тексты, частая смена профиля и жалобы повышают risk_score;
- при risk_score выше порога профиль временно paused и попадает в moderation queue.

Жалобы и блокировки:
- пользователь может пожаловаться на spam, underage, harassment, sexual_content, scam, hate, fake_profile или other;
- после собственной жалобы карточка скрывается для заявителя немедленно;
- block действует независимо от результата жалобы и исключает пользователя из выдачи в обеих направлениях;
- три уникальные жалобы от разных пользователей за 24 часа дают временную паузу до решения модератора;
- жалобы одного и того же пользователя дедуплицируются, но не теряются в аудите;
- модераторские действия пишутся в moderation_actions, без удаления исходных отчётов.

Антиспам:
- максимум 30 показов карточек за 10 минут и 300 за сутки на пользователя;
- максимум 10 новых message_requests за час и 30 за сутки;
- максимум 3 заявки одной и той же паре за 24 часа;
- максимум 5 жалоб за час;
- одинаковый текст или одинаковый file_unique_id в адрес одного получателя блокируется на 24 часа;
- превышение лимита возвращает retry_after и не создаёт запись заявки;
- все операции like, skip, message request и report идемпотентны через уникальные ключи и транзакции SQLite.

Авторизация:
- telegram_user_id берётся только из Update.effective_user, ID из callback_data не считается доказательством личности;
- каждое действие проверяет владельца объекта и доступ к match/request;
- административные команды доступны только allowlist moderator Telegram IDs;
- BOT_TOKEN хранится только в .env, не логируется и не попадает в git;
- логи не должны содержать полные тексты анкет, file_id, телефоны и токены; для трассировки используются внутренние числовые ID.

ФЗ-152 и приватность:
- Telegram ID, username, имя, фото, город, возраст, язык, игры и тексты считаются персональными данными или могут позволить идентификацию;
- до публикации требуется информированное согласие на обработку и показ анкеты другим пользователям;
- в privacy policy указываются оператор, цели, состав данных, сроки хранения, основания обработки, порядок удаления и контакты оператора;
- собираются только данные, необходимые для поиска напарников; дата рождения, точный адрес и телефон по умолчанию не хранятся;
- пользователь должен иметь команды удаления анкеты и данных, выгрузки основных данных и отзыва согласия;
- удаление выполняется каскадно или через обезличивание статистики, резервные копии удаляются по сроку хранения;
- доступ к SQLite ограничивается учётной записью процесса, резервные копии шифруются, секреты не хранятся рядом с базой;
- для пользователей из РФ необходимо отдельно проверить требования к оператору ПДн, локализации первичной базы, трансграничной передаче и уведомлению Роскомнадзора с юристом; это не заменяет юридическое заключение.

## ddl

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    telegram_username TEXT,
    first_name TEXT,
    is_bot_blocked INTEGER NOT NULL DEFAULT 0 CHECK (is_bot_blocked IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
);

CREATE TABLE profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    gender TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age BETWEEN 18 AND 120),
    country_code TEXT NOT NULL,
    city TEXT,
    bio TEXT NOT NULL CHECK (length(bio) BETWEEN 1 AND 1000),
    photo_file_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'paused', 'banned', 'deleted')),
    is_visible INTEGER NOT NULL DEFAULT 1 CHECK (is_visible IN (0, 1)),
    paused_until TEXT,
    paused_reason TEXT,
    auto_paused_at TEXT,
    last_active_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    card_revision INTEGER NOT NULL DEFAULT 1,
    CHECK ((status = 'active' AND is_visible = 1) OR status <> 'active')
);

CREATE TABLE profile_languages (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    language_code TEXT NOT NULL CHECK (length(language_code) BETWEEN 2 AND 10),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    PRIMARY KEY (profile_id, language_code)
);

CREATE TABLE profile_games (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    game_name TEXT NOT NULL CHECK (length(game_name) BETWEEN 1 AND 80),
    PRIMARY KEY (profile_id, game_name)
);

CREATE TABLE profile_filters (
    profile_id INTEGER PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    min_age INTEGER NOT NULL DEFAULT 18 CHECK (min_age BETWEEN 18 AND 120),
    max_age INTEGER NOT NULL DEFAULT 120 CHECK (max_age BETWEEN 18 AND 120),
    geo_mode TEXT NOT NULL DEFAULT 'any' CHECK (geo_mode IN ('any', 'country', 'city')),
    require_common_language INTEGER NOT NULL DEFAULT 1 CHECK (require_common_language IN (0, 1)),
    require_common_game INTEGER NOT NULL DEFAULT 0 CHECK (require_common_game IN (0, 1)),
    only_active_recent INTEGER NOT NULL DEFAULT 0 CHECK (only_active_recent IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (min_age <= max_age)
);

CREATE TABLE filter_languages (
    profile_id INTEGER NOT NULL REFERENCES profile_filters(profile_id) ON DELETE CASCADE,
    language_code TEXT NOT NULL,
    PRIMARY KEY (profile_id, language_code)
);

CREATE TABLE filter_games (
    profile_id INTEGER NOT NULL REFERENCES profile_filters(profile_id) ON DELETE CASCADE,
    game_name TEXT NOT NULL,
    PRIMARY KEY (profile_id, game_name)
);

CREATE TABLE filter_countries (
    profile_id INTEGER NOT NULL REFERENCES profile_filters(profile_id) ON DELETE CASCADE,
    country_code TEXT NOT NULL,
    PRIMARY KEY (profile_id, country_code)
);

CREATE TABLE likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (from_user_id, to_user_id),
    CHECK (from_user_id <> to_user_id)
);

CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_a_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_b_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed', 'blocked')),
    matched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    CHECK (user_a_id < user_b_id),
    UNIQUE (user_a_id, user_b_id)
);

CREATE TABLE view_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    candidate_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cycle_no INTEGER NOT NULL DEFAULT 1,
    action TEXT NOT NULL CHECK (action IN ('shown', 'opened', 'skip', 'like', 'unlike', 'expired', 'mutual')),
    shown_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    action_at TEXT,
    cooldown_until TEXT,
    UNIQUE (viewer_user_id, candidate_user_id, cycle_no),
    CHECK (viewer_user_id <> candidate_user_id)
);

CREATE TABLE deck_state (
    viewer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    candidate_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cycle_no INTEGER NOT NULL DEFAULT 1,
    impression_count INTEGER NOT NULL DEFAULT 0,
    skip_count INTEGER NOT NULL DEFAULT 0,
    last_action TEXT CHECK (last_action IN ('shown', 'opened', 'skip', 'like', 'unlike', 'expired', 'mutual')),
    last_shown_at TEXT,
    last_action_at TEXT,
    next_eligible_at TEXT,
    like_pending_until TEXT,
    last_cycle_no INTEGER,
    is_exhausted INTEGER NOT NULL DEFAULT 0 CHECK (is_exhausted IN (0, 1)),
    PRIMARY KEY (viewer_user_id, candidate_user_id),
    CHECK (viewer_user_id <> candidate_user_id)
);

CREATE TABLE deck_sessions (
    viewer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cycle_no INTEGER NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_served_at TEXT,
    exhausted_at TEXT,
    reset_at TEXT,
    PRIMARY KEY (viewer_user_id, cycle_no)
);

CREATE TABLE profile_pauses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    reason TEXT NOT NULL CHECK (reason IN ('manual', 'auto_inactive', 'moderation', 'temporary')),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    note TEXT
);

CREATE TABLE user_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blocker_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    UNIQUE (blocker_user_id, blocked_user_id),
    CHECK (blocker_user_id <> blocked_user_id)
);

CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL CHECK (reason IN ('spam', 'underage', 'harassment', 'sexual_content', 'scam', 'hate', 'fake_profile', 'other')),
    comment TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewing', 'resolved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    UNIQUE (reporter_user_id, target_user_id, reason),
    CHECK (reporter_user_id <> target_user_id)
);

CREATE TABLE message_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    match_id INTEGER REFERENCES matches(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('text', 'photo', 'video', 'video_note', 'contact_request')),
    text_content TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'expired', 'blocked', 'delivered')),
    requires_consent INTEGER NOT NULL DEFAULT 1 CHECK (requires_consent IN (0, 1)),
    moderation_status TEXT NOT NULL DEFAULT 'not_checked' CHECK (moderation_status IN ('not_checked', 'allowed', 'rejected', 'manual_review')),
    idempotency_key TEXT NOT NULL UNIQUE,
    telegram_message_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    responded_at TEXT,
    CHECK (sender_user_id <> recipient_user_id),
    CHECK ((kind = 'text' AND text_content IS NOT NULL AND length(text_content) BETWEEN 1 AND 4000) OR kind <> 'text')
);

CREATE TABLE message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES message_requests(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL CHECK (media_type IN ('photo', 'video', 'video_note')),
    telegram_file_id TEXT NOT NULL,
    file_unique_id TEXT,
    caption TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE registration_sessions (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    draft_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT
);

CREATE TABLE moderation_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    moderator_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL CHECK (action IN ('warn', 'pause', 'ban', 'unban', 'delete_content', 'dismiss_report')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_profiles_candidate_pool ON profiles(status, is_visible, last_active_at, published_at);
CREATE INDEX idx_profiles_country_age ON profiles(country_code, age, status, is_visible);
CREATE INDEX idx_profiles_last_active ON profiles(last_active_at DESC);
CREATE INDEX idx_profile_languages_language ON profile_languages(language_code, profile_id);
CREATE INDEX idx_profile_games_game ON profile_games(game_name, profile_id);
CREATE INDEX idx_filter_languages_language ON filter_languages(language_code, profile_id);
CREATE INDEX idx_filter_games_game ON filter_games(game_name, profile_id);
CREATE INDEX idx_filter_countries_country ON filter_countries(country_code, profile_id);
CREATE INDEX idx_likes_to_user ON likes(to_user_id, status, created_at);
CREATE INDEX idx_likes_from_user ON likes(from_user_id, status, created_at);
CREATE INDEX idx_matches_user_a ON matches(user_a_id, status);
CREATE INDEX idx_matches_user_b ON matches(user_b_id, status);
CREATE INDEX idx_views_viewer_time ON view_events(viewer_user_id, shown_at DESC);
CREATE INDEX idx_views_candidate_time ON view_events(candidate_user_id, shown_at DESC);
CREATE INDEX idx_deck_eligible ON deck_state(viewer_user_id, next_eligible_at, cycle_no);
CREATE INDEX idx_deck_pending_likes ON deck_state(viewer_user_id, like_pending_until);
CREATE INDEX idx_pauses_profile_active ON profile_pauses(profile_id, ended_at);
CREATE INDEX idx_blocks_blocker ON user_blocks(blocker_user_id, blocked_user_id);
CREATE INDEX idx_blocks_blocked ON user_blocks(blocked_user_id, blocker_user_id);
CREATE INDEX idx_reports_status ON reports(status, created_at);
CREATE INDEX idx_reports_target ON reports(target_user_id, status);
CREATE INDEX idx_message_recipient_status ON message_requests(recipient_user_id, status, created_at);
CREATE INDEX idx_message_sender ON message_requests(sender_user_id, created_at);
CREATE INDEX idx_attachments_request ON message_attachments(request_id);
CREATE INDEX idx_moderation_target ON moderation_actions(target_user_id, created_at DESC);

CREATE UNIQUE INDEX uq_active_primary_language ON profile_languages(profile_id) WHERE is_primary = 1;
CREATE UNIQUE INDEX uq_active_match_pair ON matches(user_a_id, user_b_id) WHERE status = 'active';
CREATE UNIQUE INDEX uq_open_profile_pause ON profile_pauses(profile_id) WHERE ended_at IS NULL;
CREATE INDEX idx_active_reports_dedup ON reports(target_user_id, reporter_user_id, created_at);

## files

Ориентировочная структура проекта для Python 3.11 и python-telegram-bot v21:

- bot.py — создание Application, загрузка .env, регистрация handlers, запуск polling/webhook; 120–180 строк.
- config.py — настройки токена, лимитов, cooldown, inactivity_days, moderator IDs; 80–120 строк.
- db/connection.py — sqlite3 connection factory, WAL, foreign_keys, busy_timeout, транзакции; 100–140 строк.
- db/schema.sql — полный DDL и индексы; 220–300 строк.
- db/migrations.py — версия схемы и последовательные миграции; 120–180 строк.
- db/repositories/users.py — users и last_seen_at; 100–150 строк.
- db/repositories/profiles.py — создание, публикация, редактирование, пауза, удаление; 220–300 строк.
- db/repositories/filters.py — профильные фильтры, языки, игры, страны; 160–220 строк.
- db/repositories/deck.py — view_events, deck_state, deck_sessions, cooldown и циклы; 300–450 строк.
- db/repositories/likes.py — likes, reciprocal like и matches; 180–250 строк.
- db/repositories/moderation.py — blocks, reports, moderation_actions, ban/pause; 180–250 строк.
- db/repositories/messages.py — message_requests, attachments, idempotency и статусы; 220–320 строк.
- domain/deck.py — чистый scoring, фильтрация и выбор кандидата; 250–350 строк.
- domain/cooldowns.py — формулы кулдаунов, second pass и reset policy; 100–160 строк.
- domain/validators.py — валидация возраста, страны, языков, игр, bio и медиа; 180–260 строк.
- domain/matches.py — правила like, mutual match, block precedence; 120–180 строк.
- handlers/start.py — /start, /help, /cancel; 100–150 строк.
- handlers/registration.py — FSM регистрации, draft, preview, публикация; 350–500 строк.
- handlers/profile_edit.py — редактирование и пауза анкеты; 220–320 строк.
- handlers/deck.py — показ карточек, like, skip, report, block, second pass; 300–450 строк.
- handlers/filters.py — экран и сохранение фильтров; 180–250 строк.
- handlers/messages.py — режим ожидания текста/фото/видео/кружка и inbox; 350–500 строк.
- handlers/moderation.py — пользовательские жалобы, административный review; 220–320 строк.
- handlers/callbacks.py — единый parser callback_data, проверка namespace и длины; 140–220 строк.
- ui/keyboards.py — все InlineKeyboardMarkup и ReplyKeyboardMarkup; 250–350 строк.
- ui/renderers.py — render_home, render_card, render_preview, render_inbox и error screens; 300–450 строк.
- jobs/scheduler.py — регистрация JobQueue задач; 80–120 строк.
- jobs/maintenance.py — авто-пауза, очистка TTL, завершение stale views, статистика; 180–260 строк.
- services/telegram_media.py — безопасная отправка file_id, обработка Telegram exceptions; 120–180 строк.
- services/notifications.py — mutual match, заявки, cooldown и moderation уведомления; 150–220 строк.
- privacy_policy.md — текст согласий, удаления и обработки ПДн; 100–180 строк.
- .env.example — BOT_TOKEN и необязательные настройки; 10–20 строк.
- requirements.txt — python-telegram-bot>=21, python-dotenv>=1.0; 5–10 строк.
- tests/test_validators.py — boundary tests для полей регистрации; 180–260 строк.
- tests/test_deck.py — фильтры, score, cooldown, second pass, малые базы, anti-loop; 350–500 строк.
- tests/test_likes_matches.py — idempotency, reciprocal like, block precedence; 180–260 строк.
- tests/test_messages.py — consent, media types, TTL, rate limits; 220–320 строк.
- tests/test_handlers_smoke.py — callback routing, stale callbacks, render non-empty; 180–260 строк.
- tests/test_schema.py — включение foreign keys, ограничения и индексы; 120–180 строк.
- README.md — запуск Windows/VPS, миграции, .env, backup и модерация; 180–260 строк.

Итого ядро без тестов и документации: примерно 4 000–5 500 строк. С тестами, миграциями и UI-рендерами: примерно 6 000–8 000 строк. Базовую реализацию следует начинать с schema.sql, repositories/deck.py, domain/deck.py, registration FSM и интеграционных тестов SQLite; Telegram polling подключать после прохождения тестов доменной логики.
