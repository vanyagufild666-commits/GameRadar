# PartyRadar: отметки выбранных настроек и заполнение анкеты

Статус: дизайн-контракт для реализации основным агентом.

Цель документа: согласовать поведение UI и чистых функций для трёх изменений:

1. отмечать выбранное предпочтение пола на шаге «Как искать напарников»;
2. отмечать активное значение в каждой группе фильтров;
3. считать полноту анкеты по обязательным данным визарда и показывать разбивку.

Кодовые callback-значения не меняются. Документ не требует изменений схемы SQLite.

## 1. Общие правила отметки

### 1.1. Единый вид

Единственный вид отметки выбранного пункта — суффикс ` ✅`, отделённый от
текста одним пробелом. Не смешивать его с существующим языковым префиксом
`✅ `: в изменяемых группах поиска и фильтров используется только суффикс.

Примеры:

- `📍 Пол и город ✅`;
- `Мой пол ✅`;
- `👤 Свой ✅`;
- `🗣️ Общий язык ✅`.

Существующий текст кнопки «✅ Готово» не является отметкой выбора и остаётся
как есть. Это команда завершения шага, а не элемент группы выбора.

Вспомогательный форматтер должен быть чистым и идемпотентным:

```python
def mark_selected(label: str, selected: bool) -> str:
    """Return label with one trailing `` ✅`` when selected."""
```

Форматтер не должен добавлять две отметки при повторном рендере. Для этого
либо вызывающий код передаёт исходную подпись, либо форматтер сначала удаляет
один известный суффикс.

### 1.2. Визард: предпочтение пола

В `handlers/registration.py`, в `_render_step(state == "search")`, выбранным
считается значение `draft.get("gender_pref")` после безопасного default
`"any"`. Ровно одна из трёх кнопок получает суффикс:

| Значение | Кнопка | Callback без изменений |
|---|---|---|
| `any` | `Любой пол ✅` | `onb:gender_pref:any` |
| `same` | `Мой пол ✅` | `onb:gender_pref:same` |
| `other` | `Противоположный ✅` | `onb:gender_pref:other` |

Если в старом draft значение отсутствует или неизвестно, отображается и
считается выбранным `any`; при подтверждении поиска текущая логика сохраняет
`gender_pref = "any"`. Не отмечать несколько вариантов и не делать выбор
зависимым от `search_mode`.

Режим поиска в этом же экране уже использует тот же суффикс; после изменения
у режима и пола должен быть единый стиль. Ряды и callback-data остаются:

```text
onb:search_mode:city
onb:search_mode:genre
onb:gender_pref:any
onb:gender_pref:same
onb:gender_pref:other
onb:search_done
onb:cancel
```

### 1.3. Фильтры: логические группы

`screen_filters` рендерит отметку независимо для следующих групп:

1. режим: ровно один из `city`, `genre`;
2. предпочтение пола: ровно один из `any`, `same`, `other`;
3. возраст: один canonical preset или точный custom-вариант, если он есть в
   кнопочном ряду;
4. география: ровно один из `any`, `country`, `city`;
5. общий язык: отметка только у включённого toggle;
6. общая игра: отметка только у включённого toggle.

Для toggle нет отдельной callback-кнопки «выключить»: нажатие той же кнопки
переключает значение. Поэтому при `0` отметки нет, при `1` подпись получает
суффикс. Это не нарушение правила «ровно один вариант»: toggle — одно
бинарное состояние, у которого отмечается только активное состояние.

Кнопки, уже существующие в коде, сохраняют callback-data буквально:

```text
filters:mode:city
filters:mode:genre
filters:gender:any
filters:gender:same
filters:gender:other
filters:window:5
filters:window:10
filters:window:all
filters:age:18:24
filters:age:18:99
filters:geo:any
filters:geo:city
filters:lang
filters:game
```

Для корректного отображения старого допустимого состояния
`geo_mode == "country"` добавить в географический ряд подпись `Моя страна` с
callback `filters:geo:country`. Это добавление не изменяет ни одного
существующего callback-data; обработчик `_filters` уже принимает `country`.
Если продукт намеренно не показывает этот вариант, legacy `country` всё равно
нельзя тихо показывать как `any`: экран должен отрисовать активную страну
отдельной кнопкой или явно нормализовать состояние отдельным согласованным
миграционным решением.

## 2. Возрастные пресеты и дубль 18–99

### 2.1. Источник возраста

Для вычисления `±5` и `±10` нужен возраст владельца анкеты. `flt` его не
содержит, поэтому вызывающий код получает `prof["age"]` и передаёт его в
`screen_filters`. Рамки вычисляются с теми же границами, что и фильтрация:

```python
age_window(age: int) -> tuple[int, int]       # текущая доменная функция, ±5
window_for(age: int, delta: int) -> tuple[int, int]
# low = max(config.MIN_AGE, age - delta)
# high = min(config.MAX_AGE, age + delta)
```

`±5` и `±10` считаются выбранными только при точном совпадении `min_age` и
`max_age` с вычисленной парой. Нельзя отмечать `±5` по приблизительному
пересечению диапазонов.

`18–99` в ряду `filters:window:*` — canonical preset «весь допустимый
диапазон», то есть точное совпадение с `(config.MIN_AGE, config.MAX_AGE)`.

### 2.2. Разрешение двух кнопок 18–99

В текущем экране две разные callback-кнопки сохраняют одинаковый смысл:

- `filters:window:all` — preset «весь диапазон»;
- `filters:age:18:99` — custom-ряд с теми же границами.

В `profile_filters` сохраняются только `min_age` и `max_age`, поэтому после
записи нельзя узнать, какой из двух callback был нажат. Различать их в UI
после факта невозможно и не следует пытаться через скрытые эвристики.

Canonical-правило:

- если диапазон равен `(MIN_AGE, MAX_AGE)`, он всегда считается preset `all`;
- отметка ставится только на кнопку `filters:window:all`;
- одноимённая кнопка `filters:age:18:99` в custom-ряду не получает отметку;
- подписи в двух рядах должны быть различимыми, например `18–99` для
  preset и `Свой: 18–99` для custom, но callback-data не меняются;
- после любого рендера состояние остаётся детерминированным, даже если
  старые данные были получены через `filters:age:18:99`.

Это намеренное разрешение дубля: один диапазон — один canonical выбор и одна
зелёная галочка.

### 2.3. Произвольный custom-диапазон

Custom считается точным совпадением с кнопкой только если обе границы равны
границам этой кнопки и диапазон не был уже классифицирован как preset.
Например:

| `min_age`, `max_age` | Отметка |
|---|---|
| `18, 24` | на custom-кнопке `filters:age:18:24`, если это не совпадает с `±5`, `±10` или `all` для текущего возраста |
| `20, 30` | ни на одной возрастной кнопке: это допустимый custom-диапазон без подходящего варианта |
| `18, 99` | только на `filters:window:all`, custom-дубль не отмечать |
| возраст 25, диапазон `20, 30` | на `filters:window:5`, потому что это точное вычисленное `±5` |

Порядок классификации строго такой:

1. `±5`;
2. `±10`;
3. `all`;
4. exact custom button (`18–24` или custom label для диапазона);
5. произвольный custom без отметки на кнопках.

Чтобы активный произвольный диапазон всё равно был понятен, строка сводки
должна показывать `Возраст: 20–30 (свой диапазон)`. Это не добавляет вторую
галочку и не создаёт ложного совпадения. В тестах для custom `18–24` нужно
проверить либо отметку exact custom, либо отсутствие отметки, если диапазон
совпал с canonical preset; для `20–30` — отсутствие отметки на возрастных
кнопках и сохранность диапазона в сводке.

## 3. Контракты чистых функций

Типы ниже описывают публичный контракт UI-модуля. Имена dataclass можно
реализовать в `ui/screens.py`; внешних зависимостей и Telegram API у них нет.

### 3.1. Фильтры

Совместимый новый контракт:

```python
def screen_filters(
    flt: Mapping[str, Any],
    available: int,
    viewer_age: int | None = None,
) -> Screen:
    """Render filters and selected marks.

    ``viewer_age`` is required for reliable ±5/±10 classification. ``None``
    is accepted only for old callers: ±5/±10 buttons are then unmarked unless
    the state can be classified without the viewer age (all/custom).
    """
```

`flt` содержит минимум:

```text
min_age: int
max_age: int
geo_mode: "any" | "country" | "city"
require_common_language: int | bool
require_common_game: int | bool
only_active_recent: int | bool
search_mode: "city" | "genre"
gender_pref: "any" | "same" | "other"
```

Старые позиционные вызовы `screen_filters(flt, available)` должны продолжить
работать. Обработчик `_filters` всегда передаёт возраст, когда профиль есть:

```python
prof = _profile(store, user_id)
flt = store.get_filters(prof["id"])
return Result(screen_filters(
    flt,
    store.count_available(user_id),
    viewer_age=prof.get("age"),
))
```

Порядок действий в `_filters` сохраняется: применить callback, перечитать
`flt`, затем рендерить. `available` считается после изменения фильтра.

Локальные правила рендера:

- label строится из базовой подписи и `mark_selected`;
- в каждой небинарной группе не более одной кнопки с ` ✅`;
- все callback-data остаются в allowlist и укладываются в 64 байта;
- текст `Screen.text` остаётся не длиннее 4096 символов.

### 3.2. Отчёт заполнения

Текущий `_fill` возвращает число, но число не позволяет объяснить пользователю
причину неполноты. Заменить его на типизированный отчёт:

```python
@dataclass(frozen=True)
class FillItem:
    key: str
    label: str
    points: int
    complete: bool
    detail: str

@dataclass(frozen=True)
class FillReport:
    progress: int
    required: tuple[FillItem, ...]
    optional: tuple[FillItem, ...]


def _fill(
    prof: Mapping[str, Any],
    langs: Sequence[str],
    games: Sequence[str],
) -> FillReport:
    """Calculate required completeness and its user-facing breakdown."""
```

`progress` — сумма `points` только завершённых required-items, ограниченная
диапазоном 0..100. `optional` не влияет на `progress`; это отдельный список
улучшений карточки. Сумма required points обязана быть 100.

Обязательная матрица и веса:

| key | label | points | complete, если |
|---|---|---:|---|
| `gender` | Пол | 15 | `prof["gender"]` — допустимое значение, включая осознанный `"x"` («не указывать»), если оно разрешено визардом |
| `age` | Возраст | 15 | целое значение в `config.MIN_AGE..config.MAX_AGE` |
| `country` | Страна | 15 | непустой допустимый `country_code` |
| `languages` | Языки | 15 | `langs` содержит хотя бы один допустимый язык |
| `games` | Игры | 15 | `games` содержит хотя бы одну игру |
| `bio` | О себе | 25 | непустой валидный текст; прохождение визарда считается завершённым независимо от достижения quality-порога 80 символов |

Это отражает обязательность текущего визарда, а не желательное качество
профиля. В частности, 1 игра и короткая, но прошедшая текущую валидацию
биография закрывают соответствующие required-items полностью.

Optional-items:

| key | label | points | complete, если |
|---|---|---:|---|
| `photo` | Картинка | 0 | есть `photo_file_id` |
| `city` | Город | 0 | непустой `city` |
| `games_quality` | Три и более игры | 0 | `len(games) >= 3` |
| `bio_quality` | Подробная биография | 0 | длина bio не меньше 80 символов |

Optional-items имеют нулевой вес намеренно: их отсутствие не уменьшает
завершённость обязательного визарда. Их `detail` используется для подсказок,
например `Можно добавить картинку` или `Город не указан`.

Не включать в `_fill` `search_mode`, `gender_pref`, `only_active_recent` и
другие фильтры: это настройки выдачи, а не обязательные публичные поля
анкеты; для них уже есть безопасные defaults. Если позже продукт решит
считать настройки поиска частью анкеты, это будет отдельное изменение общего
веса и отдельные миграционные тесты, а не скрытая поправка текущего процента.

### 3.3. Экран прогресса

Для нового кода:

```python
def screen_progress(
    shown: int,
    likes_out: int,
    likes_in: int,
    matches: int,
    requests_in: int,
    streak: int,
    level: str,
    fill: FillReport | int,
) -> Screen:
    """Render activity metrics and profile-completeness breakdown."""
```

`FillReport` — основной путь. Тип `int` сохраняется как compatibility-вход для
старых тестов и внешних вызовов: он показывает старую строку процента без
разбивки либо создаёт минимальную legacy-строку. Новый handler никогда не
передаёт туда голое число.

`handlers/callbacks.py`, ветка `_menu(..., tail=["progress"])`, получает
`langs`, `games`, строит `fill = _fill(prof, langs, games)` и передаёт именно
`fill` в `screen_progress`:

```python
fill = _fill(prof, langs, games)
return Result(screen_progress(
    store.views_made(user_id),
    store.likes_given(user_id),
    store.likes_received(user_id),
    store.matches_count(user_id),
    store.requests_received(user_id),
    store.streak(user_id),
    store.level(user_id),
    fill,
))
```

После строки `Заполнение анкеты: N%` экран показывает компактную разбивку:

```text
Обязательные поля:
✅ Пол — 15/15
✅ Возраст — 15/15
⚠️ Языки — 0/15: выбери хотя бы один язык

Можно улучшить:
○ Картинка
○ Город
```

Для завершённого визарда без картинки и города:

```text
Заполнение анкеты: 100%
Обязательные поля: 100/100
✅ Пол  ✅ Возраст  ✅ Страна  ✅ Языки  ✅ Игры  ✅ О себе
Можно улучшить: картинка, город
```

