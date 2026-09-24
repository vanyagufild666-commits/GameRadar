"""Сквозные сценарии: регистрация → колода → лайк → мэтч → заявка → контакты.

Всё идёт через handlers (без Telegram), поэтому проверяется реальная логика бота.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from db.store import Store
from handlers import callbacks as cb
from handlers import registration as reg


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


def register(store: Store, tg_id: int, username: str, age: int = 25, city: str = "Омск",
             games: str = "Valorant, Dota 2", bio: str | None = None) -> dict:
    """Проходит визард регистрации целиком, как это делает пользователь в чате."""
    user = store.ensure_user(tg_id, username, username.capitalize())
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    assert reg.on_text(store, user["id"], str(age)) is not None
    reg.on_button(store, user["id"], "country", "RU")
    reg.on_text(store, user["id"], city)
    reg.on_button(store, user["id"], "lang", "ru")
    reg.on_button(store, user["id"], "lang_done")
    reg.on_text(store, user["id"], games)
    reg.on_text(store, user["id"], bio or "Ищу спокойную пати, играю вечером, без токсичности")
    reg.on_button(store, user["id"], "photo_skip")
    screen, published = reg.publish(store, user["id"])
    assert published, "анкета должна публиковаться после полного визарда"
    assert store.profile_by_user(user["id"])["status"] == "active"
    return user


# ── регистрация ──────────────────────────────────────────────────────────────
def test_wizard_keeps_state_in_db_and_survives_restart(store: Store):
    user = store.ensure_user(2001, "wiz", "Wiz")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "f")
    reg.on_text(store, user["id"], "31")

    # «перезапуск бота»: новое подключение к той же базе
    store.close()
    import config
    again = Store(config.DB_PATH if False else store.path)
    try:
        state, draft, _ = again.load_draft(user["id"])
        assert state == "country" and draft["gender"] == "f" and draft["age"] == 31
        assert reg.step_screen(again, user["id"]).text
    finally:
        again.close()


def test_invalid_input_keeps_user_on_same_step(store: Store):
    user = store.ensure_user(2002, "bad", "Bad")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    screen = reg.on_text(store, user["id"], "10")
    assert "18+" in screen.text
    state, _, attempts = store.load_draft(user["id"])
    assert state == "age" and attempts == 1
    assert reg.on_text(store, user["id"], "abc") is not None


def test_publish_requires_full_draft(store: Store):
    user = store.ensure_user(2003, "half", "Half")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    screen, ok = reg.publish(store, user["id"])
    assert not ok and "не полностью" in screen.text
    assert store.profile_by_user(user["id"]) is None


def test_language_multi_select_respects_limit(store: Store):
    user = store.ensure_user(2004, "poly", "Poly")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    reg.on_text(store, user["id"], "27")
    reg.on_button(store, user["id"], "country", "KZ")
    reg.on_text(store, user["id"], "Алматы")
    for code in ("ru", "en", "kk", "uk", "tr", "de"):
        reg.on_button(store, user["id"], "lang", code)
    _, draft, _ = store.load_draft(user["id"])
    assert len(draft["languages"]) == 4  # config.MAX_LANGUAGES


# ── колода ───────────────────────────────────────────────────────────────────
def test_next_card_shows_the_other_player_and_never_self(store: Store):
    a = register(store, 3001, "alpha")
    b = register(store, 3002, "beta", age=27, games="Valorant")
    result = cb.route(store, a["id"], "deck:next")
    prof_b = store.profile_by_user(b["id"])
    assert f"#{prof_b['id']}" in result.screen.text
    assert prof_b["bio"] in result.screen.text
    labels = [label for row in result.screen.rows for label, _ in row]
    assert any("В пати" in label for label in labels)
    assert any("Написать" in label for label in labels)
    assert any("Пропустить" in label for label in labels)
    assert any("Выключить анкету" in label for label in labels)


def test_skip_hides_card_for_cooldown_and_moves_on(store: Store):
    a = register(store, 3101, "skipper")
    b = register(store, 3102, "target")
    c = register(store, 3103, "third")
    card = cb.route(store, a["id"], "deck:next")
    pid = _pid_from(card)
    result = cb.route(store, a["id"], f"deck:skip:{pid}")
    assert "Пропустить" not in result.screen.text or "СИГНАЛ" in result.screen.text
    skipped_profile = store.profile_by_id(int(pid))
    deck = store.deck_row(a["id"], skipped_profile["user_id"])
    assert deck["skip_count"] == 1 and deck["next_eligible_at"] is not None
    assert store.count_available(a["id"]) >= 0


def test_second_pass_reopens_deck_after_cards_were_only_shown(store: Store):
    a = register(store, 3201, "cycler")
    register(store, 3202, "only")
    first = cb.route(store, a["id"], "deck:next")
    assert "СИГНАЛ" in first.screen.text
    empty = cb.route(store, a["id"], "deck:next")
    assert "РАДАР ЧИСТ" in empty.screen.text, "в текущем проходе анкета уже показана"
    again = cb.route(store, a["id"], "deck:second_pass")
    assert store.current_cycle(a["id"]) == 2
    assert "СИГНАЛ" in again.screen.text, "новый проход возвращает показанные анкеты обратно"


def test_second_pass_respects_explicit_skip(store: Store):
    a = register(store, 3203, "skipper2")
    b = register(store, 3204, "target2")
    pid = str(store.profile_by_user(b["id"])["id"])
    cb.route(store, a["id"], "deck:next")
    cb.route(store, a["id"], f"deck:skip:{pid}")
    again = cb.route(store, a["id"], "deck:second_pass")
    assert "РАДАР ЧИСТ" in again.screen.text, "явный пропуск держит анкету на таймере"
    assert store.deck_row(a["id"], b["id"])["next_eligible_at"] is not None


def test_paused_profile_cannot_open_deck(store: Store):
    a = register(store, 3301, "paused")
    register(store, 3302, "other")
    cb.route(store, a["id"], "profile:pause")
    result = cb.route(store, a["id"], "deck:next")
    assert "выключена" in result.screen.text.lower()
    resumed = cb.route(store, a["id"], "profile:resume")
    assert "снова в эфире" in resumed.screen.text


# ── лайки, мэтчи, контакты ───────────────────────────────────────────────────
def test_mutual_like_creates_match_and_hides_pair_from_deck(store: Store):
    a = register(store, 4001, "liker")
    b = register(store, 4002, "answerer")
    pid_b = str(store.profile_by_user(b["id"])["id"])
    pid_a = str(store.profile_by_user(a["id"])["id"])

    first = cb.route(store, a["id"], f"deck:like:{pid_b}")
    assert store.has_like(a["id"], b["id"]) is True
    assert store.active_match(a["id"], b["id"]) is None
    assert first.effects and first.effects[0]["user_id"] == b["id"], "оппонент получает сигнал"

    cb.route(store, b["id"], "deck:next")
    second = cb.route(store, b["id"], f"deck:like:{pid_a}")
    match = store.active_match(a["id"], b["id"])
    assert match is not None and match["status"] == "active"
    assert second.alert and "Взаимно" in second.alert

    # пара выведена из колоды
    assert store.candidate_pool(a["id"], 18, 99) == []


def test_contacts_open_only_after_both_sides_confirm(store: Store):
    a, b, match = _matched_pair(store, 4101, 4102)
    opened = cb.route(store, a["id"], f"match:open:{match['id']}")
    assert "не открываются автоматически" in opened.screen.text
    assert "не подтвердил" in opened.screen.text

    partial = cb.route(store, a["id"], f"match:share:{match['id']}")
    assert "Ждём подтверждения" in (partial.alert or "")
    assert not any(label == f"@{b['telegram_username']}" for row in partial.screen.rows for label, _ in row)

    final = cb.route(store, b["id"], f"match:share:{match['id']}")
    assert "Контакт открыт" in (final.alert or "")
    urls = [action for row in final.screen.rows for _, action in row]
    assert any(action.startswith("https://t.me/") for action in urls), "контакт отдаётся ссылкой"
    assert final.effects and "Контакт открыт" in final.effects[0]["text"]


# ── заявки на связь ──────────────────────────────────────────────────────────
def test_message_request_flow_text(store: Store):
    a = register(store, 5001, "sender")
    b = register(store, 5002, "recipient")
    pid_b = str(store.profile_by_user(b["id"])["id"])

    chooser = cb.route(store, a["id"], f"request:type:{pid_b}")
    kinds = [action for row in chooser.screen.rows for _, action in row]
    assert any(action.endswith(":photo") for action in kinds)
    assert any(action.endswith(":video") for action in kinds)
    assert any(action.endswith(":video_note") for action in kinds)

    compose = cb.route(store, a["id"], f"request:go:{pid_b}:text")
    assert "Напиши короткое сообщение" in compose.screen.text

    sent = cb.submit_request(store, a["id"], "text", "Привет! Ищу пати в Valorant вечером")
    assert sent.alert == "Заявка отправлена"
    assert sent.effects and sent.effects[0]["type"] == "notify"

    inbox = cb.route(store, b["id"], "menu:inbox")
    assert "sender" in inbox.screen.text or "Sender" in inbox.screen.text
    request_id = store.inbox(b["id"])[0]["id"]

    seen = cb.route(store, b["id"], f"inbox:open:{request_id}")
    assert "Привет" in seen.screen.text
    accepted = cb.route(store, b["id"], f"inbox:accept:{request_id}")
    assert "контакт открыт" in (accepted.alert or "").lower()
    assert store.request_by_id(request_id)["status"] == "accepted"
    assert accepted.effects and "Контакт" in accepted.effects[0]["text"]


def test_media_request_requires_matching_kind(store: Store):
    a = register(store, 5101, "photographer")
    b = register(store, 5102, "viewer")
    pid_b = str(store.profile_by_user(b["id"])["id"])
    cb.route(store, a["id"], f"request:go:{pid_b}:photo")
    sent = cb.submit_request(store, a["id"], "photo", "мой сетап", file_id="file-abc")
    assert sent.effects[0]["type"] == "notify_media"
    assert sent.effects[0]["file_id"] == "file-abc"
    rows = store.q("SELECT * FROM message_attachments")
    assert rows and rows[0]["media_type"] == "photo"


def test_duplicate_request_today_is_rejected(store: Store):
    a = register(store, 5201, "spammer")
    b = register(store, 5202, "calm")
    pid_b = str(store.profile_by_user(b["id"])["id"])
    cb.route(store, a["id"], f"request:go:{pid_b}:text")
    first = cb.submit_request(store, a["id"], "text", "привет")
    assert first.alert == "Заявка отправлена"
    cb.route(store, a["id"], f"request:go:{pid_b}:text")
    second = cb.submit_request(store, a["id"], "text", "привет ещё раз")
    assert "уже отправлена" in second.screen.text


def test_request_decline_and_block(store: Store):
    a = register(store, 5301, "unwanted")
    b = register(store, 5302, "polite")
    pid_b = str(store.profile_by_user(b["id"])["id"])
    cb.route(store, a["id"], f"request:go:{pid_b}:text")
    cb.submit_request(store, a["id"], "text", "привет")
    rid = store.inbox(b["id"])[0]["id"]
    declined = cb.route(store, b["id"], f"inbox:decline:{rid}")
    assert declined.alert == "Заявка отклонена"
    assert store.request_by_id(rid)["status"] == "rejected"

    # повторная заявка того же типа в тот же день блокируется идемпотентностью,
    # поэтому проверяем блокировку на другом формате контакта
    cb.route(store, a["id"], f"request:go:{pid_b}:photo")
    cb.submit_request(store, a["id"], "photo", "мой сетап", file_id="file-photo-1")
    rid2 = store.inbox(b["id"])[0]["id"]
    cb.route(store, b["id"], f"inbox:block:{rid2}")
    assert store.blocked_between(a["id"], b["id"]) is True
    assert store.request_by_id(rid2)["status"] == "blocked"
    assert store.candidate_pool(a["id"], 18, 99) == []


# ── жалобы, фильтры, меню ────────────────────────────────────────────────────
def test_report_hides_profile_and_serves_next(store: Store):
    a = register(store, 6001, "reporter")
    b = register(store, 6002, "offender")
    register(store, 6003, "innocent")
    pid_b = str(store.profile_by_user(b["id"])["id"])
    screen = cb.route(store, a["id"], f"report:open:{pid_b}")
    assert "Что не так" in screen.screen.text
    done = cb.route(store, a["id"], f"report:reason:{pid_b}:spam")
    assert done.alert == "Жалоба отправлена"
    assert store.blocked_between(a["id"], b["id"]) is True
    assert store.new_reports(), "жалоба попадает в очередь модерации"


def test_filters_change_availability(store: Store):
    a = register(store, 6101, "filtered", age=40)
    register(store, 6102, "young", age=21)
    # после регистрации окно поиска — возраст ±5 лет, поэтому 21-летний в выдачу не попадает
    assert store.count_available(a["id"]) == 0
    cb.route(store, a["id"], "filters:window:all")     # 18–99 — расширяем окно
    assert store.count_available(a["id"]) >= 1
    # фильтр по возрасту кандидатов: 21-летний выпадает из диапазона 30+
    result = cb.route(store, a["id"], "filters:age:30:99")
    assert "30–99" in result.screen.text
    assert store.count_available(a["id"]) == 0
    cb.route(store, a["id"], "filters:age:18:99")
    assert store.count_available(a["id"]) >= 1


def test_menu_screens_render_for_registered_user(store: Store):
    a = register(store, 6201, "menuuser")
    for data in ("menu:home", "menu:profile", "menu:progress", "menu:filters",
                 "menu:inbox", "ref:share"):
        result = cb.route(store, a["id"], data)
        assert result.screen.text.strip(), data
        assert len(result.screen.text) <= 4096, data


def test_unregistered_user_gets_onboarding(store: Store):
    user = store.ensure_user(6301, "newbie", "Newbie")
    result = cb.route(store, user["id"], "menu:home")
    assert "Сначала нужна анкета" in result.screen.text
    deck = cb.route(store, user["id"], "deck:next")
    assert "Сначала нужна анкета" in deck.screen.text


def _pid_from(result: cb.Result) -> str:
    """Достаёт id профиля из первой карточки колоды."""
    for row in result.screen.rows:
        for _, action in row:
            if action.startswith("deck:like:"):
                return action.rsplit(":", 1)[1]
    raise AssertionError("в карточке нет кнопки лайка")


def _matched_pair(store: Store, tg_a: int, tg_b: int) -> tuple[dict, dict, dict]:
    a = register(store, tg_a, f"user{tg_a}")
    b = register(store, tg_b, f"user{tg_b}")
    pid_a = str(store.profile_by_user(a["id"])["id"])
    pid_b = str(store.profile_by_user(b["id"])["id"])
    cb.route(store, a["id"], f"deck:like:{pid_b}")
    cb.route(store, b["id"], f"deck:like:{pid_a}")
    match = store.active_match(a["id"], b["id"])
    assert match is not None
    return a, b, match
