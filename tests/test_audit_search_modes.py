"""Приёмка выбора параметров поиска — мои собственные тесты, не тесты автора фичи.

Проверяю по описанию задачи, а не по коду:
1. миграция реальной «старой» базы (колонок нет, данные есть);
2. режим «пол + город» действительно режет по городу;
3. режим «жанр игр + пол» режет по жанру и не режет по городу;
4. фильтр по полу работает в живом пути карточки;
5. окно возраста ±5 получают все, кто регистрируется через визард;
6. деградация, когда жанр не определился.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

import config
from db.store import Store
from handlers import callbacks as cb
from handlers import registration as reg
from tests.test_flow import _pid_from, register

TARGET = "СИГНАЛ"


@pytest.fixture()
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Store(path)
    yield s
    s.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def _wizard(store: Store, tg_id: int, username: str, age: int = 24, city: str = "Омск",
            games: str = "Valorant", gender: str = "m", mode: str = "city",
            pref: str = "any") -> dict:
    """Полный визард с прохождением шага выбора параметров поиска (как в живом чате)."""
    user = store.ensure_user(tg_id, username, username.capitalize())
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", gender)
    reg.on_text(store, user["id"], str(age))
    reg.on_button(store, user["id"], "country", "RU")
    reg.on_text(store, user["id"], city)
    reg.on_button(store, user["id"], "lang", "ru")
    reg.on_button(store, user["id"], "lang_done")
    reg.on_text(store, user["id"], games)
    reg.on_button(store, user["id"], "search_mode", mode)
    reg.on_button(store, user["id"], "gender_pref", pref)
    reg.on_button(store, user["id"], "search_done")
    reg.on_text(store, user["id"], "Играю вечерами, без токсичности, зовите в пати")
    reg.on_button(store, user["id"], "photo_skip")
    screen, published = reg.publish(store, user["id"])
    assert published, "визард с выбранными параметрами поиска должен публиковаться"
    return user


def _deck(store: Store, user: dict) -> list[dict]:
    """Выдача колоды: id профилей, попавших в карточки (по кнопкам лайка).

    Пропуск сам отдаёт следующую карточку, поэтому идём по цепочке ответов,
    иначе часть выдачи теряется.
    """
    seen: list[dict] = []
    result = cb.route(store, user["id"], "deck:next")
    for _ in range(8):
        if TARGET not in result.screen.text:
            break
        pid = int(_pid_from(result))
        seen.append(store.profile_by_id(pid))
        result = cb.route(store, user["id"], f"deck:skip:{pid}")
    return seen


# ── 1. миграция старой базы ──────────────────────────────────────────────────
def test_legacy_database_gets_new_columns_and_keeps_its_filters(store: Store):
    """База, созданная до фичи: колонок нет, данные есть."""
    author = register(store, 13001, "old", age=30, city="Омск")
    pid = store.profile_by_user(author["id"])["id"]
    # откатываем схему до состояния «до фичи»: колонки убираем, данные оставляем
    store.x("ALTER TABLE profile_filters DROP COLUMN search_mode")
    store.x("ALTER TABLE profile_filters DROP COLUMN gender_pref")
    store.x("UPDATE profile_filters SET min_age = 25, max_age = 35, geo_mode = 'country' WHERE profile_id = ?",
            (pid,))
    columns = {r["name"] for r in store.q("PRAGMA table_info(profile_filters)")}
    assert "search_mode" not in columns and "gender_pref" not in columns
    store.close()

    path = store.path if hasattr(store, "path") else None
    assert path
    reopened = Store(path)                      # тот же файл, теперь с миграцией
    try:
        columns = {r["name"] for r in reopened.q("PRAGMA table_info(profile_filters)")}
        assert {"search_mode", "gender_pref"} <= columns, "миграция добавила колонки"
        flt = reopened.get_filters(pid)
        assert (flt["search_mode"], flt["gender_pref"]) == ("city", "any"), "старые строки получили дефолт"
        assert (flt["min_age"], flt["max_age"], flt["geo_mode"]) == (25, 35, "country"), \
            "миграция не переписала прежние настройки"
        # и фильтры продолжают работать: анкета старого формата читается без падения
        assert reopened.count_available(author["id"]) >= 0
        assert cb.route(reopened, author["id"], "menu:filters").screen is not None
    finally:
        reopened.close()


# ── 2-3. режимы поиска в живом пути карточки ─────────────────────────────────
def test_city_mode_hides_other_cities_in_the_live_deck(store: Store):
    viewer = _wizard(store, 13101, "city_mode", mode="city")
    local = register(store, 13102, "local", city="Омск")
    faraway = register(store, 13103, "faraway", city="Томск")
    _wizard(store, 13104, "filler", city="Омск")

    served = {p["user_id"] for p in _deck(store, viewer)}
    assert local["id"] in served, "игрок из своего города должен показываться"
    assert faraway["id"] not in served, "режим «пол + город» не должен показывать другие города"


def test_genre_mode_gives_genre_neighbours_even_from_another_city(store: Store):
    viewer = _wizard(store, 13201, "genre_mode", mode="genre", games="Valorant")
    same_genre_other_city = register(store, 13202, "sniper", city="Томск", games="CS2")
    other_genre_same_city = register(store, 13203, "moba_fan", city="Омск", games="Dota 2")

    served = {p["user_id"] for p in _deck(store, viewer)}
    assert same_genre_other_city["id"] in served, "шутер из другого города — сосед по жанру"
    assert other_genre_same_city["id"] not in served, "другой жанр не показываем даже из своего города"


# ── 4. пол ───────────────────────────────────────────────────────────────────
def test_gender_preference_is_enforced_in_the_live_deck(store: Store):
    viewer = _wizard(store, 13301, "straight", gender="m", pref="other")
    girl = register(store, 13302, "girl", city="Омск")
    store.x("UPDATE profiles SET gender = 'f' WHERE user_id = ?", (girl["id"],))
    boy = register(store, 13303, "boy", city="Омск")
    _wizard(store, 13304, "filler2", city="Омск")

    served = {p["user_id"] for p in _deck(store, viewer)}
    assert girl["id"] in served, "противоположный пол показывается"
    assert boy["id"] not in served, "«противоположный пол» не должен показывать свой пол"


def test_same_gender_preference_hides_the_opposite_gender(store: Store):
    viewer = _wizard(store, 13401, "same_gender", gender="m", pref="same")
    girl = register(store, 13402, "girl2", city="Омск")
    store.x("UPDATE profiles SET gender = 'f' WHERE user_id = ?", (girl["id"],))
    boy = register(store, 13403, "boy2", city="Омск")

    served = {p["user_id"] for p in _deck(store, viewer)}
    assert boy["id"] in served
    assert girl["id"] not in served


# ── 5. возраст ±5 ────────────────────────────────────────────────────────────
def test_age_window_is_five_years_either_way_after_registration(store: Store):
    viewer = _wizard(store, 13501, "aged", age=24, city="Омск")
    flt = store.get_filters(store.profile_by_user(viewer["id"])["id"])
    assert (flt["min_age"], flt["max_age"]) == (19, 29)

    too_old = register(store, 13502, "old_fella", age=31, city="Омск")
    in_range = register(store, 13503, "peer", age=26, city="Омск")
    _wizard(store, 13504, "filler3", age=24, city="Омск")

    served = {p["user_id"] for p in _deck(store, viewer)}
    assert in_range["id"] in served, "ровесник показывается"
    assert too_old["id"] not in served, "разница больше 5 лет не показывается"
    assert config.MIN_AGE <= flt["min_age"] and flt["max_age"] <= config.MAX_AGE


def test_age_window_clamps_at_the_edges(store: Store):
    young = _wizard(store, 13601, "young", age=20, city="Омск")
    old = _wizard(store, 13602, "elder", age=97, city="Омск")
    assert store.get_filters(store.profile_by_user(young["id"])["id"])["min_age"] == config.MIN_AGE
    flt_old = store.get_filters(store.profile_by_user(old["id"])["id"])
    assert (flt_old["min_age"], flt_old["max_age"]) == (92, config.MAX_AGE)


def test_registration_through_typed_answers_still_gets_the_five_year_window(store: Store):
    """Если человек отвечал текстом, а не кнопками, окно ±5 всё равно обязано примениться."""
    user = register(store, 13701, "typed", age=33, city="Омск")   # helpers не жмут шаг поиска
    flt = store.get_filters(store.profile_by_user(user["id"])["id"])
    assert (flt["min_age"], flt["max_age"]) == (28, 38), \
        "окно ±5 должно применяться и когда шаг пройден текстом, а не кнопкой"


# ── 7. согласованность счётчика, режима без города и советов ──────────────────
def test_draft_started_before_the_feature_still_gets_the_window(store: Store):
    """Визард, начатый на старом шаге «Картинка»: при публикации окно ±5 обязано примениться."""
    user = store.ensure_user(13931, "legacy_draft", "Legacy")
    store.save_draft(user["id"], "photo", {
        "gender": "m", "age": 44, "country": "RU", "city": "Омск",
        "languages": ["ru"], "games": ["Dota 2"],
        "bio": "Играю вечерами, зову в пати",
    })                                     # ни search_mode, ни search_configured — как в старом черновике
    reg.on_button(store, user["id"], "photo_skip")
    assert reg.publish(store, user["id"])[1]

    flt = store.get_filters(store.profile_by_user(user["id"])["id"])
    assert (flt["min_age"], flt["max_age"]) == (39, 49)
    assert (flt["search_mode"], flt["gender_pref"]) == ("city", "any")


def test_available_counter_matches_what_the_deck_actually_gives(store: Store):
    """«Свободных анкет: N» не должно обещать карточки, которых человек не увидит."""
    viewer = _wizard(store, 13901, "counter", mode="genre", games="Valorant")
    register(store, 13902, "shooter", city="Томск", games="CS2")
    register(store, 13903, "moba_guy", city="Омск", games="Dota 2")

    promised = store.count_available(viewer["id"])
    served = _deck(store, viewer)
    assert promised == 1, "счётчик учитывает жанровый фильтр, а не весь пул"
    assert len(served) == promised, "сколько обещали — столько и показали"


def test_city_mode_without_a_city_does_not_deadlock_the_deck(store: Store):
    """Город пропущен, но выбран режим «пол + город»: радар не должен навсегда опустеть."""
    user = store.ensure_user(13911, "nomad", "Nomad")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    reg.on_text(store, user["id"], "27")
    reg.on_button(store, user["id"], "country", "RU")
    reg.on_button(store, user["id"], "city_skip")            # город не указан
    reg.on_button(store, user["id"], "lang", "ru")
    reg.on_button(store, user["id"], "lang_done")
    reg.on_text(store, user["id"], "Valorant")
    note = reg.on_button(store, user["id"], "search_mode", "city")
    assert "город не указан" in note.text.lower(), "человека предупреждают об отсутствии города"
    reg.on_button(store, user["id"], "gender_pref", "any")
    reg.on_button(store, user["id"], "search_done")
    reg.on_text(store, user["id"], "Играю вечерами, зову в пати")
    reg.on_button(store, user["id"], "photo_skip")
    assert reg.publish(store, user["id"])[1]

    flt = store.get_filters(store.profile_by_user(user["id"])["id"])
    assert flt["search_mode"] == "city" and flt["geo_mode"] == "any", \
        "без города география не должна резать выдачу в ноль"

    register(store, 13912, "townsman", city="Омск")
    result = cb.route(store, user["id"], "deck:next")
    assert TARGET in result.screen.text, "анкета из любого города всё равно показывается"


def test_empty_deck_advice_does_not_offer_the_mode_already_enabled(store: Store):
    """Тому, кто уже ищет по городу, не советуем переключиться на поиск по городу."""
    viewer = _wizard(store, 13921, "city_guy", mode="city", city="Омск")
    register(store, 13922, "other_genre", city="Томск", games="Dota 2")

    result = cb.route(store, viewer["id"], "deck:next")
    assert TARGET not in result.screen.text
    assert "переключи режим" not in result.screen.text.lower(), \
        "совет про смену режима уместен только в жанровом режиме"

def test_unknown_games_in_genre_mode_do_not_break_the_deck(store: Store):
    viewer = _wizard(store, 13801, "no_genre", mode="genre", games="Неведомая Игрушка 3000")
    register(store, 13802, "someone", city="Томск", games="Valorant")

    result = cb.route(store, viewer["id"], "deck:next")
    assert result.screen is not None, "пустая выдача не должна ронять бота"
    assert "жанр" in result.screen.text.lower(), "пользователю объясняют, что жанр не определился"
    actions = [a for row in result.screen.rows for _, a in row]
    assert "filters:mode:city" in actions, "предлагается переключиться на режим «пол + город»"
