"""Регистрация: пошаговый визард. Чистые функции + состояние в registration_sessions.

Состояние хранится в БД, поэтому шаг не теряется ни при рестарте бота,
ни при возврате пользователя через день.
"""
from __future__ import annotations

from typing import Optional

import config
from db.store import DraftSession, Store, age_window
from domain.validators import (validate_age, validate_bio, validate_city, validate_games,
                               validate_languages)
from ui.screens import (Screen, mark_selected, screen_preview, screen_reg_step,
                              screen_registration_confirm_restart, screen_registration_exit,
                              screen_registration_resume, screen_need_profile, screen_welcome)

STEPS: list[dict] = [
    {"key": "gender", "title": "Пол", "input": "buttons"},
    {"key": "age", "title": "Возраст", "input": "text"},
    {"key": "country", "title": "Страна", "input": "buttons"},
    {"key": "city", "title": "Город", "input": "text"},
    {"key": "languages", "title": "Языки", "input": "multi"},
    {"key": "games", "title": "Игры", "input": "text"},
    {"key": "search", "title": "Как искать напарников", "input": "buttons"},
    {"key": "bio", "title": "О себе", "input": "text"},
    {"key": "photo", "title": "Картинка", "input": "photo"},
]
TOTAL = len(STEPS)
STEP_BY_KEY = {s["key"]: i + 1 for i, s in enumerate(STEPS)}
CONSENT_EVENT = "consent_18"


def has_consent(store: Store, user_id: int) -> bool:
    """Подтверждение 18+ и правил — разовое, хранится событием (схему менять не нужно)."""
    return store.has_event(user_id, CONSENT_EVENT)


def give_consent(store: Store, user_id: int) -> None:
    if not has_consent(store, user_id):
        store.log_event(user_id, CONSENT_EVENT)


STEP_TITLES = {s["key"]: s["title"] for s in STEPS}


def next_required_state(draft: dict) -> str:
    """Первый шаг, который ещё не заполнен; 'preview', если анкета готова.

    Город и картинка пропускаемые: считаются завершёнными, когда шаг пройден
    (даже с пустым значением — человек явно нажал «Пропустить»).
    """
    if not draft.get("gender"):
        return "gender"
    if not draft.get("age"):
        return "age"
    if not draft.get("country"):
        return "country"
    if "city" not in draft:
        return "city"
    if not draft.get("languages"):
        return "languages"
    if not draft.get("games"):
        return "games"
    if not draft.get("search_configured") and not draft.get("bio"):
        return "search"
    if not draft.get("bio"):
        return "bio"
    if "photo_file_id" not in draft:
        return "photo"
    return "preview"


def normalize_draft(state: str, draft: dict) -> tuple[str, dict]:
    """Приводит черновик к валидному шагу. Устаревшее/неизвестное состояние
    переводит на первый незаполненный шаг, не выдумывая ответы."""
    if state == "search" and draft.get("bio") and not draft.get("search_configured"):
        # Старый визард сразу спрашивал биографию: bio уже есть, значит шаг поиска
        # пройден в прежнем виде — идём к картинке, а не обратно в настройки поиска.
        return "photo", draft
    if state == "preview":
        if next_required_state(draft) == "preview":
            return state, draft
        return next_required_state(draft), draft
    if state in STEP_BY_KEY:
        return state, draft
    return next_required_state(draft), draft


def _progress_summary(draft: dict) -> str:
    filled: list[str] = []
    if draft.get("gender"):
        filled.append("пол")
    if draft.get("age"):
        filled.append("возраст")
    if draft.get("country"):
        filled.append("страну")
    if draft.get("city"):
        filled.append("город")
    if draft.get("languages"):
        filled.append("языки")
    if draft.get("games"):
        filled.append("игры")
    if draft.get("bio"):
        filled.append("текст карточки")
    return "Заполнено: " + ", ".join(filled) if filled else ""


# ── навигация по шагам ───────────────────────────────────────────────────────
def start(store: Store, user_id: int) -> Screen:
    """Вход в визард: продолжает начатую анкету, а не начинает заново."""
    if store.load_draft_session(user_id):
        return resume_screen(store, user_id)
    store.restart_draft(user_id)
    return step_screen(store, user_id)


def restart(store: Store, user_id: int) -> Screen:
    """Явный сброс — только по подтверждённой кнопке «Начать заново»."""
    store.restart_draft(user_id)
    return step_screen(store, user_id)


def reset(store: Store, user_id: int) -> Screen:
    return restart(store, user_id)


