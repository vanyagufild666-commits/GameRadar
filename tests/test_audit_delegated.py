"""Независимая приёмка механик, реализованных субагентом.

Пишу свои тесты с нуля (свои сценарии, свои проверки), чтобы принять работу по факту,
а не по отчёту: накрутка приглашений, права модератора, окно напоминаний, отчёт прохода.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import types
from datetime import datetime, timedelta

import pytest

import bot
import config
from db.store import FMT, Store
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


def _extra_players(store: Store, count: int, start_tg: int) -> list[int]:
    """Реальные пользователи-кандидаты: view_events держит внешний ключ на users."""
    ids = []
    for i in range(count):
        ids.append(store.ensure_user(start_tg + i, None, f"P{start_tg + i}")["id"])
    return ids


def _decide(store: Store, user: dict, times: int, action: str = "skip",
            start_tg: int = 80000) -> None:
    """Записывает решения по карточкам напрямую: как будто человек нажал кнопку."""
    cycle = store.current_cycle(user["id"])
    for candidate_id in _extra_players(store, times, start_tg):
        store.record_view(user["id"], candidate_id, cycle, action)


def _view_events(store: Store, user: dict, times: int, action: str, start_tg: int) -> None:
    cycle = store.current_cycle(user["id"])
    for candidate_id in _extra_players(store, times, start_tg):
        store.record_view(user["id"], candidate_id, cycle, action)


def _event_at(store: Store, user_id: int, name: str, days_ago: float) -> None:
    stamp = (datetime.now() - timedelta(days=days_ago)).strftime(FMT)
    store.x("INSERT INTO events (user_id, name, source, created_at) VALUES (?,?,?,?)",
            (user_id, name, None, stamp))


# ── 1. реферальные приглашения ───────────────────────────────────────────────
def test_one_telegram_account_counts_only_once(store: Store):
    referrer = store.ensure_user(9001, "ref", "Ref")
    invited = register(store, 9002, "friend")
    assert store.record_referral(referrer["id"], 9002) is True
    assert store.record_referral(referrer["id"], 9002) is False, "повторный старт не даёт второе приглашение"
    rows = store.q("SELECT * FROM referrals WHERE referrer_user_id = ?", (referrer["id"],))
    assert len(rows) == 1 and rows[0]["invited_user_id"] == invited["id"]


def test_self_invite_and_unknown_referrer_are_rejected(store: Store):
    user = register(store, 9101, "self")
    assert store.record_referral(user["id"], 9101) is False, "самоприглашение не считается"
    assert store.record_referral(999999, 9101) is False, "пригласивший должен существовать"
    assert store.q("SELECT * FROM referrals") == []


def test_reward_needs_active_profile_and_three_real_decisions(store: Store):
    referrer = store.ensure_user(9201, "ref2", "Ref2")
    invited = store.ensure_user(9202, "lazy", "Lazy")
    store.record_referral(referrer["id"], 9202)
    assert store.confirm_referral(invited["id"]) is None, "без анкеты награды нет"

    register(store, 9202, "lazy")
    _decide(store, store.user_by_tg(9202), 2, start_tg=80100)
    assert store.confirm_referral(invited["id"]) is None, "двух решений недостаточно"

    # просмотры без решения не считаются
    _view_events(store, store.user_by_tg(9202), 5, "shown", 80200)
    _view_events(store, store.user_by_tg(9202), 5, "expired", 80300)
    assert store.confirm_referral(invited["id"]) is None, "shown/expired — не решения"

    _decide(store, store.user_by_tg(9202), 1, start_tg=80400)
    confirmed = store.confirm_referral(invited["id"])
    assert confirmed and confirmed["rewarded"] == 1
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT + 10


def test_reward_is_granted_once_per_invite(store: Store):
    referrer = store.ensure_user(9301, "ref3", "Ref3")
    register(store, 9302, "guest")
    invited = store.user_by_tg(9302)
    store.record_referral(referrer["id"], 9302)
    _decide(store, invited, 3)
    assert store.confirm_referral(invited["id"]) is not None
    assert store.confirm_referral(invited["id"]) is None, "второй раз та же награда не выдаётся"
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT + 10


def test_weekly_cap_blocks_the_sixth_confirmed_invite(store: Store):
    referrer = store.ensure_user(9401, "farmer", "Farmer")
    for i in range(6):
        register(store, 9410 + i, f"guest{i}")
        assert store.record_referral(referrer["id"], 9410 + i) is True
        _decide(store, store.user_by_tg(9410 + i), 3, start_tg=85000 + i * 10)
    for i in range(5):
        assert store.confirm_referral(store.user_by_tg(9410 + i)["id"]) is not None
    assert store.confirm_referral(store.user_by_tg(9415)["id"]) is None, "лимит 5 за неделю"
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT + 50


def test_bonus_cards_are_spent_only_above_the_base_limit(store: Store):
    referrer = store.ensure_user(9501, "spender", "Spender")
    register(store, 9502, "guest2")
    store.record_referral(referrer["id"], 9502)
    invited = store.user_by_tg(9502)
    _decide(store, invited, 3, start_tg=86000)
    store.confirm_referral(invited["id"])

    store.consume_referral_cards(referrer["id"], 60)          # ровно база — бонус цел
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT + 10
    store.consume_referral_cards(referrer["id"], 65)          # 5 сверх базы — списываем
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT + 5
    store.consume_referral_cards(referrer["id"], 500)         # больше выданного списать нельзя
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT


def test_deck_respects_personal_limit(store: Store):
    """Лимит карточек в _deck должен читаться через персональный лимит, а не константу."""
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "handlers", "callbacks.py"), encoding="utf-8").read()
    assert "store.daily_card_limit(user_id)" in source
    assert "served_today(user_id, today) >= config.DAILY_CARD_LIMIT" not in source


def test_reward_arrives_through_real_deck_actions(store: Store):
    """Сквозная проверка: награда приходит по факту решений, а не по прямому вызову метода."""
    from tests.test_flow import _pid_from

    referrer = store.ensure_user(11001, "host", "Host")
    invited = register(store, 11002, "guest3")
    store.record_referral(referrer["id"], 11002)
    for i in range(4):
        register(store, 11010 + i, f"filler{i}")

    from handlers import callbacks as cb
    collected: list[dict] = []
    result = cb.route(store, invited["id"], "deck:next")
    # пропуск сам отдаёт следующую карточку, поэтому идём по цепочке ответов
    for _ in range(6):
        if "СИГНАЛ" not in result.screen.text:
            break
        result = cb.route(store, invited["id"], f"deck:skip:{_pid_from(result)}")
        collected += result.effects

    decisions = store.q1("SELECT COUNT(*) AS c FROM view_events WHERE viewer_user_id = ? "
                         "AND action = 'skip'", (invited["id"],))["c"]
    assert decisions >= 3, "гость принял три решения"
    assert any(e.get("user_id") == referrer["id"] for e in collected), "пригласивший получает уведомление"
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT + 10
    assert store.referral_for_invited(invited["id"])["rewarded"] == 1


def test_no_reward_for_a_guest_who_never_decided(store: Store):
    from handlers import callbacks as cb

    referrer = store.ensure_user(11101, "host2", "Host2")
    invited = register(store, 11102, "passive")
    store.record_referral(referrer["id"], 11102)
    register(store, 11103, "filler_one")
    cb.route(store, invited["id"], "deck:next")          # один показ без решения
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT
    assert store.referral_for_invited(invited["id"])["rewarded"] == 0


# ── 2. отчёт прохода ─────────────────────────────────────────────────────────
def test_cycle_report_counts_shows_likes_and_requests(store: Store):
    a = register(store, 9601, "reporter")
    b = register(store, 9602, "target")
    register(store, 9603, "second_target")
    pid_b = str(store.profile_by_user(b["id"])["id"])

    from handlers import callbacks as cb
    cb.route(store, a["id"], "deck:next")
    cb.route(store, a["id"], "deck:next")
    cb.route(store, a["id"], f"deck:like:{pid_b}")
    cb.route(store, a["id"], f"request:go:{pid_b}:text")

    report = store.cycle_report(a["id"])
    assert report["shown"] == 2, "в проходе показано две карточки"
    assert report["likes"] == 1
    assert report["requests"] == 0, "заявка ещё не отправлена — открыт только экран заявки"

    cb.submit_request(store, a["id"], "text", "привет")
    assert store.cycle_report(a["id"])["requests"] == 1, "отправленная заявка попадает в отчёт прохода"


def test_empty_deck_screen_shows_scan_report(store: Store):
    from handlers import callbacks as cb
    a = register(store, 9701, "scanner")
    register(store, 9702, "only")
    cb.route(store, a["id"], "deck:next")
    cb.route(store, a["id"], "deck:next")
    screen = cb.route(store, a["id"], "deck:next")
    assert "Скан завершён" in screen.screen.text, "пустая колода подводит итог прохода"


# ── 3. модерация ─────────────────────────────────────────────────────────────
class _FakeChat:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, text, **kw):
        self.sent.append(text)


class _FakeMessage:
    def __init__(self):
        self.photo = None
        self.chat = _FakeChat()

    async def delete(self):
        pass


class _FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.answered: list = []
        self.edited: str | None = None

    async def answer(self, text=None, **kw):
        self.answered.append(text)

    async def edit_message_text(self, text, **kw):
        self.edited = text

    async def edit_message_media(self, **kw):
        pass


class _FakeUpdate:
    def __init__(self, tg_id: int, data: str):
        self.callback_query = _FakeQuery(data)
        self.effective_user = types.SimpleNamespace(id=tg_id, username=f"u{tg_id}",
                                                    first_name=f"U{tg_id}")
        self.effective_chat = types.SimpleNamespace(id=tg_id)


class _FakeContext:
    def __init__(self):
        self.application = types.SimpleNamespace(send_message=self._noop, bot=None)

    async def _noop(self, *a, **kw):
        pass


def _call_mod(store: Store, tg_id: int, data: str):
    bot.STORE = store
    update = _FakeUpdate(tg_id, data)
    asyncio.run(bot.on_callback(update, _FakeContext()))
    return update.callback_query


def test_moderation_is_closed_for_non_admins(store: Store, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [555])
    author = register(store, 9801, "author")
    offender = register(store, 9802, "offender")
    store.report(author["id"], offender["id"], "spam")
    report_id = store.new_reports()[0]["id"]

    query = _call_mod(store, 4242, f"mod:ban:{report_id}")
    assert "Недоступно" in query.answered
    assert store.new_reports(), "жалоба осталась в очереди"
    assert store.profile_by_user(offender["id"])["status"] == "active", "не-админ ничего не забан ил"
    assert store.q("SELECT * FROM moderation_actions") == []


def test_admin_ban_and_reject_work(store: Store, monkeypatch):
    admin_tg = 777
    monkeypatch.setattr(config, "ADMIN_IDS", [admin_tg])
    admin = store.ensure_user(admin_tg, "admin", "Admin")
    author = register(store, 9901, "author2")
    offender = register(store, 9902, "offender2")
    store.report(author["id"], offender["id"], "spam")
    report_id = store.new_reports()[0]["id"]

    _call_mod(store, admin_tg, f"mod:suspend:{report_id}")
    assert store.profile_by_user(offender["id"])["status"] == "paused"
    assert store.new_reports() == [], "жалоба выведена из очереди"

    store.report(author["id"], offender["id"], "spam")
    store.x("UPDATE reports SET status='new' WHERE id = ?", (report_id,))
    _call_mod(store, admin_tg, f"mod:ban:{report_id}")
    assert store.profile_by_user(offender["id"])["status"] == "banned"
    actions = [r["action"] for r in store.q("SELECT * FROM moderation_actions ORDER BY id")]
    assert actions == ["suspend", "ban"]
    assert all(r["moderator_user_id"] == admin["id"] for r in store.q("SELECT * FROM moderation_actions"))


def test_invalid_moderation_action_changes_nothing(store: Store, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [888])
    author = register(store, 9951, "author3")
    offender = register(store, 9952, "offender3")
    store.report(author["id"], offender["id"], "spam")
    report_id = store.new_reports()[0]["id"]

    _call_mod(store, 888, f"mod:delete_all:{report_id}")
    assert store.new_reports(), "неизвестное действие не обрабатывает жалобу"
    assert store.q("SELECT * FROM moderation_actions") == []
    with pytest.raises(ValueError):
        store.moderation_action(report_id, 1, "delete_all")


# ── 4. напоминания ───────────────────────────────────────────────────────────
def test_reminder_only_for_stale_but_not_dead_users(store: Store):
    register(store, 10001, "stale")     # действие 3 дня назад
    register(store, 10002, "active")    # действие сегодня
    register(store, 10003, "fresh")     # действие 6 часов назад
    register(store, 10004, "dead")      # действие 20 дней назад
    register(store, 10005, "paused")

    _event_at(store, store.user_by_tg(10001)["id"], "card_skipped", 3)
    _event_at(store, store.user_by_tg(10002)["id"], "card_liked", 0.2)
    _event_at(store, store.user_by_tg(10003)["id"], "card_skipped", 0.25)
    _event_at(store, store.user_by_tg(10004)["id"], "card_skipped", 20)
    _event_at(store, store.user_by_tg(10005)["id"], "card_skipped", 3)
    prof = store.profile_by_user(store.user_by_tg(10005)["id"])
    store.set_profile_status(prof["id"], "paused", visible=0, reason="manual")

    candidates = {c["telegram_user_id"] for c in store.reminder_candidates()}
    assert 10001 in candidates, "тот, кто остыл 1–7 дней назад, получает напоминание"
    assert 10002 not in candidates, "активного сейчас не трогаем"
    assert 10003 not in candidates, "не чаще раза в сутки"
    assert 10004 not in candidates, "ушедшего больше недели не возвращаем"
    assert 10005 not in candidates, "выключенную анкету не будим"


def test_reminder_is_recorded_once_per_day(store: Store):
    user = register(store, 10101, "remindme")
    _event_at(store, user["id"], "card_skipped", 3)
    assert store.record_reminder(user["id"]) is True
    assert store.record_reminder(user["id"]) is False, "второе напоминание в сутки не отправляем"
    assert store.has_event_since(user["id"], "reminder_sent", timedelta(days=1)) is True


def test_blocked_bot_is_never_reminded(store: Store):
    user = register(store, 10201, "blocked")
    _event_at(store, user["id"], "card_skipped", 2)
    store.set_bot_blocked(user["id"], True)
    assert store.reminder_candidates() == []
