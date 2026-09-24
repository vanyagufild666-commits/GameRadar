from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime

import pytest

import config
from db.store import Store
from domain.deck import Viewer, passes_hard_filters
from handlers import callbacks as cb
from handlers import registration as reg


def test_game_genres_are_case_insensitive_and_ignore_unknown_games():
    assert config.game_genres(["CS2", "VALORANT", "Overwatch"]) == {"shooter"}
    assert config.game_genres(["Dota 2", "League of Legends", "Minecraft"]) == {"moba", "sandbox"}
    assert config.game_genres(["not in catalog"]) == set()
    assert config.game_genres(["cS2", "deep rock galactic", "??"]) == {"shooter", "coop"}


@pytest.mark.parametrize("age,expected", [(24, (19, 29)), (20, (18, 25)), (97, (92, 99))])
def test_age_window(age, expected):
    assert reg.age_window(age) == expected


def test_city_and_genre_modes_apply_geography_genre_and_gender():
    viewer_city = Viewer(1, 1, 25, "RU", "Омск", languages={"ru"}, games={"valorant"},
                         min_age=20, max_age=30, geo_mode="city", search_mode="city")
    candidate = dict(user_id=2, age=24, gender="f", country_code="RU", city="Томск",
                     languages={"ru"}, games={"valorant"})
    assert not passes_hard_filters(viewer_city, candidate, datetime.now())
    viewer_genre = Viewer(1, 1, 25, "RU", "Омск", languages={"ru"}, games={"valorant"},
                          min_age=20, max_age=30, geo_mode="any", search_mode="genre")
    assert passes_hard_filters(viewer_genre, candidate, datetime.now())
    assert not passes_hard_filters(viewer_genre, {**candidate, "games": {"dota 2"}}, datetime.now())


def test_gender_preference_filters_same_and_other():
    v = Viewer(1, 1, 25, "RU", gender="m", gender_pref="same", require_common_language=False)
    candidate = dict(user_id=2, age=25, gender="f", country_code="RU", city="Омск")
    assert not passes_hard_filters(v, candidate, datetime.now())
    assert passes_hard_filters(v, {**candidate, "gender": "m"}, datetime.now())
    v.gender_pref = "other"
    assert passes_hard_filters(v, candidate, datetime.now())
    assert not passes_hard_filters(v, {**candidate, "gender": "m"}, datetime.now())


def test_wizard_step_and_publish_save_search_preferences(store=None):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Store(path)
    try:
        user = s.ensure_user(77701, "prefs", "Prefs")
        reg.start(s, user["id"])
        reg.on_button(s, user["id"], "gender", "m")
        reg.on_text(s, user["id"], "24")
        reg.on_button(s, user["id"], "country", "RU")
        reg.on_text(s, user["id"], "Омск")
        reg.on_button(s, user["id"], "lang", "ru")
        reg.on_button(s, user["id"], "lang_done")
        reg.on_text(s, user["id"], "Valorant")
        assert reg.STEP_BY_KEY["search"] < reg.STEP_BY_KEY["bio"]
        assert s.load_draft(user["id"])[0] == "search"
        reg.on_button(s, user["id"], "search_mode", "city")
        reg.on_button(s, user["id"], "gender_pref", "other")
        reg.on_button(s, user["id"], "search_done")
        reg.on_text(s, user["id"], "Ищу спокойную команду для игры вечером без токсичности")
        reg.on_button(s, user["id"], "photo_skip")
        screen, ok = reg.publish(s, user["id"])
        assert ok
        profile = s.profile_by_user(user["id"])
        filters = s.get_filters(profile["id"])
        assert (filters["search_mode"], filters["gender_pref"], filters["min_age"], filters["max_age"]) == ("city", "other", 19, 29)
    finally:
        s.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


def test_old_schema_migrates_with_defaults(tmp_path):
    path = tmp_path / "old.db"
    old_schema = open("db/schema.sql", encoding="utf-8").read()
    old_schema = old_schema.replace("    search_mode TEXT NOT NULL DEFAULT 'city' CHECK (search_mode IN ('city', 'genre')),\n", "")
    old_schema = old_schema.replace("    gender_pref TEXT NOT NULL DEFAULT 'any' CHECK (gender_pref IN ('any', 'same', 'other')),\n", "")
    conn = sqlite3.connect(path)
    conn.executescript(old_schema)
    conn.execute("INSERT INTO users (telegram_user_id) VALUES (77801)")
    conn.execute("INSERT INTO profiles (user_id, gender, age, country_code, bio) VALUES (1, 'm', 25, 'RU', 'bio')")
    conn.execute("INSERT INTO profile_filters (profile_id) VALUES (1)")
    conn.commit()
    conn.close()
    store = Store(str(path))
    try:
        flt = store.get_filters(1)
        assert flt["search_mode"] == "city" and flt["gender_pref"] == "any"
        store.save_filters(1, search_mode="genre", gender_pref="same")
        assert store.get_filters(1)["search_mode"] == "genre"
    finally:
        store.close()


def test_genre_mode_without_known_games_has_explanatory_exhausted_screen():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Store(path)
    try:
        user = s.ensure_user(77901, "unknown", "Unknown")
        s.create_profile(user["id"], dict(gender="m", age=25, country="RU", city="Омск", bio="Ищу спокойную пати, играю по вечерам", languages=["ru"], games=["Unknown Game"]), publish=True)
        profile = s.profile_by_user(user["id"])
        s.save_filters(profile["id"], search_mode="genre", geo_mode="any", require_common_language=0)
        result = cb.route(s, user["id"], "deck:next")
        assert "РАДАР ЧИСТ" in result.screen.text
        assert "жанр" in result.screen.text.lower() and "режим" in result.screen.text.lower()
    finally:
        s.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