def resume_screen(store: Store, user_id: int) -> Screen:
    """Экран продолжения: есть незаконченная анкета — предлагаем продолжить."""
    session = store.load_draft_session(user_id)
    if session is None:
        return screen_welcome()
    state, _ = normalize_draft(session.state, session.draft)
    return screen_registration_resume(STEP_BY_KEY.get(state, 1), TOTAL,
                                      STEP_TITLES.get(state, ""),
                                      _progress_summary(session.draft))


def exit_wizard(store: Store, user_id: int) -> Screen:
    """Выход из визарда с сохранением прогресса (не стирает черновик)."""
    session = store.load_draft_session(user_id)
    if session is None:
        return screen_need_profile()
    state, _ = normalize_draft(session.state, session.draft)
    return screen_registration_exit(STEP_BY_KEY.get(state, 1), TOTAL,
                                    STEP_TITLES.get(state, ""))


def step_screen(store: Store, user_id: int) -> Screen:
    """Текущий шаг черновика; неизвестное состояние чинится до ближайшего шага."""
    session = store.load_draft_session(user_id)
    if session is None:
        return _render_step("gender", {"languages": [], "games": []})
    state, draft = normalize_draft(session.state, session.draft)
    if (state, draft) != (session.state, session.draft):
        store.save_draft_session(user_id, state, draft, session.attempts,
                                 expected_revision=session.revision)
    return _render_step(state, draft)


def _render_step(state: str, draft: dict) -> Screen:
    step = STEP_BY_KEY[state]
    if state == "gender":
        return screen_reg_step(step, TOTAL, "Пол",
                               "Кого ищем в отряд? Это только для поиска и не влияет ни на что другое.",
                               [[(label, f"onb:gender:{code}")] for code, label in config.GENDERS.items()])
    if state == "age":
        return screen_reg_step(step, TOTAL, "Возраст",
                               f"Сколько тебе лет? Только {config.MIN_AGE}+. Напиши числом.",
                               [[("🛑 Отмена", "onb:cancel")]])
    if state == "country":
        items = list(config.COUNTRIES.items())
        rows = [[(label, f"onb:country:{code}")] for code, label in items[:6]]
        rows.append([(label, f"onb:country:{code}") for code, label in items[6:8]])
        rows.append([(label, f"onb:country:{code}") for code, label in items[8:10]])
        rows.append([(label, f"onb:country:{code}") for code, label in items[10:]])
        rows.append([("🛑 Отмена", "onb:cancel")])
        return screen_reg_step(step, TOTAL, "Страна",
                               "Выбери страну. Она нужна для часового пояса и языка общения.",
                               rows)
    if state == "city":
        return screen_reg_step(step, TOTAL, "Город",
                               "Напиши свой город текстом. Точный адрес не показываем — только город.",
                               [[("⏭️ Пропустить", "onb:city_skip")], [("🛑 Отмена", "onb:cancel")]])
    if state == "languages":
        rows: list[list[tuple[str, str]]] = []
        chosen = draft.get("languages", [])
        line: list[tuple[str, str]] = []
        for code, label in config.LANGUAGES.items():
            mark = "✅ " if code in chosen else ""
            line.append((f"{mark}{label}", f"onb:lang:{code}"))
            if len(line) == 2:
                rows.append(line)
                line = []
        if line:
            rows.append(line)
        rows.append([("✅ Готово", "onb:lang_done")])
        return screen_reg_step(step, TOTAL, "Языки",
                               f"На каком языке общаться? Можно выбрать до {config.MAX_LANGUAGES}. "
                               "Так ты найдёшь игроков из разных стран с общим языком.",
                               rows)
    if state == "games":
        return screen_reg_step(step, TOTAL, "Игры",
                               f"Во что играешь? До {config.MAX_GAMES} игр через запятую,\n"
                               "например: Valorant, Dota 2, Helldivers 2.",
                               [[("🛑 Отмена", "onb:cancel")]])
    if state == "search":
        mode = draft.get("search_mode") or "city"
        known = config.game_genres(draft.get("games") or [])
        has_city = bool((draft.get("city") or "").strip())
        notes: list[str] = []
        if mode == "city" and not has_city:
            notes.append("Город не указан — география в поиске учитываться не будет.")
        if mode == "genre":
            notes.append("Выбран жанр: " + ", ".join(sorted(known)) if known
                         else "Жанр не определён: поиск пойдёт по играм.")
        prompt = "Выбери режим: по полу и городу или по жанру игр и полу. Возраст — ±5 лет."
        pref = draft.get("gender_pref") or "any"
        rows = [[(mark_selected("\U0001f4cd Пол и город", mode == "city"), "onb:search_mode:city"),
                 (mark_selected("\U0001f3ae Жанр и пол", mode == "genre"), "onb:search_mode:genre")],
                [(mark_selected("Любой пол", pref == "any"), "onb:gender_pref:any"),
                 (mark_selected("Мой пол", pref == "same"), "onb:gender_pref:same"),
                 (mark_selected("Противоположный", pref == "other"), "onb:gender_pref:other")],
                [("✅ Готово", "onb:search_done")], [("🛑 Отмена", "onb:cancel")]]
        note = "\n".join(notes)
        return screen_reg_step(step, TOTAL, "Как искать напарников", prompt, rows, note=note)
    if state == "bio":
        return screen_reg_step(step, TOTAL, "О себе",
                               "Напиши 1–3 строки: стиль игры, роль, когда бываешь онлайн,\n"
                               "отношение к новичкам. Так тебя выберут быстрее.",
                               [[("🛑 Отмена", "onb:cancel")]])
    return screen_reg_step(step, TOTAL, "Картинка",
                           "Пришли фото или игровой арт одним сообщением.\n"
                           "Реальное фото не обязательно — подойдёт любая картинка.",
                           [[("⏭️ Без картинки", "onb:photo_skip")], [("🛑 Отмена", "onb:cancel")]])


