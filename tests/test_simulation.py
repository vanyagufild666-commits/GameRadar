"""Симуляция колоды: 60 дней одним пользователем на базе из 12 анкет.

Проверяет главное требование заказчика: одни и те же анкеты не вылетают раз за разом,
но и не пропадают навсегда — у каждой свой таймер.
"""
from __future__ import annotations

import os
import random
import tempfile
from datetime import datetime, timedelta

import pytest

import config
from db.store import FMT, Store
from domain.deck import DeckContext, Viewer, pick

DAY = 24


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


def seed_players(store: Store, count: int = 12) -> dict:
    games = ["Valorant", "Dota 2", "CS2", "Helldivers 2", "Minecraft"]
    viewer = store.ensure_user(9000, "sim_viewer", "Viewer")
    store.create_profile(viewer["id"], dict(
        gender="m", age=26, country="RU", city="Омск", bio="Ищу стабильную пати на вечер, без токсичности",
        languages=["ru", "en"], games=games, photo_file_id="fid0"), publish=True)
    for i in range(count):
        user = store.ensure_user(9100 + i, f"player{i}", f"Player{i}")
        store.create_profile(user["id"], dict(
            gender="f" if i % 2 else "m", age=20 + i % 15, country="RU",
            city="Омск" if i % 3 == 0 else "Тверь",
            bio="Играю вечером после работы, ищу напарников без криков и токсичности",
            languages=["ru"] if i % 4 else ["ru", "en"],
            games=[games[i % len(games)]] + ([games[(i + 1) % len(games)]] if i % 2 else []),
            photo_file_id=f"fid{i}"), publish=True)
    return viewer


def run_simulation(store: Store, viewer: dict, days: int = 60, seed: int = 7) -> list[list[int]]:
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, 20, 0, 0)
    prof = store.profile_by_user(viewer["id"])
    flt = store.get_filters(prof["id"])
    logs: list[list[int]] = []

    for day in range(days):
        now = start + timedelta(days=day)
        cycle = store.current_cycle(viewer["id"]) if day else 1
        store.start_cycle(viewer["id"], cycle)
        served: list[int] = []
        for _ in range(config.DAILY_CARD_LIMIT):
            viewer_obj = Viewer(
                user_id=viewer["id"], profile_id=prof["id"], age=prof["age"],
                country_code=prof["country_code"], city=prof["city"],
                languages=set(store.profile_languages(prof["id"])),
                games=set(store.profile_games(prof["id"])),
                min_age=flt["min_age"], max_age=flt["max_age"], geo_mode=flt["geo_mode"],
                require_common_language=bool(flt["require_common_language"]),
                require_common_game=bool(flt["require_common_game"]))
            ctx = DeckContext(now=now, cycle_no=cycle,
                              shown_in_cycle=store.shown_in_cycle(viewer["id"], cycle),
                              last_served_user_id=store.last_served_candidate(viewer["id"], cycle),
                              daily_salt=f"day-{day}",
                              new_served=0, total_served=0)
            pool = store.candidate_pool(viewer["id"], flt["min_age"], flt["max_age"])
            cand = pick(viewer_obj, pool, ctx)
            if not cand:
                break
            served.append(cand["user_id"])
            store.mark_served(viewer["id"], cycle, cand["user_id"])

            roll = rng.random()
            if roll < 0.45:
                action, hours = "skip", 24 * 2 ** min(int(cand.get("skip_count") or 0), 4)
                store.upsert_deck_state(viewer["id"], cand["user_id"], skip_count=int(cand.get("skip_count") or 0) + 1)
            elif roll < 0.85:
                action, hours = "expired", 6
            else:
                action, hours = "like", 168
            hours = min(hours, config.COOLDOWN_SKIP_MAX_H)
            expiry = (now + timedelta(hours=hours)).strftime(FMT)
            store.record_view(viewer["id"], cand["user_id"], cycle, action, expiry)
            store.upsert_deck_state(viewer["id"], cand["user_id"], cycle_no=cycle,
                                    last_action=action, last_action_at=now.strftime(FMT),
                                    next_eligible_at=expiry)
        logs.append(served)
        # новый проход на следующий день
        store.start_cycle(viewer["id"], cycle + 1)
    return logs


def test_same_card_never_repeats_back_to_back(store: Store):
    viewer = seed_players(store)
    logs = run_simulation(store, viewer)
    for day_cards in logs:
        for prev, nxt in zip(day_cards, day_cards[1:]):
            assert prev != nxt, "одна и та же карточка не показывается дважды подряд"


def test_every_player_is_seen_and_nobody_disappears_forever(store: Store):
    viewer = seed_players(store)
    logs = run_simulation(store, viewer)
    seen: dict[int, int] = {}
    for day_cards in logs:
        for user_id in day_cards:
            seen[user_id] = seen.get(user_id, 0) + 1
    assert len(seen) == 12, "за 60 дней радар должен показать всех игроков базы"
    thin = [uid for uid, count in seen.items() if count < 3]
    assert not thin, f"анкеты, показанные реже трёх раз (пропали навсегда): {thin}"


def test_daily_deck_is_bounded_not_endless(store: Store):
    viewer = seed_players(store)
    logs = run_simulation(store, viewer)
    per_day = [len(day_cards) for day_cards in logs]
    assert max(per_day) <= 12, "в один день больше анкет, чем есть в базе, быть не может"
    assert max(per_day) < 12 or per_day.count(12) <= 2, "почти каждый день колода не должна выдавать всех подряд"
    assert sum(per_day) / len(per_day) >= 2, "колода не должна стоять пустой"


def test_cooldowns_actually_recycle_cards(store: Store):
    viewer = seed_players(store, count=3)
    logs = run_simulation(store, viewer, days=20)
    first_week = [uid for day_cards in logs[:7] for uid in day_cards]
    second_week = [uid for day_cards in logs[7:14] for uid in day_cards]
    assert set(first_week) == set(second_week), "те же анкеты возвращаются после таймера, а не исчезают"


def test_skipped_card_returns_after_its_timer(store: Store):
    viewer = seed_players(store, count=1)
    logs = run_simulation(store, viewer, days=40, seed=3)
    assert logs[0], "в первый день анкета должна показаться"
    card = logs[0][0]
    later_days = [day_cards for day_cards in logs[1:] if card in day_cards]
    assert later_days, "после истечения кулдауна пропущенная анкета возвращается в колоду"
