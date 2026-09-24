"""Тесты колоды: кулдауны, фильтры, скоринг, антизацикливание."""
from __future__ import annotations

from datetime import datetime, timedelta

import config
from db.store import FMT
from domain.deck import (DeckContext, Viewer, apply_exploration_cap, cooldown_hours,
                         exhausted_reason, is_new_profile, next_eligible, normalize_candidate,
                         passes_hard_filters, pick, rank, relaxation_advice, score, tie_breaker)

NOW = datetime(2026, 9, 24, 12, 0, 0)


def viewer(**kw) -> Viewer:
    base = dict(user_id=1, profile_id=1, age=25, country_code="RU", city="Омск",
                languages={"ru", "en"}, games={"valorant", "dota 2"})
    base.update(kw)
    return Viewer(**base)


def cand(uid: int, **kw) -> dict:
    base = dict(profile_id=uid * 10, user_id=uid, gender="m", age=26, country_code="RU",
                city="Омск", bio="Ищу спокойную пати, играю вечером после работы, без токсичности",
                photo_file_id="AgAC", published_at=(NOW - timedelta(days=10)).strftime(FMT),
                last_active_at=NOW.strftime(FMT), languages={"ru"}, games={"valorant"},
                skip_count=0, impression_count=0, last_shown_at=None, next_eligible_at=None,
                is_exhausted=0, like_pending_until=None)
    base.update(kw)
    return base


def ctx(**kw) -> DeckContext:
    base = dict(now=NOW, cycle_no=1, shown_in_cycle=set(), last_served_user_id=None, daily_salt="s1")
    base.update(kw)
    return DeckContext(**base)


# ── кулдауны ─────────────────────────────────────────────────────────────────
def test_cooldown_table_matches_policy():
    assert cooldown_hours("expired") == config.COOLDOWN_AFTER_SHOWN_H == 6
    assert cooldown_hours("opened") == config.COOLDOWN_OPENED_H == 12
    assert cooldown_hours("like") == config.COOLDOWN_LIKE_UNANSWERED_H
    assert cooldown_hours("unlike") == config.COOLDOWN_UNLIKE_H
    assert cooldown_hours("mutual") is None


def test_skip_cooldown_grows_and_caps():
    assert cooldown_hours("skip", 1) == 24
    assert cooldown_hours("skip", 2) == 48
    assert cooldown_hours("skip", 3) == 96
    assert cooldown_hours("skip", 10) == config.COOLDOWN_SKIP_MAX_H


def test_next_eligible_and_mutual_exclusion():
    assert next_eligible("skip", 2, NOW) == NOW + timedelta(hours=48)
    assert next_eligible("mutual", 0, NOW) is None


# ── жёсткие фильтры ──────────────────────────────────────────────────────────
def test_self_is_never_shown():
    assert not passes_hard_filters(viewer(user_id=7), cand(7), NOW)


def test_active_cooldown_blocks():
    future = (NOW + timedelta(hours=1)).strftime(FMT)
    assert not passes_hard_filters(viewer(), cand(2, next_eligible_at=future), NOW)
    past = (NOW - timedelta(hours=1)).strftime(FMT)
    assert passes_hard_filters(viewer(), cand(2, next_eligible_at=past), NOW)


def test_language_and_game_requirements():
    v = viewer(require_common_language=True, require_common_game=False)
    assert passes_hard_filters(v, cand(2, languages={"en"}), NOW)
    assert not passes_hard_filters(v, cand(2, languages={"kk"}), NOW)

    v_game = viewer(require_common_game=True)
    assert passes_hard_filters(v_game, cand(2, games={"Valorant", "CS2"}), NOW)
    assert not passes_hard_filters(v_game, cand(2, games={"cs2"}), NOW)


def test_geo_modes():
    assert passes_hard_filters(viewer(geo_mode="country"), cand(2, city="Тверь"), NOW)
    assert not passes_hard_filters(viewer(geo_mode="country"), cand(2, country_code="KZ"), NOW)
    assert passes_hard_filters(viewer(geo_mode="city"), cand(2), NOW)
    assert not passes_hard_filters(viewer(geo_mode="city"), cand(2, city="Тверь"), NOW)


def test_age_range_enforced():
    assert not passes_hard_filters(viewer(min_age=30), cand(2, age=22), NOW)
    assert passes_hard_filters(viewer(min_age=30), cand(2, age=31), NOW)