Фактические эмодзи и существующий стиль экранов сохраняются при реализации;
пример выше описывает семантику, а не требует изменения callback-контрактов.
Разбивка не должна раскрывать внутренние имена `key`, только локализованные
`label` и полезный `detail`. Экран обязан укладываться в 4096 символов; при
добавлении новых пунктов использовать компактную строку и ограниченный
fallback, а не обрезать процент или обязательные причины.

## 4. Точки реализации

Код не изменяется этим документом. Реализация должна быть ограничена такими
местами:

1. `ui/screens.py`, `_fill` (текущий блок примерно 310–318): добавить
   `FillItem`, `FillReport`, новый расчёт required/optional и `mark_selected`
   либо локальный эквивалент.
2. `ui/screens.py`, `screen_filters` (примерно 471–498): принять
   `viewer_age`, вычислить canonical age choice, добавить суффиксы всем
   активным группам и различить подписи двух 18–99.
3. `ui/screens.py`, `screen_progress` (примерно 501–515): принять
   `FillReport | int`, отрисовать процент и required/optional breakdown.
4. `handlers/registration.py`, `_render_step` (примерно 207–224): добавить
   суффикс к выбранным `gender_pref`, сохранив defaults и callbacks.
5. `handlers/callbacks.py`, `_filters` (примерно 405–434): передать
   `prof.get("age")` в `screen_filters`; после callback перечитать `flt` как
   сейчас.
6. `handlers/callbacks.py`, `_menu` progress (примерно 134–144): передать
   `_fill(prof, langs, games)` как отчёт, а не только integer.
7. `tests/test_screens.py`: обновить fixture-вызовы только для новых
   assertions; старый вызов с integer для `screen_progress` оставить как
   compatibility-проверку.
8. Добавить отдельные тесты в `tests/test_ui_marks.py` и
   `tests/test_fill_progress.py` либо в существующие тематические модули.
9. `handlers/callbacks.py` allowlist и маршрутизация не менять, кроме
   использования уже поддержанного `filters:geo:country`, если добавляется
   кнопка страны. `db/schema.sql` и `db/store.py` для этого дизайна не меняются.

Не менять callback-data существующих кнопок, порядок переходов визарда,
семантику фильтров, SQL-подсчёт `available` или правило `Screen` как чистого
значения.

## 5. Матрица тестов

### 5.1. Отметки визарда

| Тест | Подготовка | Ожидание |
|---|---|---|
| `test_wizard_search_marks_any_gender_preference` | Draft на `search`, `gender_pref="any"` | ровно `Любой пол ✅`; `same` и `other` без суффикса |
| `test_wizard_search_marks_same_gender_preference` | Draft с `same` | ровно `Мой пол ✅` |
| `test_wizard_search_marks_other_gender_preference` | Draft с `other` | ровно `Противоположный ✅` |
| `test_wizard_search_defaults_missing_gender_preference_to_any` | Draft без `gender_pref` | отмечен `any`, callback-data не изменены |
| `test_wizard_search_uses_same_mark_style_for_mode_and_gender` | Draft с `genre` и `other` | обе выбранные кнопки имеют суффикс ` ✅`, не префикс; в каждой группе ровно одна |

Проверять подписи через `screen.rows`, а не только наличие текста во всём
экране, чтобы одна отметка не маскировала две.

### 5.2. Все группы фильтров

| Тест | Подготовка | Ожидание |
|---|---|---|
| `test_filters_marks_selected_city_mode` | `search_mode="city"` | только `filters:mode:city` отмечен |
| `test_filters_marks_selected_genre_mode` | `search_mode="genre"` | только `filters:mode:genre` отмечен |
| `test_filters_marks_one_gender_preference` | по очереди `any`, `same`, `other` | в gender-ряду ровно одна отметка и она соответствует значению |
| `test_filters_marks_age_window_using_viewer_age` | возраст 25, диапазон 20–30 | отмечен `filters:window:5`; `±10` и custom без отметки |
| `test_filters_marks_clamped_age_window` | возраст 20, `MIN_AGE=18`, диапазон 18–25 | отмечен только `±5`, если это вычисленный диапазон |
| `test_filters_marks_all_age_preset_once` | `18..99` | отмечен только `filters:window:all`, custom 18–99 не отмечен |
| `test_filters_marks_exact_custom_18_24` | диапазон 18..24, не совпадающий с preset | отмечен custom `filters:age:18:24` |
| `test_filters_leaves_arbitrary_custom_age_unmarked` | диапазон 20..30, ни один preset/custom button не равен ему | возрастные кнопки без отметки; сводка явно показывает `20–30` и свой диапазон |
| `test_filters_marks_selected_geo_any_city_country` | по очереди `any`, `city`, `country` | ровно одна geo-отметка; country виден для legacy/поддержанного callback |
| `test_filters_marks_language_toggle_only_when_enabled` | `require_common_language` 0 и 1 | при 0 без отметки, при 1 отметка на language toggle |
| `test_filters_marks_game_toggle_only_when_enabled` | `require_common_game` 0 и 1 | при 0 без отметки, при 1 отметка на game toggle |
| `test_filters_preserves_all_existing_callback_data` | собрать все actions экрана | все перечисленные legacy callbacks присутствуют без изменения строк |
| `test_filters_screen_stays_within_telegram_limits` | крайние значения текста/фильтров | `text <= 4096`, labels и callback bytes в существующих лимитах |