def advance(store: Store, user_id: int, next_state: str, draft: dict,
            session: Optional[DraftSession] = None) -> Screen:
    """Переход на следующий шаг с сохранением. Успешный ответ сбрасывает счётчик ошибок."""
    if session is not None:
        store.save_draft_session(user_id, next_state, draft, attempts=0,
                                 expected_revision=session.revision)
    else:
        store.save_draft_session(user_id, next_state, draft, attempts=0)
    return _render_step(next_state, draft)


# ── кнопки визарда ───────────────────────────────────────────────────────────
def on_button(store: Store, user_id: int, action: str, value: Optional[str] = None) -> Screen:
    session = store.load_draft_session(user_id)
    state = session.state if session else ""
    draft = dict(session.draft) if session else {}
    rev = session.revision if session else None

    if action == "resume":
        return step_screen(store, user_id)
    if action == "restart":
        return screen_registration_confirm_restart()
    if action == "restart_confirm":
        return restart(store, user_id)
    if action == "restart_keep":
        return resume_screen(store, user_id)

    if action == "gender" and value in config.GENDERS:
        draft["gender"] = value
        return advance(store, user_id, "age", draft, session)

    if action == "search_mode" and value in {"city", "genre"}:
        draft["search_mode"] = value
        store.save_draft_session(user_id, "search", draft, 0, expected_revision=rev)
        return _render_step("search", draft)

    if action == "gender_pref" and value in {"any", "same", "other"}:
        draft["gender_pref"] = value
        store.save_draft_session(user_id, "search", draft, 0, expected_revision=rev)
        return _render_step("search", draft)

    if action == "search_done" and state == "search":
        draft.setdefault("search_mode", "city")
        draft.setdefault("gender_pref", "any")
        draft["search_configured"] = True
        return advance(store, user_id, "bio", draft, session)

    if action == "country" and value in config.COUNTRIES:
        draft["country"] = value
        return advance(store, user_id, "city", draft, session)

    if action == "city_skip":
        draft["city"] = None
        return advance(store, user_id, "languages", draft, session)

    if action == "lang" and value in config.LANGUAGES:
        chosen = list(draft.get("languages") or [])
        if value in chosen:
            chosen.remove(value)
        elif len(chosen) < config.MAX_LANGUAGES:
            chosen.append(value)
        draft["languages"] = chosen
        store.save_draft_session(user_id, "languages", draft, 0, expected_revision=rev)
        return _render_step("languages", draft)

    if action == "lang_done":
        ok, result = validate_languages(list(draft.get("languages") or []))
        if not ok:
            return screen_reg_step(STEP_BY_KEY["languages"], TOTAL, "Языки", str(result),
                                   _lang_rows(draft))
        draft["languages"] = result
        return advance(store, user_id, "games", draft, session)

    if action == "photo_skip":
        draft.setdefault("search_mode", "city")
        draft.setdefault("gender_pref", "any")
        draft["photo_file_id"] = None
        store.save_draft_session(user_id, "preview", draft, 0, expected_revision=rev)
        return screen_preview(draft)

    return _render_step(state if state in STEP_BY_KEY else "gender", draft)