# ── скоринг ──────────────────────────────────────────────────────────────────
def test_new_profile_boost_and_decay():
    fresh = cand(2, published_at=NOW.strftime(FMT), last_active_at=NOW.strftime(FMT))
    old = cand(3, published_at=(NOW - timedelta(days=30)).strftime(FMT),
               last_active_at=(NOW - timedelta(days=10)).strftime(FMT))
    assert is_new_profile(fresh, NOW) and not is_new_profile(old, NOW)
    assert score(viewer(), fresh, ctx()) > score(viewer(), old, ctx())


def test_more_common_games_score_higher():
    one = cand(2, games={"valorant"})
    three = cand(3, games={"valorant", "dota 2", "cs2"})
    assert score(viewer(), three, ctx()) > score(viewer(), one, ctx())


def test_skip_count_is_penalty():
    assert score(viewer(), cand(2, skip_count=5), ctx()) < score(viewer(), cand(3, skip_count=0), ctx())


def test_recent_repeat_is_penalised():
    recent = cand(2, last_shown_at=(NOW - timedelta(hours=3)).strftime(FMT))
    long_ago = cand(3, last_shown_at=(NOW - timedelta(hours=40)).strftime(FMT))
    assert score(viewer(), recent, ctx()) < score(viewer(), long_ago, ctx())


def test_tie_breaker_is_deterministic_and_salt_sensitive():
    a = tie_breaker(1, 2, 1, "salt")
    assert a == tie_breaker(1, 2, 1, "salt")
    assert a != tie_breaker(1, 2, 1, "other-salt")


# ── выдача и антизацикливание ────────────────────────────────────────────────
def test_rank_excludes_shown_and_last_served():
    pool = [cand(2), cand(3), cand(4)]
    ranked = rank(viewer(), pool, ctx(shown_in_cycle={3}))
    assert [c["user_id"] for c, _ in ranked] == [2, 4]

    ranked2 = rank(viewer(), pool, ctx(last_served_user_id=2))
    assert all(c["user_id"] != 2 for c, _ in ranked2)


def test_pick_returns_best_and_none_when_empty():
    pool = [cand(2, games={"valorant"}), cand(3, games={"valorant", "dota 2"})]
    best = pick(viewer(), pool, ctx())
    assert best is not None and best["user_id"] == 3
    assert pick(viewer(), [], ctx()) is None


def test_pick_never_repeats_same_candidate_in_cycle():
    pool = [cand(2), cand(3)]
    first = pick(viewer(), pool, ctx())
    second = pick(viewer(), pool, ctx(shown_in_cycle={first["user_id"]}, last_served_user_id=first["user_id"]))
    assert second is not None and second["user_id"] != first["user_id"]
    third = pick(viewer(), pool, ctx(shown_in_cycle={2, 3}, last_served_user_id=3))
    assert third is None


def test_exploration_cap_promotes_non_new_when_share_exhausted():
    fresh = cand(2, published_at=NOW.strftime(FMT))
    old = cand(3, published_at=(NOW - timedelta(days=20)).strftime(FMT),
               last_active_at=(NOW - timedelta(hours=2)).strftime(FMT))
    ranked = rank(viewer(), [fresh, old], ctx(now=NOW, new_served=3, total_served=4))
    assert ranked[0][0]["user_id"] == 3, "при выбранной доле свежих вперёд идёт не-новая анкета"

    ranked2 = rank(viewer(), [fresh, old], ctx(now=NOW, new_served=0, total_served=4))
    assert ranked2[0][0]["user_id"] == 2, "пока доля не выбрана, свежая анкета в приоритете"


def test_apply_exploration_cap_noop_when_below_share():
    ranked = [(cand(2), 5.0), (cand(3), 1.0)]
    assert apply_exploration_cap(ranked, ctx(new_served=0, total_served=10)) == ranked


# ── деградация при малой базе ────────────────────────────────────────────────
def test_relaxation_advice_only_for_small_base():
    assert relaxation_advice(strict_count=50, pool_size=100) == []
    assert "invite_friends" in relaxation_advice(strict_count=3, pool_size=5)
    assert "expand_filters" in relaxation_advice(strict_count=0, pool_size=0)


def test_exhausted_reason_explains_why():
    pool = [cand(2, next_eligible_at=(NOW + timedelta(hours=5)).strftime(FMT))]
    assert exhausted_reason(viewer(), pool, ctx()) == "all_on_cooldown"
    assert exhausted_reason(viewer(), [cand(3)], ctx(shown_in_cycle={3})) == "all_shown"
    assert exhausted_reason(viewer(), [], ctx()) == "no_candidates"


def test_normalize_candidate_lowercases_games():
    c = normalize_candidate(cand(2, games={"VALORANT"}, languages={"RU"}))
    assert c["games"] == {"valorant"} and c["langs"] == {"ru"}
