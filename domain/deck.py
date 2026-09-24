"""Доменная логика колоды: фильтры, скоринг, кулдауны, антизацикливание.

Модуль намеренно чистый: принимает словари/датаклассы, не знает про Telegram.
Именно его покрывают тесты (tests/test_deck.py).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

import config
from db.store import parse_dt

# действие из view_events → сколько часов карточка не показывается
COOLDOWN_TABLE: dict[str, Optional[float]] = {
    "shown": config.COOLDOWN_AFTER_SHOWN_H,
    "expired": config.COOLDOWN_AFTER_SHOWN_H,
    "opened": config.COOLDOWN_OPENED_H,
    "skip": config.COOLDOWN_SKIP_BASE_H,
    "like": config.COOLDOWN_LIKE_UNANSWERED_H,
    "unlike": config.COOLDOWN_UNLIKE_H,
    "mutual": None,          # пара выведена из колоды до закрытия мэтча
}


@dataclass
class Viewer:
    """Смотрящий: профиль + его фильтры."""
    user_id: int
    profile_id: int
    age: int
    country_code: str
    city: Optional[str] = None
    languages: set[str] = field(default_factory=set)
    games: set[str] = field(default_factory=set)
    min_age: int = config.MIN_AGE
    max_age: int = config.MAX_AGE
    geo_mode: str = "any"
    require_common_language: bool = True
    require_common_game: bool = False
    only_active_recent: bool = False
    search_mode: str = "city"
    gender: str = "x"
    gender_pref: str = "any"


@dataclass
class DeckContext:
    """Всё изменчивое состояние одного запроса «дай следующую карточку»."""
    now: datetime
    cycle_no: int
    shown_in_cycle: set[int] = field(default_factory=set)
    last_served_user_id: Optional[int] = None
    daily_salt: str = ""
    new_served: int = 0        # сколько свежих анкет уже показано в сессии
    total_served: int = 0      # всего показов в сессии


def normalize_candidate(row: dict) -> dict:
    """Единый вид кандидата: игры в нижнем регистре, языки — множество."""
    cand = dict(row)
    games = cand.get("games") or set()
    cand["games"] = {str(g).strip().lower() for g in games if str(g).strip()}
    langs = cand.get("languages") or set()
    cand["langs"] = {str(l).strip().lower() for l in langs if str(l).strip()}
    return cand


def cooldown_hours(action: str, skip_count: int = 0) -> Optional[float]:
    """Часы кулдауна для действия. Пропуск растёт экспоненциально и упирается в потолок.

    None означает «исключить из колоды» (взаимный лайк).
    """
    base = COOLDOWN_TABLE.get(action, config.COOLDOWN_AFTER_SHOWN_H)
    if base is None:
        return None
    if action == "skip":
        exponent = max(0, skip_count - 1)
        return min(config.COOLDOWN_SKIP_BASE_H * (2 ** exponent), config.COOLDOWN_SKIP_MAX_H)
    return base


def next_eligible(action: str, skip_count: int, now: datetime) -> Optional[datetime]:
    hours = cooldown_hours(action, skip_count)
    return None if hours is None else now + timedelta(hours=hours)


def cooldown_active(value: Optional[str], now: datetime) -> bool:
    dt = parse_dt(value)
    return bool(dt and dt > now)


def _langs(cand: dict) -> set[str]:
    """Языки кандидата: принимаем и сырую строку из БД ('languages'), и нормализованный набор."""
    raw = cand.get("langs")
    if raw is None:
        raw = cand.get("languages") or set()
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def _games(cand: dict) -> set[str]:
    """Игры кандидата — так же терпимо к источнику данных."""
    raw = cand.get("games") or set()
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def common_languages(viewer: Viewer, cand: dict) -> list[str]:
    return sorted(viewer.languages & _langs(cand))


def common_games(viewer: Viewer, cand: dict) -> list[str]:
    return sorted(viewer.games & _games(cand))


def passes_hard_filters(viewer: Viewer, cand: dict, now: datetime) -> bool:
    """Жёсткие условия: без них карточка не показывается никогда (в этом цикле)."""
    if cand["user_id"] == viewer.user_id:
        return False
    if cand.get("is_exhausted") == 1:
        return False
    if cooldown_active(cand.get("next_eligible_at"), now):
        return False
    if not (viewer.min_age <= int(cand["age"]) <= viewer.max_age):
        return False
    if viewer.gender_pref == "same" and viewer.gender in {"m", "f"} and cand.get("gender") != viewer.gender:
        return False
    if viewer.gender_pref == "other" and viewer.gender in {"m", "f"} and cand.get("gender") not in ({"m", "f"} - {viewer.gender}):
        return False
    if viewer.search_mode == "genre":
        own_genres = config.game_genres(viewer.games)
        candidate_genres = config.game_genres(_games(cand))
        if not own_genres or not own_genres.intersection(candidate_genres):
            return False
    if viewer.require_common_language and not common_languages(viewer, cand):
        return False
    if viewer.require_common_game and not common_games(viewer, cand):
        return False
    if viewer.geo_mode == "country" and cand["country_code"] != viewer.country_code:
        return False
    if viewer.geo_mode == "city":
        if cand["country_code"] != viewer.country_code:
            return False
        if (cand.get("city") or "").strip().lower() != (viewer.city or "").strip().lower():
            return False
    return True


def _hours_since(value: Optional[str], now: datetime) -> float:
    dt = parse_dt(value)
    if not dt:
        return 10_000.0
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def is_new_profile(cand: dict, now: datetime) -> bool:
    return _hours_since(cand.get("published_at"), now) <= config.NEW_PROFILE_BOOST_H


def tie_breaker(viewer_id: int, candidate_id: int, cycle_no: int, salt: str) -> float:
    raw = f"{viewer_id}:{candidate_id}:{cycle_no}:{salt}".encode()
    return int.from_bytes(hashlib.blake2s(raw, digest_size=4).digest(), "big") / 2 ** 32


def score(viewer: Viewer, cand: dict, ctx: DeckContext) -> float:
    """Скор карточки: чем выше, тем раньше показывается."""
    now = ctx.now
    total = 0.0

    # свежие анкеты получают буст (долю выдачи ограничивает apply_exploration_cap)
    if is_new_profile(cand, now):
        total += config.W_NEW_PROFILE

    # активность: свежий last_active_at весит больше (экспоненциальное затухание)
    total += min(config.W_ACTIVITY_MAX,
                 config.W_ACTIVITY_MAX * math.exp(-_hours_since(cand.get("last_active_at"), now) / 72.0))

    # общий язык и игры
    langs = common_languages(viewer, cand)
    if langs:
        total += min(config.W_LANG_MAX, config.W_LANG_FIRST + config.W_LANG_NEXT * (len(langs) - 1))
    games = common_games(viewer, cand)
    if games:
        total += min(config.W_GAME_MAX, config.W_GAME_FIRST + config.W_GAME_NEXT * (len(games) - 1))

    # география
    if (cand.get("city") or "").strip() and (cand.get("city") or "").strip().lower() == (viewer.city or "").strip().lower():
        total += config.W_GEO_CITY
    elif cand["country_code"] == viewer.country_code:
        total += config.W_GEO_COUNTRY

    # близкий возраст (полный диапазон уже отфильтрован жёстко)
    if abs(int(cand["age"]) - viewer.age) <= 5:
        total += config.W_AGE_FIT

    # качественно заполненная карточка
    if cand.get("photo_file_id") and len(cand.get("bio") or "") >= 80:
        total += config.W_RICH_CARD

    # штрафы
    if cand["user_id"] in ctx.shown_in_cycle:
        total += config.P_SHOWN_IN_CYCLE
    total += max(config.P_SKIP_MAX, config.P_SKIP_PER * int(cand.get("skip_count") or 0))
    if _hours_since(cand.get("last_shown_at"), now) < config.ANTI_REPEAT_H:
        total += config.P_REPEAT_24H

    # лайк, который давно висит без ответа — показываем реже
    pending = parse_dt(cand.get("like_pending_until"))
    if pending and pending < now:
        total += config.P_STALE_LIKE

    total += tie_breaker(viewer.user_id, cand["user_id"], ctx.cycle_no, ctx.daily_salt)
    return total


def apply_exploration_cap(ranked: list[tuple[dict, float]], ctx: DeckContext) -> list[tuple[dict, float]]:
    """Свежие анкеты не должны забирать больше 30% выдачи.

    Если лимит уже выбран, лучший «не новый» кандидат поднимается на первое место.
    """
    if not ranked:
        return ranked
    share = (ctx.new_served / ctx.total_served) if ctx.total_served else 0.0
    if share < config.NEW_PROFILE_MAX_SHARE:
        return ranked
    if not is_new_profile(ranked[0][0], ctx.now):
        return ranked
    for idx, (cand, _) in enumerate(ranked):
        if not is_new_profile(cand, ctx.now):
            rest = ranked[:idx] + ranked[idx + 1:]
            return [ranked[idx]] + rest
    return ranked


def rank(viewer: Viewer, candidates: Iterable[dict], ctx: DeckContext) -> list[tuple[dict, float]]:
    eligible = []
    for raw in candidates:
        cand = normalize_candidate(raw)
        if not passes_hard_filters(viewer, cand, ctx.now):
            continue
        if cand["user_id"] in ctx.shown_in_cycle:
            continue
        if ctx.last_served_user_id is not None and cand["user_id"] == ctx.last_served_user_id:
            continue
        eligible.append((cand, score(viewer, cand, ctx)))
    eligible.sort(key=lambda pair: (-pair[1], pair[0]["user_id"]))
    return apply_exploration_cap(eligible, ctx)


def pick(viewer: Viewer, candidates: Iterable[dict], ctx: DeckContext) -> Optional[dict]:
    """Следующая карточка или None, если колода исчерпана."""
    ranked = rank(viewer, candidates, ctx)
    return ranked[0][0] if ranked else None


def relaxation_advice(strict_count: int, pool_size: int, viewer: Optional[Viewer] = None) -> list[str]:
    """Что предложить, когда в базе мало анкет: ослабляем только необязательное."""
    advice: list[str] = []
    if viewer and viewer.search_mode == "genre" and not config.game_genres(viewer.games):
        return ["switch_mode_city", "expand_filters"]
    if strict_count >= config.SMALL_BASE:
        return advice
    if pool_size == 0:
        advice.append("expand_filters")
    if pool_size < config.SMALL_BASE:
        advice.append("switch_language_optional")
        advice.append("invite_friends")
    return advice


def exhausted_reason(viewer: Viewer, candidates: list[dict], ctx: DeckContext) -> str:
    """Честная причина пустой колоды — показывается пользователю."""
    if not candidates:
        return "no_candidates"
    locked = 0
    shown = 0
    for raw in candidates:
        cand = normalize_candidate(raw)
        if cooldown_active(cand.get("next_eligible_at"), ctx.now):
            locked += 1
        if cand["user_id"] in ctx.shown_in_cycle:
            shown += 1
    if shown:
        return "all_shown"
    if locked:
        return "all_on_cooldown"
    return "no_candidates"