def _lang_rows(draft: dict) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    line: list[tuple[str, str]] = []
    chosen = draft.get("languages", [])
    for code, label in config.LANGUAGES.items():
        mark = "✅ " if code in chosen else ""
        line.append((f"{mark}{label}", f"onb:lang:{code}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([("✅ Готово", "onb:lang_done")])
    return rows


# ── текстовый ввод ───────────────────────────────────────────────────────────
def on_text(store: Store, user_id: int, text: str) -> Optional[Screen]:
    """Возвращает экран, если состояние ждёт текст; иначе None (обработает catch-all)."""
    session = store.load_draft_session(user_id)
    if session is None:
        return None
    state = session.state
    if state not in STEP_BY_KEY:
        return None
    draft = dict(session.draft)
    rev = session.revision

    if state == "age":
        ok, result = validate_age(text)
        if not ok:
            store.save_draft_session(user_id, state, draft, session.attempts + 1,
                                     expected_revision=rev)
            return screen_reg_step(STEP_BY_KEY[state], TOTAL, "Возраст", str(result),
                                   [[("🛑 Отмена", "onb:cancel")]])
        draft["age"] = result
        return advance(store, user_id, "country", draft, session)

    if state == "city":
        ok, result = validate_city(text)
        if not ok:
            store.save_draft_session(user_id, state, draft, session.attempts + 1,
                                     expected_revision=rev)
            return screen_reg_step(STEP_BY_KEY[state], TOTAL, "Город", str(result),
                                   [[("⏭️ Пропустить", "onb:city_skip")], [("🛑 Отмена", "onb:cancel")]])
        draft["city"] = result
        return advance(store, user_id, "languages", draft, session)

    if state == "games":
        ok, result = validate_games(text)
        if not ok:
            store.save_draft_session(user_id, state, draft, session.attempts + 1,
                                     expected_revision=rev)
            return screen_reg_step(STEP_BY_KEY[state], TOTAL, "Игры", str(result),
                                   [[("🛑 Отмена", "onb:cancel")]])
        draft["games"] = result
        return advance(store, user_id, "search", draft, session)

    if state == "search":
        # Совместимость с ранее начатыми визардами: прежний экран сразу спрашивал биографию.
        ok, result = validate_bio(text)
        if ok:
            draft["bio"] = result
            # Ответ текстом = согласие с показанными по умолчанию параметрами поиска.
            # Без этого окно ±5 лет и режим поиска молча не применялись бы.
            draft.setdefault("search_mode", "city")
            draft.setdefault("gender_pref", "any")
            draft["search_configured"] = True
            return advance(store, user_id, "photo", draft, session)
        return _render_step("search", draft)

    if state == "bio":
        ok, result = validate_bio(text)
        if not ok:
            store.save_draft_session(user_id, state, draft, session.attempts + 1,
                                     expected_revision=rev)
            return screen_reg_step(STEP_BY_KEY[state], TOTAL, "О себе", str(result),
                                   [[("🛑 Отмена", "onb:cancel")]])
        draft["bio"] = result
        return advance(store, user_id, "photo", draft, session)

    return None


def on_photo(store: Store, user_id: int, file_id: str) -> Optional[Screen]:
    session = store.load_draft_session(user_id)
    if session is None or session.state != "photo":
        return None
    draft = dict(session.draft)
    draft["photo_file_id"] = file_id
    draft.setdefault("search_mode", "city")
    draft.setdefault("gender_pref", "any")
    store.save_draft_session(user_id, "preview", draft, 0, expected_revision=session.revision)
    return screen_preview(draft)


def publish(store: Store, user_id: int) -> tuple[Screen, bool]:
    """Публикует анкету. Возвращает экран и признак успеха.

    Если чего-то не хватает, черновик сохраняется, а человека отправляют
    на первый незаполненный шаг — начинать заново не нужно.
    """
    session = store.load_draft_session(user_id)
    if session is None:
        return screen_need_profile(), False
    draft = dict(session.draft)
    # Публикация всегда идёт из визарда: значит окно ±5 лет и режим поиска обязаны примениться,
    # даже если человек прошёл шаг поиска нажатием «Готово» или печатным ответом.
    draft["registration_wizard"] = True
    missing = next_required_state(draft)
    if missing != "preview":
        title = STEP_TITLES.get(missing, "Анкета")
        return screen_reg_step(STEP_BY_KEY[missing], TOTAL, title,
                               f"Анкета заполнена не полностью — продолжим с шага «{title}».",
                               [[("▶️ Продолжить", "onb:resume")]]), False
    if store.is_registered(user_id):
        # Анкета уже опубликована: повторная публикация не создаёт вторую.
        return screen_need_profile(), False
    store.create_profile(user_id, draft, publish=True)
    store.clear_draft(user_id)
    store.log_event(user_id, "profile_published")
    return _published_screen(), True


def _published_screen() -> Screen:
    from ui.screens import box, TITLE
    lines = [
        "🚀 Анкета в эфире.",
        "",
        "Теперь тебя видят подходящие игроки, а ты можешь смотреть карточки.",
        "Колода показывает по одной анкете — жми ❤️, если хочешь собрать пати.",
    ]
    return Screen(box(TITLE, lines), [[("🛰️ Искать напарника", "deck:next")],
                                      [("👤 Моя анкета", "menu:profile")]])