Для тестов «ровно одна» считать только подписи соответствующего ряда. Для
бинарных toggle проверять одно активное состояние, а не требовать отметку при
выключенном значении.

### 5.3. Процент и разбивка

| Тест | Подготовка | Ожидание |
|---|---|---|
| `test_fill_full_wizard_profile_without_photo_or_city_is_100` | валидны gender, age, country, >=1 language, >=1 game, валидный bio; photo/city отсутствуют | `FillReport.progress == 100`; все required complete; photo/city только optional |
| `test_fill_does_not_treat_gender_x_as_missing` | полный профиль с `gender="x"` | gender complete; профиль не теряет 15 баллов |
| `test_fill_reports_each_missing_required_field` | по одному удалить каждое обязательное поле | соответствующий item incomplete, progress уменьшается ровно на его points, detail объясняет причину |
| `test_fill_accepts_one_game_as_complete_wizard_field` | одна валидная игра | games 15/15; `games_quality` optional incomplete |
| `test_fill_separates_short_bio_from_missing_bio` | bio проходит визард, но короче 80 | bio required complete; `bio_quality` optional incomplete |
| `test_fill_optional_photo_and_city_never_reduce_progress` | добавить/удалить photo и city | required progress одинаков; меняются только optional statuses |
| `test_progress_screen_renders_fill_breakdown` | передать `FillReport` с complete/incomplete items | видны процент, `N/100`, причины незаполненных required и optional подсказки |
| `test_progress_screen_keeps_integer_compatibility` | передать `70` как старый вызов | экран не падает и содержит `70%`; новый handler integer не использует |
| `test_progress_screen_stays_within_telegram_limit` | максимальная разбивка | `text <= 4096`, кнопка меню сохранена |
| `test_menu_progress_reads_profile_languages_games` | зарегистрированный профиль и `menu:progress` | вызывается новый отчёт; экран показывает реальную разбивку, не только число |
| `test_missing_profile_progress_still_returns_need_profile` | пользователь без профиля | сохраняется текущий `screen_need_profile` |

### 5.4. Регрессии и приёмка

1. Выполнить весь существующий набор: `./.venv/Scripts/python.exe -m pytest -q`.
2. Приёмочный критерий текущей базы: все 154 старых теста зелёные.
3. Проверить отдельно существующие контракты `filters:mode:city` и
   `filters:window:all` из `test_audit_search_modes.py` и `test_flow.py`.
4. Проверить полный визард без города и фото через фактический `Store`, затем
   открыть `menu:progress`; результат должен быть 100%, а не 30%.
5. Проверить профиль с неполным legacy-данными: процент ниже 100 должен
   сопровождаться конкретными required-items, а не только голым числом.
6. Проверить все тексты и callback-data статически: Unicode replacement
   character `U+FFFD` не появляется в кодовых файлах, callback-data не
   нормализуются и не заменяются при добавлении отметок.

## 6. Границы решения

- Фото и город остаются полезными, но необязательными для публикации и не
  уменьшают показатель обязательной заполненности.
- Количество игр и длина биографии дают optional quality-подсказки, но не
  превращают успешно пройденные шаги визарда в «неполные».
- Поиск по полу/городу или жанру и предпочтение пола имеют собственные
  отметки; они не влияют на процент профиля.
- Возрастные preset-ы вычисляются из возраста владельца анкеты, а не из
  возраста кандидатов и не из текста summary.
- Две кнопки с одинаковыми границами `18–99` не получают две отметки: данные
  canonical-известны только как диапазон, поэтому выбран preset `all`.
- Любое последующее изменение weights или состава required-items требует
  отдельного дизайн-решения и обновления ожидаемых значений тестов; нельзя
  возвращать частичный старый расчёт только ради совместимости с числом 30%.
