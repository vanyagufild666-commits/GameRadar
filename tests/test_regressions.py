"""Регрессионные тесты на дефекты, найденные при разборе кода субагентом.

Каждый тест падал бы на старой версии — это проверка, что дефект закрыт, а не описание кода.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from db.store import Store
from handlers import callbacks as cb
from handlers import registration as reg
from tests.test_flow import register


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


def test_viewer_filters_use_profile_id_not_user_id(store: Store):
    """Фильтры читались по users.id, а лежат в profile_filters по profiles.id.

    Готовим расхождение: пользователь без анкеты занимает users.id = 1.
    """
    store.ensure_user(7001, "ghost", "Ghost")
    a = register(store, 7002, "viewer")
    register(store, 7003, "young", age=21)
    prof = store.profile_by_user(a["id"])
    assert a["id"] != prof["id"], "тест осмыслен только при расхождении users.id и profiles.id"

    cb.route(store, a["id"], "filters:age:30:99")
    assert store.get_filters(prof["id"])["min_age"] == 30, "фильтр записан профилю смотрящего"
    assert store.count_available(a["id"]) == 0, "чужой профиль не должен диктовать фильтры смотрящему"
    assert "РАДАР ЧИСТ" in cb.route(store, a["id"], "deck:next").screen.text


def test_card_message_button_opens_request_chooser(store: Store):
    """Кнопка «Написать / медиа» на карточке раньше молча выдавала следующую карточку."""
    a = register(store, 7101, "sender2")
    register(store, 7102, "target3")
    card = cb.route(store, a["id"], "deck:next")
    action = next(act for row in card.screen.rows for _, act in row if act.startswith("deck:request:"))

    chooser = cb.route(store, a["id"], action)
    assert "ЗАПРОС В ПАТИ" in chooser.screen.text
    kinds = [act for row in chooser.screen.rows for _, act in row]
    assert any(act.endswith(":text") for act in kinds)
    assert any(act.endswith(":photo") for act in kinds)
    assert any(act.endswith(":video_note") for act in kinds)


def test_card_shows_player_name_from_telegram(store: Store):
    """Пул кандидатов не отдавал display_name, и карточка показывала «игрок»."""
    a = register(store, 7201, "nameless")
    register(store, 7202, "frostbyte")
    card = cb.route(store, a["id"], "deck:next")
    assert "Frostbyte" in card.screen.text
    pool = store.candidate_pool(a["id"], 18, 99)
    assert pool and pool[0]["display_name"] == "Frostbyte"


def test_second_pass_limited_to_once_per_week(store: Store):
    """Второй проход ограничен SECOND_PASS_COOLDOWN_H, а не только счётчиком за сутки."""
    a = register(store, 7301, "passer")
    register(store, 7302, "only2")
    cb.route(store, a["id"], "deck:next")
    cb.route(store, a["id"], "deck:next")

    first = cb.route(store, a["id"], "deck:second_pass")
    assert store.current_cycle(a["id"]) == 2
    assert "не чаще" not in (first.alert or ""), "первый проход должен проходить"

    cb.route(store, a["id"], "deck:next")
    blocked = cb.route(store, a["id"], "deck:second_pass")
    assert "не чаще" in (blocked.alert or ""), "второй проход подряд запрещён"
    assert store.current_cycle(a["id"]) == 2


def test_second_pass_allowed_again_after_week(store: Store):
    a = register(store, 7401, "patient")
    register(store, 7402, "only3")
    cb.route(store, a["id"], "deck:second_pass")
    assert store.second_pass_allowed(a["id"]) is False
    # «прокручиваем» время: сдвигаем старт последнего прохода на 8 суток назад
    store.x("UPDATE deck_sessions SET started_at = datetime(started_at, '-8 day') "
            "WHERE viewer_user_id = ? AND cycle_no > 1", (a["id"],))
    assert store.second_pass_allowed(a["id"]) is True


def test_invalid_request_status_is_rejected(store: Store):
    """Схема допускает rejected, а код отправлял declined — теперь это ловится до SQLite."""
    a = register(store, 7501, "guard")
    b = register(store, 7502, "other4")
    rid = store.create_request(a["id"], b["id"], "text", "привет", "k-guard")
    with pytest.raises(ValueError):
        store.respond_request(rid, "declined")
    store.respond_request(rid, "rejected")
    assert store.request_by_id(rid)["status"] == "rejected"


def test_no_broken_utf8_characters_in_sources():
    """U+FFFD в текстах бота пользователь видит как «?» — следим за кодировкой файлов."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    skip = {".venv", "__pycache__", ".git"}
    offenders = []
    for path in root.rglob("*"):
        if not path.is_file() or skip & set(path.parts):
            continue
        if path.suffix not in {".py", ".md", ".sql", ".txt"}:
            continue
        if "\ufffd" in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"битые символы в файлах: {offenders}"


def test_card_shows_match_reason_and_remaining_counter(store: Store):
    """Карточка отвечает на вопрос «почему я вижу этого игрока» и сколько ещё осталось."""
    a = register(store, 8101, "reasons")
    register(store, 8102, "one", games="Valorant")
    register(store, 8103, "two", games="Dota 2")

    card = cb.route(store, a["id"], "deck:next")
    assert "СОВПАЛО" in card.screen.text
    assert "игры:" in card.screen.text
    assert "ОСТАЛОСЬ 1" in card.screen.text, "после первой карточки в проходе остаётся ещё одна"

    last = cb.route(store, a["id"], "deck:next")
    assert "СИГНАЛ ПОСЛЕДНИЙ В ЭТОМ ПРОХОДЕ" in last.screen.text
    assert "таймер" in last.screen.text.lower(), "пользователю объясняют ротацию без раскрытия формулы"


def test_consent_is_recorded_once(store: Store):
    """Подтверждение 18+ хранится событием: гейт в bot.py опирается на него."""
    user = store.ensure_user(8201, "adult", "Adult")
    assert reg.has_consent(store, user["id"]) is False
    reg.give_consent(store, user["id"])
    assert reg.has_consent(store, user["id"]) is True
    reg.give_consent(store, user["id"])
    row = store.q1("SELECT COUNT(*) AS c FROM events WHERE user_id = ? AND name = ?",
                   (user["id"], reg.CONSENT_EVENT))
    assert row["c"] == 1, "повторное нажатие не должно дублировать согласие"


def test_consent_screen_lists_rules_and_has_no_dead_ends(store: Store):
    from ui.screens import screen_consent

    screen = screen_consent()
    text = screen.text.lower()
    for required in ("18+", "nsfw", "буллинг", "личные данные"):
        assert required in text, f"в дисклеймере нет пункта «{required}»"
    actions = [action for row in screen.rows for _, action in row]
    assert "onb:consent" in actions and "onb:exit" in actions


def test_inbox_items_contract_is_stable(store: Store):
    """Экран входящих получает preview и имя, а не сырые колонки message_requests."""
    a = register(store, 7601, "writer")
    b = register(store, 7602, "reader")
    rid = store.create_request(a["id"], b["id"], "text", "Ищу пати в Dota", "k-inbox")
    items = store.inbox_items(b["id"])
    assert items and items[0]["preview"] == "Ищу пати в Dota"
    assert items[0]["sender_name"] == "Writer"

    store.add_attachment(rid, "photo", "fid-x", caption=None)
    store.x("UPDATE message_requests SET text_content = NULL WHERE id = ?", (rid,))
    assert store.inbox_items(b["id"])[0]["preview"] == "📷 фото без подписи"
