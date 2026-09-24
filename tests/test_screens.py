"""Смоук-тесты экранов: лимиты Telegram на текст и callback_data."""
from __future__ import annotations

from ui import screens as sc

URL = "https://t.me/PartyRadarBot?start=share"


def _all_screens() -> list[tuple[str, sc.Screen]]:
    cand = dict(profile_id=7, user_id=2, gender="f", age=24, country_code="RU", city="Омск",
                bio="Играю вечером, ищу спокойную пати", photo_file_id="fid",
                display_name="Beta")
    compat = {"languages": ["ru", "en"], "games": ["Valorant"], "games_count": 1, "status": "ОНЛАЙН"}
    draft = dict(gender="m", age=25, country="RU", city="Омск", languages=["ru"],
                 games=["Valorant"], bio="Ищу пати", photo_file_id=None)
    prof = dict(gender="m", age=25, country_code="RU", city="Омск", bio="Ищу пати",
                photo_file_id="fid")
    flt = dict(min_age=18, max_age=99, geo_mode="city", require_common_language=1,
               require_common_game=0)
    item = dict(id=1, kind="photo", preview="привет, ищу пати", sender_name="Beta")
    return [
        ("welcome", sc.screen_welcome(True)),
        ("consent", sc.screen_consent()),
        ("about", sc.screen_about()),
        ("reg", sc.screen_reg_step(3, 8, "Город", "Напиши город", [[("⏭️ Пропустить", "onb:city_skip")]])),
        ("preview", sc.screen_preview(draft)),
        ("home", sc.screen_home(True, 12, 3, 5, 2, 4, "Напарник")),
        ("home_off", sc.screen_home(False, 0, 0, 0)),
        ("card", sc.screen_card(cand, compat, viewer_city="Омск")),
        ("exhausted", sc.screen_exhausted("all_on_cooldown", ["invite_friends"], 3)),
        ("profile", sc.screen_my_profile(prof, ["ru"], ["Valorant"], 12, 3, 5, True)),
        ("paused", sc.screen_paused(False, 1)),
        ("match", sc.screen_match({**cand, "username": "beta"}, 1, 0, 3, "a")),
        ("match_open", sc.screen_match({**cand, "username": "beta"}, 1, 1, 3, "a")),
        ("req_type", sc.screen_request_type(7, "Beta")),
        ("req_compose", sc.screen_request_compose(7, "video_note")),
        ("inbox", sc.screen_inbox([item])),
        ("inbox_empty", sc.screen_inbox([])),
        ("inbox_item", sc.screen_inbox_item(item, cand, ["Русский"])),
        ("sent", sc.screen_request_sent("Beta")),
        ("report", sc.screen_report(7, "Beta")),
        ("report_done", sc.screen_report_done("Beta")),
        ("filters", sc.screen_filters(flt, 3)),
        ("progress", sc.screen_progress(12, 3, 5, 2, 1, 4, "Напарник", 70)),
        ("need_profile", sc.screen_need_profile()),
        ("error", sc.screen_error("Что-то пошло не так")),
    ]


def test_all_screens_fit_telegram_limits():
    for name, screen in _all_screens():
        assert screen.text.strip(), f"{name}: пустой текст"
        assert len(screen.text) <= 4096, f"{name}: текст длиннее 4096 символов"
        for row in screen.rows:
            assert 1 <= len(row) <= 4, f"{name}: некорректная ширина ряда кнопок"
            for label, action in row:
                assert label.strip(), f"{name}: пустая подпись кнопки"
                assert len(label) <= 40, f"{name}: подпись «{label}» слишком длинная"
                if sc.is_url(action):
                    assert action.startswith("https://"), f"{name}: странный URL {action}"
                else:
                    assert len(action.encode()) <= 64, f"{name}: callback_data > 64 байт ({action})"
                    assert ":" in action, f"{name}: callback_data без неймспейса ({action})"


def test_card_contains_every_required_button():
    screen = dict(_all_screens())["card"]
    actions = [a for row in screen.rows for _, a in row]
    assert any(a.startswith("deck:like:") for a in actions)
    assert any(a.startswith("deck:request:") for a in actions)
    assert any(a.startswith("deck:skip:") for a in actions)
    assert "profile:pause" in actions
    assert screen.photo == "fid", "карточка анкеты показывается с картинкой"


def test_callback_namespaces_are_known():
    known = ("onb:", "menu:", "deck:", "profile:", "request:", "inbox:", "match:",
             "report:", "block:", "filters:", "ref:")
    for name, screen in _all_screens():
        for row in screen.rows:
            for _, action in row:
                if sc.is_url(action):
                    continue
                assert action.startswith(known), f"{name}: неизвестный неймспейс {action}"


def test_box_layout_is_stable():
    text = sc.box("PARTY RADAR", ["строка"])
    assert text.startswith("╭─〔 PARTY RADAR 〕─╮")
    assert text.endswith("╰────────────────╯")
    assert "╭" in sc.box("X", ["y"], footer="низ")
