"""Интеграционные тесты хранилища: схема, пул кандидатов, лайки/мэтчи, заявки."""
from __future__ import annotations

import os
import tempfile

import pytest

from db.store import Store, cooldown_str, now_str


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


def make_pair(store: Store) -> tuple[dict, dict]:
    a = store.ensure_user(1001, "alpha", "Alpha")
    b = store.ensure_user(1002, "beta", "Beta")
    draft_a = dict(gender="m", age=25, country="RU", city="Омск", bio="Спокойный игрок, ищу пати вечером",
                   languages=["ru", "en"], games=["Valorant", "Dota 2"], photo_file_id="fidA")
    draft_b = dict(gender="f", age=24, country="RU", city="Омск", bio="Люблю кооп, играю после 20:00 каждый день",
                   languages=["ru"], games=["Valorant"], photo_file_id="fidB")
    store.create_profile(a["id"], draft_a, publish=True)
    store.create_profile(b["id"], draft_b, publish=True)
    return a, b


def test_schema_creates_and_wal_is_on(store: Store):
    tables = {r["name"] for r in store.q("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "profiles", "likes", "matches", "deck_state", "view_events",
            "message_requests", "reports", "events"} <= tables
    assert store.q1("PRAGMA journal_mode")["journal_mode"].lower() == "wal"
    assert store.q1("PRAGMA foreign_keys")["foreign_keys"] == 1


def test_candidate_pool_returns_the_other_profile_only(store: Store):
    a, b = make_pair(store)
    pool = store.candidate_pool(a["id"], 18, 99)
    assert [c["user_id"] for c in pool] == [b["id"]]
    assert pool[0]["languages"] == {"ru"}
    assert {g.lower() for g in pool[0]["games"]} == {"valorant"}


def test_paused_profile_leaves_the_pool(store: Store):
    a, b = make_pair(store)
    prof_b = store.profile_by_user(b["id"])
    store.set_profile_status(prof_b["id"], "paused", visible=0, reason="manual")
    assert store.candidate_pool(a["id"], 18, 99) == []


def test_like_and_mutual_match(store: Store):
    a, b = make_pair(store)
    assert store.like(a["id"], b["id"]) is True
    assert store.has_like(a["id"], b["id"]) is True
    assert store.active_match(a["id"], b["id"]) is None

    store.like(b["id"], a["id"])
    match = store.create_match(a["id"], b["id"])
    assert match and match["status"] == "active"
    assert store.likes_given(a["id"]) == 1 and store.likes_received(a["id"]) == 1

    # matched pair уходит из выдачи
    assert store.candidate_pool(a["id"], 18, 99) == []


def test_consent_flow_and_block_precedence(store: Store):
    a, b = make_pair(store)
    store.like(a["id"], b["id"])
    store.like(b["id"], a["id"])
    match = store.create_match(a["id"], b["id"])
    assert match["a_consent"] == 0 and match["b_consent"] == 0

    updated = store.set_consent(match["id"], a["id"])
    assert updated["a_consent"] == 1 and updated["b_consent"] == 0
    updated = store.set_consent(match["id"], b["id"])
    assert updated["a_consent"] == 1 and updated["b_consent"] == 1

    store.block(a["id"], b["id"], reason="toxicity")
    assert store.blocked_between(a["id"], b["id"]) is True
    assert store.match_by_id(match["id"])["status"] == "blocked"
    assert store.active_match(a["id"], b["id"]) is None


def test_blocks_hide_both_directions(store: Store):
    a, b = make_pair(store)
    store.block(b["id"], a["id"])
    assert store.candidate_pool(a["id"], 18, 99) == []
    assert store.candidate_pool(b["id"], 18, 99) == []


def test_message_request_flow_and_idempotency(store: Store):
    a, b = make_pair(store)
    rid = store.create_request(a["id"], b["id"], "text", "Привет, ищу пати в Valorant", "k1")
    assert rid and rid > 0
    assert store.create_request(a["id"], b["id"], "text", "дубль", "k1") is None  # idempotency

    store.add_attachment(rid, "photo", "fid-1", caption="мой сетап") if False else None
    inbox = store.inbox(b["id"])
    assert len(inbox) == 1 and inbox[0]["kind"] == "text"
    assert store.requests_today(a["id"]) == 1

    store.respond_request(rid, "accepted")
    assert store.inbox(b["id"]) == []
    assert store.request_by_id(rid)["status"] == "accepted"


def test_attachment_is_stored(store: Store):
    a, b = make_pair(store)
    rid = store.create_request(a["id"], b["id"], "video_note", None, "k2")
    store.add_attachment(rid, "video_note", "file-id-42", caption=None)
    rows = store.q("SELECT * FROM message_attachments WHERE request_id = ?", (rid,))
    assert rows and rows[0]["telegram_file_id"] == "file-id-42"


def test_report_and_resolution(store: Store):
    a, b = make_pair(store)
    store.report(a["id"], b["id"], "spam", "реклама в заявке")
    pending = store.new_reports()
    assert len(pending) == 1 and pending[0]["reason"] == "spam"
    store.resolve_report(pending[0]["id"])
    assert store.new_reports() == []


def test_deck_state_progress_and_cycles(store: Store):
    a, b = make_pair(store)
    cycle = store.current_cycle(a["id"])
    assert cycle == 1

    store.mark_served(a["id"], cycle, b["id"])
    assert store.shown_in_cycle(a["id"], cycle) == {b["id"]}
    assert store.last_served_candidate(a["id"]) == b["id"]
    assert (store.deck_row(a["id"], b["id"]) or {})["impression_count"] == 1

    expiry = cooldown_str(24)
    store.record_view(a["id"], b["id"], cycle, "skip", expiry)
    store.upsert_deck_state(a["id"], b["id"], skip_count=1, last_action="skip",
                            next_eligible_at=expiry, last_action_at=now_str())
    assert store.count_available(a["id"]) == 0

    store.start_cycle(a["id"], 2)
    assert store.current_cycle(a["id"]) == 2


def test_open_impression_tracking(store: Store):
    a, b = make_pair(store)
    cycle = store.current_cycle(a["id"])
    assert store.open_impression(a["id"]) is None
    store.record_view(a["id"], b["id"], cycle, "shown")
    imp = store.open_impression(a["id"])
    assert imp and imp["candidate_user_id"] == b["id"]
    store.record_view(a["id"], b["id"], cycle, "skip", cooldown_str(24))
    assert store.open_impression(a["id"]) is None


def test_registration_draft_roundtrip(store: Store):
    a = store.ensure_user(1003, None, "Gamma")
    store.save_draft(a["id"], "age", {"gender": "f", "languages": ["ru"]}, attempts=1)
    state, draft, attempts = store.load_draft(a["id"])
    assert state == "age" and draft["gender"] == "f" and attempts == 1
    store.clear_draft(a["id"])
    assert store.load_draft(a["id"]) == ("", {}, 0)


def test_stats_counts_everything(store: Store):
    a, b = make_pair(store)
    store.like(a["id"], b["id"])
    store.report(a["id"], b["id"], "fake")
    stats = store.stats()
    assert stats["profiles_active"] == 2 and stats["likes"] == 1 and stats["reports_new"] == 1
