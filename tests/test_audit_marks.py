"""Приёмка: галочки в визарде, зелёные галочки в фильтрах, честный процент заполнения.

Тесты по дизайн-контракту docs/design-selected-marks.md, без оглядки на реализацию:
ровно одна отметка в каждой группе, пресеты возраста по возрасту владельца,
100% у профиля, прошедшего визард, разбивка на экране прогресса.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from db.store import Store
from handlers import registration as reg
from ui import screens as sc

CHECK = "\u2705"  # ✅


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


def _labels(screen: sc.Screen) -> list[str]:
    """Все подписи кнопок экрана."""
    return [label for row in screen.rows for label, _ in row]


def _marks(screen: sc.Screen) -> int:
    return sum(1 for label in _labels(screen) if label.endswith(CHECK))


def _marked(screen: sc.Screen, *prefixes: str) -> list[str]:
    """Подписи отмеченных кнопок, отобранные по их action-коду (не по тексту)."""
    return [label for row in screen.rows for label, action in row
            if label.endswith(CHECK) and action.startswith(prefixes)]


def _draft_to_search(store: Store, tg_id: int, pref: str | None = "any") -> dict:
    """Проводит визард до шага «Как искать напарников» и возвращает пользователя."""
    user = store.ensure_user(tg_id, f"u{tg_id}", f"U{tg_id}")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    reg.on_text(store, user["id"], "25")
    reg.on_button(store, user["id"], "country", "RU")
    reg.on_text(store, user["id"], "Омск")
    reg.on_button(store, user["id"], "lang", "ru")
    reg.on_button(store, user["id"], "lang_done")
    reg.on_text(store, user["id"], "Valorant")
    if pref is not None:
        reg.on_button(store, user["id"], "gender_pref", pref)
    return user


def _wizard(store: Store, tg_id: int, gender="m", age=25, city="Омск",
            games="Valorant, Dota 2, CS2", bio="Ищу спокойную пати, играю вечером, без токсичности и криков.",
            photo=None, search_done=True) -> dict:
    """Полный визард через кнопки; photo=None — пропуск картинки."""
    user = store.ensure_user(tg_id, f"w{tg_id}", f"W{tg_id}")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", gender)
    reg.on_text(store, user["id"], str(age))
    reg.on_button(store, user["id"], "country", "RU")
    if city:
        reg.on_text(store, user["id"], city)
    else:
        reg.on_button(store, user["id"], "city_skip")
    reg.on_button(store, user["id"], "lang", "ru")
    reg.on_button(store, user["id"], "lang_done")
    reg.on_text(store, user["id"], games)
    if search_done:
        reg.on_button(store, user["id"], "search_done")
    reg.on_text(store, user["id"], bio)
    if photo:
        reg.on_photo(store, user["id"], "fid_photo")
    else:
        reg.on_button(store, user["id"], "photo_skip")
    assert reg.publish(store, user["id"])[1]
    return user


# ── 1. визард: галочка на предпочтении пола ──────────────────────────────────
def test_wizard_marks_selected_gender_preference(store: Store):
    for pref, label in (("any", "Любой пол"), ("same", "Мой пол"), ("other", "Противоположный")):
        user = _draft_to_search(store, 1000 + {"any": 0, "same": 1, "other": 2}[pref], pref)
        screen = reg.on_button(store, user["id"], "search_mode", "city")  # перерисовка шага
        labels = _labels(screen)
        assert f"{label} {CHECK}" in labels, f"выбран {pref} → отмечен «{label}»"
        others = [l for l in labels if l.endswith(CHECK) and "пол" in l and l != f"{label} {CHECK}"]
        assert not others, "в группе пола ровно одна галочка"


def test_wizard_missing_gender_preference_defaults_to_any(store: Store):
    user = _draft_to_search(store, 1003, pref=None)
    screen = reg.step_screen(store, user["id"])
    assert f"Любой пол {CHECK}" in _labels(screen)


def test_wizard_mode_and_gender_share_one_mark_style(store: Store):
    user = _draft_to_search(store, 1004, pref="other")
    screen = reg.on_button(store, user["id"], "search_mode", "genre")
    marked = [l for l in _labels(screen) if l.endswith(CHECK)]
    assert len(marked) == 2, "ровно режим и пол"
    assert all(" ✅" in l for l in marked), "суффикс, а не префикс"


# ── 2. фильтры: по одной галочке на группу ────────────────────────────────────
def _flt(**over):
    base = dict(min_age=18, max_age=99, geo_mode="any", require_common_language=1,
                require_common_game=0, search_mode="city", gender_pref="any")
    base.update(over)
    return base


def test_filters_mark_exactly_one_option_per_group(store: Store):
    screen = sc.screen_filters(_flt(min_age=20, max_age=30, search_mode="city", gender_pref="same",
                                    geo_mode="city", require_common_language=0,
                                    require_common_game=1), 5, viewer_age=25)
    marked = [l for l in _labels(screen) if l.endswith(CHECK)]
    groups = {
        "режим": _marked(screen, "filters:mode:"),
        "пол": _marked(screen, "filters:gender:"),
        "возраст": _marked(screen, "filters:window:", "filters:age:"),
        "гео": _marked(screen, "filters:geo:"),
        "общая игра": _marked(screen, "filters:game"),
    }
    for name, found in groups.items():
        assert len(found) == 1, f"{name}: ожидалась одна галочка, получено {found}"
    assert not any("Общий язык" in l for l in marked), "выключенный тумблер не отмечается"


def test_filters_age_window_uses_viewer_age(store: Store):
    # возраст 25 → ±5 = 20..30
    screen = sc.screen_filters(_flt(min_age=20, max_age=30), 3, viewer_age=25)
    marked = _marked(screen, "filters:window:", "filters:age:")
    assert len(marked) == 1 and "±5" in marked[0], marked


def test_filters_clamped_age_window(store: Store):
    # возраст 20 → ±5 = 18..25 (нижняя граница зажата)
    screen = sc.screen_filters(_flt(min_age=18, max_age=25), 3, viewer_age=20)
    marked = _marked(screen, "filters:window:", "filters:age:")
    assert len(marked) == 1 and "±5" in marked[0], marked


def test_filters_full_range_marks_preset_all_only_once(store: Store):
    screen = sc.screen_filters(_flt(min_age=18, max_age=99), 3, viewer_age=25)
    marked = _marked(screen, "filters:window:", "filters:age:")
    assert len(marked) == 1 and "18–99" in marked[0], \
        f"дубль 18–99: отметка только на пресете window:all, получено {marked}"


def test_filters_exact_custom_18_24_is_marked(store: Store):
    screen = sc.screen_filters(_flt(min_age=18, max_age=24), 3, viewer_age=30)
    marked = _marked(screen, "filters:age:")
    assert len(marked) == 1 and "18–24" in marked[0], marked


def test_filters_arbitrary_custom_age_unmarked_but_shown(store: Store):
    screen = sc.screen_filters(_flt(min_age=20, max_age=30), 3, viewer_age=40)
    marked = _marked(screen, "filters:window:", "filters:age:")
    assert marked == [], "произвольный диапазон не выдаётся за пресет"
    assert "20–30" in screen.text and "свой диапазон" in screen.text


def test_filters_geo_country_button_exists_and_is_marked(store: Store):
    screen = sc.screen_filters(_flt(geo_mode="country"), 3)
    labels = _labels(screen)
    actions = [a for row in screen.rows for _, a in row]
    assert "filters:geo:country" in actions, "legacy-значение geo_mode=country видно отдельной кнопкой"
    assert any(l.endswith(CHECK) and "Моя страна" in l for l in labels)


def test_filters_toggles_mark_only_when_enabled(store: Store):
    on = sc.screen_filters(_flt(require_common_language=1, require_common_game=1), 3)
    off = sc.screen_filters(_flt(require_common_language=0, require_common_game=0), 3)
    assert any(l.endswith(CHECK) and "Общий язык" in l for l in _labels(on))
    assert any(l.endswith(CHECK) and "Общая игра" in l for l in _labels(on))
    assert not any(l.endswith(CHECK) and ("Общий язык" in l or "Общая игра" in l) for l in _labels(off))


def test_filters_keep_all_legacy_callbacks(store: Store):
    screen = sc.screen_filters(_flt(), 3, viewer_age=25)
    actions = [a for row in screen.rows for _, a in row]
    for cb in ("filters:mode:city", "filters:mode:genre", "filters:gender:any",
               "filters:gender:same", "filters:gender:other", "filters:window:5",
               "filters:window:10", "filters:window:all", "filters:age:18:24",
               "filters:age:18:99", "filters:geo:any", "filters:geo:city",
               "filters:lang", "filters:game", "menu:home"):
        assert cb in actions, cb


def test_filters_screen_stays_within_telegram_limit(store: Store):
    screen = sc.screen_filters(_flt(min_age=18, max_age=24, require_common_game=1,
                                    geo_mode="country", search_mode="genre",
                                    gender_pref="other"), 0, viewer_age=99)
    assert len(screen.text) <= 4096
    assert all(len(a) <= 64 for row in screen.rows for _, a in row)


# ── 3. процент заполнения ─────────────────────────────────────────────────────
def test_fill_full_wizard_profile_without_photo_or_city_is_100(store: Store):
    user = _wizard(store, 1101, photo=None, city="Омск")
    prof = store.profile_by_user(user["id"])
    report = sc._fill(prof, store.profile_languages(prof["id"]), store.profile_games(prof["id"]))
    assert report.progress == 100
    assert not report.missing
    assert [i.key for i in report.improvements] == ["photo", "bio_quality"]


def test_fill_gender_x_is_not_missing(store: Store):
    user = _wizard(store, 1102, gender="x")
    prof = store.profile_by_user(user["id"])
    report = sc._fill(prof, store.profile_languages(prof["id"]), store.profile_games(prof["id"]))
    assert report.progress == 100, "осознанный пол «не указывать» не режет процент"


def test_fill_one_game_and_short_bio_are_complete(store: Store):
    user = _wizard(store, 1103, games="Valorant", bio="Играю вечером, ищу спокойную пати")
    prof = store.profile_by_user(user["id"])
    report = sc._fill(prof, store.profile_languages(prof["id"]), store.profile_games(prof["id"]))
    assert report.progress == 100
    keys = [i.key for i in report.improvements]
    assert "games_quality" in keys and "bio_quality" in keys


def test_fill_each_missing_required_drops_exact_points(store: Store):
    prof = dict(gender="m", age=25, country_code="RU", bio="Играю по вечерам в кооп, ищу спокойную пати",
                photo_file_id=None, city="Омск")
    langs, games = ["ru"], ["Valorant"]
    full = sc._fill(prof, langs, games)
    assert full.progress == 100
    cases = [("gender", 15), ("age", 15), ("country_code", 15), ("bio", 25)]
    for key, points in cases:
        mutated = dict(prof)
        if key == "bio":
            mutated["bio"] = ""
        elif key == "languages":
            pass
        else:
            mutated[key] = None
        if key == "languages":
            report = sc._fill(prof, [], games)
        else:
            report = sc._fill(mutated, langs, games)
        assert report.progress == 100 - points, f"{key} должен стоить {points}"


def test_fill_optional_never_reduces_progress(store: Store):
    prof = dict(gender="m", age=25, country_code="RU", bio="x" * 90, photo_file_id="f", city="Омск")
    base = sc._fill(prof, ["ru"], ["Valorant", "Dota 2", "CS2"]).progress
    for change in ({"photo_file_id": None}, {"city": None}, {"bio": "не такое подробное, но валидное"}):
        report = sc._fill(dict(prof, **change), ["ru"], ["Valorant"])
        assert report.progress == base, change


def test_real_like_profile_that_was_30_now_is_100(store: Store):
    """Профиль, на котором пользователь видел 30% (пол x, нет фото/города, 1 игра, био <80)."""
    user = _wizard(store, 1104, gender="x", city=None, games="Dota 2",
                   bio="Играю по вечерам, зову в пати", photo=None)
    prof = store.profile_by_user(user["id"])
    report = sc._fill(prof, store.profile_languages(prof["id"]), store.profile_games(prof["id"]))
    assert report.progress == 100


# ── 4. экран прогресса ────────────────────────────────────────────────────────
def test_progress_screen_shows_breakdown(store: Store):
    prof = dict(gender="m", age=25, country_code="RU", bio="", photo_file_id=None, city=None)
    report = sc._fill(prof, ["ru"], ["Valorant"])
    screen = sc.screen_progress(10, 2, 1, 0, 3, 4, "Напарник", report)
    assert "Заполнение анкеты: 75%" in screen.text
    assert "/100" in screen.text
    assert "О себе" in screen.text, "видны незаполненные обязательные пункты"
    assert "улучшить" in screen.text.lower(), "видны подсказки по улучшению"
    assert len(screen.text) <= 4096


def test_progress_screen_keeps_integer_compatibility(store: Store):
    screen = sc.screen_progress(1, 2, 3, 4, 5, 6, "Напарник", 70)
    assert "Заполнение анкеты: 70%" in screen.text


def test_menu_filters_marks_window_for_real_profile_age(store: Store):
    """Сквозной путь: роутер сам берёт возраст владельца для пресетов ±5/±10."""
    from handlers import callbacks as cb
    user = _wizard(store, 1106, age=25)
    prof = store.profile_by_user(user["id"])
    store.save_filters(prof["id"], min_age=20, max_age=30)
    result = cb.route(store, user["id"], "menu:filters")
    marked = _marked(result.screen, "filters:window:", "filters:age:")
    assert len(marked) == 1 and "±5" in marked[0], \
        f"окно 20–30 при возрасте 25 — это пресет ±5, получено {marked}"


def test_menu_progress_renders_fill_report_end_to_end(store: Store):
    from handlers import callbacks as cb
    user = _wizard(store, 1105, photo=None)
    result = cb.route(store, user["id"], "menu:progress")
    assert "Заполнение анкеты: 100%" in result.screen.text
    assert "Обязательные поля: 100/100" in result.screen.text
