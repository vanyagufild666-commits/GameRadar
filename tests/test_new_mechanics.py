from __future__ import annotations

import os
import tempfile

import pytest

import config
from db.store import Store, now_str
from handlers import callbacks as cb
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


def test_referral_is_deep_linked_deduplicated_and_confirmed_once(store: Store):
    referrer = register(store, 9001, "referrer")
    invited = register(store, 9002, "invited")
    targets = [store.ensure_user(9010 + i, f"t{i}", f"T{i}") for i in range(3)]

    assert store.record_referral(referrer["id"], 9002, source="ref_1") is True
    assert store.record_referral(referrer["id"], 9002, source="ref_1") is False
    assert store.record_referral(referrer["id"], 9001, source="ref_1") is False
    assert store.referral_for_invited(invited["id"])["rewarded"] == 0

    cycle = store.current_cycle(invited["id"])
    for target in targets:
        store.record_view(invited["id"], target["id"], cycle, "skip")
    confirmed = store.confirm_referral(invited["id"])
    assert confirmed and confirmed["referrer_user_id"] == referrer["id"]
    assert store.confirm_referral(invited["id"]) is None
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT + 10
    assert store.q1("SELECT consumed_cards FROM referral_bonuses")["consumed_cards"] == 0


def test_referral_confirmation_requires_active_profile_and_three_decisions(store: Store):
    referrer = register(store, 9021, "referrer2")
    invited = register(store, 9022, "invited2")
    target = store.ensure_user(9023, "target", "Target")
    assert store.record_referral(referrer["id"], 9022)
    cycle = store.current_cycle(invited["id"])
    for _ in range(2):
        store.record_view(invited["id"], target["id"], cycle, "skip")
    assert store.confirm_referral(invited["id"]) is None
    store.record_view(invited["id"], target["id"], cycle, "shown")
    assert store.confirm_referral(invited["id"]) is None
    store.record_view(invited["id"], target["id"], cycle, "like")
    assert store.confirm_referral(invited["id"])["rewarded"] == 1


def test_share_screen_uses_internal_referral_payload_and_explains_confirmation(store: Store):
    user = register(store, 9031, "sharer")
    result = cb.route(store, user["id"], "ref:share")
    actions = [action for row in result.screen.rows for _, action in row]
    assert any(f"?start=ref_{user['id']}" in action for action in actions)
    assert "3 решений" in result.screen.text
    assert "одно засчитанное приглашение" in result.screen.text


def test_cycle_report_is_shown_when_deck_is_empty(store: Store):
    viewer = register(store, 9041, "viewer")
    target = register(store, 9042, "target")
    cycle = store.current_cycle(viewer["id"])
    pid = store.profile_by_user(target["id"])["id"]
    cb.route(store, viewer["id"], "deck:next")
    cb.route(store, viewer["id"], f"deck:like:{pid}")
    empty = cb.route(store, viewer["id"], "deck:next")
    assert "РАДАР ЧИСТ" in empty.screen.text
    assert "Скан завершён: 1 сигналов, 1 отклика, 0 заявка" in empty.screen.text
    assert store.cycle_report(viewer["id"], cycle)["likes"] == 1


def test_moderation_actions_resolve_report_and_change_profile(store: Store):
    moderator = store.ensure_user(9051, "admin", "Admin")
    reporter = register(store, 9052, "reporter2")
    target = register(store, 9053, "target2")
    store.report(reporter["id"], target["id"], "spam")
    report = store.new_reports()[0]
    result = store.moderation_action(report["id"], moderator["id"], "ban")
    assert result["status"] == "resolved"
    assert store.profile_by_user(target["id"])["status"] == "banned"
    assert store.profile_by_user(target["id"])["is_visible"] == 0
    action = store.q1("SELECT action, moderator_user_id FROM moderation_actions WHERE report_id = ?", (report["id"],))
    assert action["action"] == "ban" and action["moderator_user_id"] == moderator["id"]


def test_referral_reward_has_seven_day_cap(store: Store):
    referrer = register(store, 9071, "capped_referrer")
    for idx in range(6):
        invited = register(store, 9080 + idx, f"invited{idx}")
        assert store.record_referral(referrer["id"], 9080 + idx)
        target = store.ensure_user(9090 + idx, f"target{idx}", f"Target{idx}")
        cycle = store.current_cycle(invited["id"])
        for decision in ("skip", "skip", "like"):
            store.record_view(invited["id"], target["id"], cycle, decision)
        if idx < 5:
            assert store.confirm_referral(invited["id"])
        else:
            assert store.confirm_referral(invited["id"]) is None
    assert store.daily_card_limit(referrer["id"]) == config.DAILY_CARD_LIMIT + 50


def test_referral_bonus_consumption_is_idempotent_for_repeated_limit_checks(store: Store):
    referrer = register(store, 9101, "consumer")
    invited = register(store, 9102, "consumer_invited")
    target = store.ensure_user(9103, "consumer_target", "Target")
    assert store.record_referral(referrer["id"], 9102)
    cycle = store.current_cycle(invited["id"])
    for action in ("skip", "skip", "skip"):
        store.record_view(invited["id"], target["id"], cycle, action)
    assert store.confirm_referral(invited["id"])
    for idx in range(config.DAILY_CARD_LIMIT + 2):
        store.record_view(referrer["id"], target["id"], store.current_cycle(referrer["id"]), "shown")
    store.consume_referral_cards(referrer["id"], config.DAILY_CARD_LIMIT + 1)
    store.consume_referral_cards(referrer["id"], config.DAILY_CARD_LIMIT + 2)
    assert store.q1("SELECT consumed_cards FROM referral_bonuses")["consumed_cards"] == 2


def test_reminder_candidates_require_old_activity_new_cards_and_daily_dedupe(store: Store):
    viewer = register(store, 9061, "reminder_viewer")
    register(store, 9062, "reminder_candidate")
    store.log_event(viewer["id"], "card_skipped")
    store.x("UPDATE events SET created_at = datetime('now', '-2 day') WHERE user_id = ? AND name = 'card_skipped'",
            (viewer["id"],))
    rows = store.reminder_candidates()
    assert rows and rows[0]["id"] == viewer["id"] and rows[0]["available"] >= 1
    assert store.record_reminder(viewer["id"]) is True
    assert store.record_reminder(viewer["id"]) is False
    assert store.reminder_candidates() == []
    store.set_bot_blocked(viewer["id"], True)
    assert store.reminder_candidates() == []
