"""Роутер кнопок и действий. Возвращает Result(screen, effects, alert).

Никаких вызовов Telegram: то, что нужно доставить другому пользователю,
описывается как effect, а bot.py его исполняет.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import config
from db.store import Store, cooldown_str, now_str
from domain.deck import (DeckContext, Viewer, exhausted_reason, normalize_candidate, pick,
                         relaxation_advice)
from domain.validators import validate_bio, validate_games
from handlers import registration as reg
from ui.screens import (Screen, screen_card, screen_error, screen_filters, screen_home,
                        screen_inbox, screen_inbox_item, screen_match, screen_my_profile,
                        screen_need_profile, screen_paused, screen_progress, screen_report,
                        screen_report_done, screen_request_compose, screen_request_sent,
                        screen_request_type, screen_exhausted)

EDITS = {
    "edit_bio": ("bio", "Пришли новый текст анкеты (1–3 строки)."),
    "edit_games": ("games", f"Перечисли игры через запятую (до {config.MAX_GAMES})."),
}


@dataclass
class Result:
    screen: Screen
    effects: list[dict] = field(default_factory=list)
    alert: Optional[str] = None


# ── сборка контекста ─────────────────────────────────────────────────────────
def _profile(store: Store, user_id: int) -> Optional[dict]:
    return store.profile_by_user(user_id)


def _viewer(store: Store, user_id: int) -> Optional[Viewer]:
    """Смотрящий собирается в Store: один источник правды для колоды и счётчиков."""
    return store.viewer(user_id)


def _deck_context(store: Store, user_id: int, viewer: Viewer, cycle: int) -> DeckContext:
    day = store.today_salt()
    return DeckContext(
        now=datetime.now(), cycle_no=cycle,
        shown_in_cycle=store.shown_in_cycle(user_id, cycle),
        last_served_user_id=store.last_served_candidate(user_id, cycle),
        daily_salt=day,
        new_served=store.new_served_today(user_id, day),
        total_served=store.served_today(user_id, day),
    )


def _candidates(store: Store, viewer: Viewer) -> list[dict]:
    return store.candidate_pool(viewer.user_id, viewer.min_age, viewer.max_age,
                                viewer.only_active_recent)


def _compat(viewer: Viewer, cand: dict) -> dict:
    langs = sorted(viewer.languages & set(cand.get("languages") or set()))
    games = sorted(g for g in viewer.games
                   if g.lower() in {str(x).lower() for x in (cand.get("games") or set())})
    return {"languages": langs, "games": sorted(cand.get("games") or []),
            "games_count": len(games), "matched_games": games, "matched_languages": langs}


def _need_profile(store: Store, user_id: int) -> Screen:
    """Нет анкеты: если есть начатый черновик — предлагаем продолжить, а не начинать заново."""
    if store.load_draft_session(user_id):
        return reg.resume_screen(store, user_id)
    return screen_need_profile()


def _profile_screen(store: Store, user_id: int) -> Screen:
    prof = _profile(store, user_id)
    if not prof:
        return _need_profile(store, user_id)
    active = prof["status"] == "active" and prof["is_visible"] == 1
    return screen_my_profile(prof, store.profile_languages(prof["id"]),
                             store.profile_games(prof["id"]),
                             store.times_shown(prof["id"]),
                             store.likes_given(user_id), store.likes_received(user_id), active)


def _home(store: Store, user_id: int) -> Screen:
    prof = _profile(store, user_id)
    if not prof:
        return _need_profile(store, user_id)
    active = prof["status"] == "active" and prof["is_visible"] == 1
    return screen_home(active, store.views_made(user_id), store.likes_given(user_id),
                       store.likes_received(user_id), len(store.inbox(user_id)),
                       store.streak(user_id), store.level(user_id))


# ── главный вход ─────────────────────────────────────────────────────────────
def route(store: Store, user_id: int, data: str) -> Result:
    parts = data.split(":")
    head, tail = parts[0], parts[1:]

    if head == "menu":
        return _menu(store, user_id, tail)
    if head == "deck":
        return _deck(store, user_id, tail)
    if head == "profile":
        return _profile_action(store, user_id, tail)
    if head == "filters":
        return _filters(store, user_id, tail)
    if head == "request":
        return _request(store, user_id, tail)
    if head == "inbox":
        return _inbox(store, user_id, tail)
    if head == "match":
        return _match(store, user_id, tail)
    if head == "report":
        return _report(store, user_id, tail)
    if head == "block":
        return _block(store, user_id, tail)
    if head == "ref":
        return _share(store, user_id)
    return Result(screen_error("Не понял команду. Вернись в меню."))


def _menu(store: Store, user_id: int, tail: list[str]) -> Result:
    what = tail[0] if tail else "home"
    if what == "home":
        return Result(_home(store, user_id))
    if what == "profile":
        return Result(_profile_screen(store, user_id))
    if what == "progress":
        prof = _profile(store, user_id)
        if not prof:
            return Result(screen_need_profile())
        langs = store.profile_languages(prof["id"])
        games = store.profile_games(prof["id"])
        from ui.screens import _fill
        return Result(screen_progress(store.views_made(user_id), store.likes_given(user_id),
                                      store.likes_received(user_id), store.matches_count(user_id),
                                      store.requests_received(user_id), store.streak(user_id),
                                      store.level(user_id), _fill(prof, langs, games)))
    if what == "filters":
        return _filters(store, user_id, [])
    if what == "inbox":
        return Result(screen_inbox(store.inbox_items(user_id)))
    if what == "reports":
        return Result(screen_error("Раздел доступен только через /reports."))
    return Result(_home(store, user_id))


# ── колода ───────────────────────────────────────────────────────────────────
def _deck(store: Store, user_id: int, tail: list[str]) -> Result:
    action = tail[0] if tail else "next"
    viewer = _viewer(store, user_id)
    if not viewer:
        return Result(screen_need_profile())
    prof = _profile(store, user_id)
    if prof["status"] != "active" or prof["is_visible"] != 1:
        return Result(screen_paused(False, len(store.inbox(user_id))),
                      alert="Анкета выключена — включи её, чтобы смотреть карточки.")

    cycle = store.current_cycle(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    limit_reached = store.served_today(user_id, today) >= store.daily_card_limit(user_id)

    if action == "second_pass":
        if limit_reached:
            return Result(screen_exhausted("all_shown", ["come_back_tomorrow"],
                                           store.count_available(user_id)),
                          alert="На сегодня лимит карточек исчерпан.")
        if not store.second_pass_allowed(user_id):
            return Result(screen_exhausted("all_on_cooldown", ["invite_friends"],
                                           store.count_available(user_id)),
                          alert=f"Второй проход доступен не чаще раза в "
                                f"{config.SECOND_PASS_COOLDOWN_H // 24} суток.")
        store.relax_soft_cooldowns(user_id)
        store.log_event(user_id, "second_pass")
        store.start_cycle(user_id, cycle + 1)
        return _serve(store, user_id, viewer, cycle + 1)

    if action == "next":
        if limit_reached:
            return Result(screen_exhausted("all_shown", ["come_back_tomorrow"],
                                           store.count_available(user_id)),
                          alert="На сегодня лимит карточек исчерпан.")
        return _serve(store, user_id, viewer, cycle)

    if action == "card" and tail[1:]:
        cand = _candidate_by_profile(store, tail[1])
        if not cand:
            return _serve(store, user_id, viewer, cycle)
        return Result(_card_screen(store, viewer, cand))

    if action == "request" and tail[1:]:
        # кнопка «Написать / медиа» на самой карточке
        return _request(store, user_id, ["type", tail[1]])

    if action in ("like", "skip") and tail[1:]:
        cand = _candidate_by_profile(store, tail[1])
        if not cand:
            return Result(screen_error("Анкета больше недоступна."), alert="Анкета недоступна")
        return _react(store, user_id, viewer, cand, action, cycle)

    return _serve(store, user_id, viewer, cycle)


def _serve(store: Store, user_id: int, viewer: Viewer, cycle: int) -> Result:
    candidates = _candidates(store, viewer)
    ctx = _deck_context(store, user_id, viewer, cycle)
    cand = pick(viewer, candidates, ctx)
    if not cand:
        reason = exhausted_reason(viewer, candidates, ctx)
        advice = relaxation_advice(store.count_available(user_id), len(candidates), viewer)
        genre_candidates = [c for c in candidates if config.game_genres(viewer.games).intersection(config.game_genres(c.get("games") or []))]
        if viewer.search_mode == "genre" and not genre_candidates:
            advice = list(advice) + ["switch_mode_city"]
        report = store.cycle_report(user_id, cycle)
        advice = list(advice)
        advice.append(f"cycle_report:{report['shown']}:{report['likes']}:{report['requests']}")
        return Result(screen_exhausted(reason, advice, store.count_available(user_id)))
    store.mark_served(user_id, cycle, cand["user_id"])
    return Result(_card_screen(store, viewer, cand))


def _card_screen(store: Store, viewer: Viewer, cand: dict) -> Screen:
    return screen_card(cand, _compat(viewer, cand), viewer_city=viewer.city or "",
                       remaining=store.count_available(viewer.user_id))


def _candidate_by_profile(store: Store, raw_pid: str) -> Optional[dict]:
    if not raw_pid.isdigit():
        return None
    prof = store.profile_by_id(int(raw_pid))
    if not prof:
        return None
    row = store.candidate_view(prof["user_id"])
    if not row:
        return None
    return normalize_candidate(row)


def _referral_effect(store: Store, user_id: int) -> list[dict]:
    confirmed = store.confirm_referral(user_id)
    if not confirmed:
        return []
    store.log_event(user_id, "referral_confirmed")
    return [{"type": "notify", "user_id": confirmed["referrer_user_id"],
             "text": "Твоё приглашение подтверждено, +10 карточек."}]


def referral_effects(store: Store, user_id: int) -> list[dict]:
    return _referral_effect(store, user_id)


def _react(store: Store, user_id: int, viewer: Viewer, cand: dict, action: str, cycle: int) -> Result:
    target = cand["user_id"]
    if store.blocked_between(user_id, target):
        return Result(screen_error("Этот игрок недоступен."))
    if action == "like":
        if store.likes_today(user_id) >= config.LIKES_PER_DAY:
            return Result(screen_error("На сегодня много лайков — дай шанс и другим. Продолжим завтра."),
                          alert="Дневной лимит лайков")
        store.record_view(user_id, target, cycle, "like",
                          cooldown_str(config.COOLDOWN_LIKE_UNANSWERED_H))
        store.log_event(user_id, "card_liked", source=f"cycle:{cycle}")
        store.upsert_deck_state(user_id, target, cycle_no=cycle, last_action="like",
                                last_action_at=now_str(),
                                next_eligible_at=cooldown_str(config.COOLDOWN_LIKE_UNANSWERED_H))
        created = store.like(user_id, target)
        if not created:
            return Result(screen_error("Лайк уже отправлен."))
        effects: list[dict] = _referral_effect(store, user_id)
        effects.append({
            "type": "notify", "user_id": target,
            "text": ("⚡ Кто-то хочет собрать с тобой пати!\n\n"
                     "Смотри карточку: если тоже нажмёшь «❤️ В пати», "
                     "вы обменяетесь контактами."),
        })
        match = store.like_back_and_match(user_id, target)
        if match:
            opponent = store.user_by_id(target) or {}
            effects.append({"type": "notify", "user_id": target,
                            "text": _match_text(opponent, user_id),
                            "rows": [[("⚡ Открыть мэтч", f"match:open:{match['id']}")]]})
            store.log_event(user_id, "match_created")
            store.log_event(target, "match_created")
            result_effects = _referral_effect(store, user_id)
            return Result(_card_screen(store, viewer, cand),
                          effects=result_effects + effects,
                          alert="Взаимно! Мэтч создан — открой его в меню.")
        return Result(_card_screen(store, viewer, cand), effects=effects,
                      alert=f"❤️ Лайк отправлен игроку {cand.get('display_name') or ''}".strip())

    # skip
    skip_count = int(cand.get("skip_count") or 0) + 1
    expiry = cooldown_str(config.COOLDOWN_SKIP_BASE_H * (2 ** max(0, skip_count - 1)),
                          cap_hours=config.COOLDOWN_SKIP_MAX_H)
    store.record_view(user_id, target, cycle, "skip", expiry)
    store.upsert_deck_state(user_id, target, cycle_no=cycle, last_action="skip",
                            skip_count=skip_count, last_action_at=now_str(),
                            next_eligible_at=expiry)
    store.log_event(user_id, "card_skipped", source=f"cycle:{cycle}")

    effects = _referral_effect(store, user_id)

    candidates = _candidates(store, viewer)
    ctx = _deck_context(store, user_id, viewer, cycle)
    cand_next = pick(viewer, candidates, ctx)
    if not cand_next:
        reason = exhausted_reason(viewer, candidates, ctx)
        advice = relaxation_advice(store.count_available(user_id), len(candidates), viewer)
        genre_candidates = [c for c in candidates if config.game_genres(viewer.games).intersection(config.game_genres(c.get("games") or []))]
        if viewer.search_mode == "genre" and not genre_candidates:
            advice = list(advice) + ["switch_game_optional"]
        report = store.cycle_report(user_id, cycle)
        advice = list(advice)
        advice.append(f"cycle_report:{report['shown']}:{report['likes']}:{report['requests']}")
        return Result(screen_exhausted(reason, advice, store.count_available(user_id)), effects=effects)
    store.mark_served(user_id, cycle, cand_next["user_id"])
    return Result(_card_screen(store, viewer, cand_next), effects=effects)


def _match_text(opponent: dict, user_id: int) -> str:
    return "⚡ Взаимный сигнал — вы оба хотите в пати!"


# ── профиль ──────────────────────────────────────────────────────────────────
def _profile_action(store: Store, user_id: int, tail: list[str]) -> Result:
    what = tail[0] if tail else "view"
    prof = _profile(store, user_id)
    if not prof:
        return Result(screen_need_profile())

    if what == "pause":
        store.set_profile_status(prof["id"], "paused", visible=0, reason="manual")
        store.log_event(user_id, "profile_paused")
        return Result(screen_paused(False, len(store.inbox(user_id))), alert="Анкета выключена")
    if what == "resume":
        store.set_profile_status(prof["id"], "active", visible=1, reason="manual")
        store.log_event(user_id, "profile_resumed")
        return Result(screen_paused(True, len(store.inbox(user_id))), alert="Анкета включена")
    if what == "edit_bio":
        store.save_draft(user_id, "edit_bio", {})
        return Result(screen_request_compose_edit("bio",
                                                  "Пришли новый текст анкеты (1–3 строки)."))
    if what == "edit_games":
        store.save_draft(user_id, "edit_games", {})
        return Result(screen_request_compose_edit("games",
                                                  f"Перечисли игры через запятую (до {config.MAX_GAMES})."))
    if what == "photo":
        store.save_draft(user_id, "edit_photo", {})
        return Result(screen_request_compose_edit("photo", "Пришли новую картинку одним сообщением."))
    return Result(_profile_screen(store, user_id))


def screen_request_compose_edit(kind: str, prompt: str) -> Screen:
    from ui.screens import box, TITLE
    return Screen(box("РЕДАКТИРОВАНИЕ", [prompt, "", "Отмена вернёт в анкету."]),
                  [[("↩️ Отмена", "menu:profile")]])


def on_edit_text(store: Store, user_id: int, text: str) -> Optional[Result]:
    """Текст в режиме редактирования анкеты."""
    state, _, _ = store.load_draft(user_id)
    prof = _profile(store, user_id)
    if not prof:
        store.clear_draft(user_id)
        return None

    if state == "edit_bio":
        ok, result = validate_bio(text)
        if not ok:
            return Result(screen_error(str(result)))
        store.update_profile_fields(prof["id"], bio=result)
        store.clear_draft(user_id)
        return Result(_profile_screen(store, user_id), alert="Текст обновлён")
    if state == "edit_games":
        ok, result = validate_games(text)
        if not ok:
            return Result(screen_error(str(result)))
        store.set_profile_games(prof["id"], result)
        store.clear_draft(user_id)
        return Result(_profile_screen(store, user_id), alert="Игры обновлены")
    return None


def on_edit_photo(store: Store, user_id: int, file_id: str) -> Optional[Result]:
    state, _, _ = store.load_draft(user_id)
    prof = _profile(store, user_id)
    if not prof:
        return None
    if state == "edit_photo":
        store.update_profile_fields(prof["id"], photo_file_id=file_id)
        store.clear_draft(user_id)
        return Result(_profile_screen(store, user_id), alert="Картинка обновлена")
    if state == "request_photo_pending":
        return None
    return None


# ── фильтры ──────────────────────────────────────────────────────────────────
def _filters(store: Store, user_id: int, tail: list[str]) -> Result:
    prof = _profile(store, user_id)
    if not prof:
        return Result(screen_need_profile())
    pid = prof["id"]
    flt = store.get_filters(pid)
    if tail:
        what = tail[0]
        if what == "age" and len(tail) >= 3:
            store.save_filters(pid, min_age=int(tail[1]), max_age=int(tail[2]))
        elif what == "geo" and len(tail) >= 2 and tail[1] in ("any", "country", "city"):
            store.save_filters(pid, geo_mode=tail[1])
        elif what == "lang":
            store.save_filters(pid, require_common_language=0 if flt["require_common_language"] else 1)
        elif what == "game":
            store.save_filters(pid, require_common_game=0 if flt["require_common_game"] else 1)
        elif what == "mode" and len(tail) >= 2 and tail[1] in ("city", "genre"):
            mode = tail[1]
            store.save_filters(pid, search_mode=mode, geo_mode="city" if mode == "city" else "any",
                               require_common_language=1 if mode == "city" else 0)
        elif what == "gender" and len(tail) >= 2 and tail[1] in ("any", "same", "other"):
            store.save_filters(pid, gender_pref=tail[1])
        elif what == "window" and len(tail) >= 2:
            age = int(prof["age"])
            delta = 5 if tail[1] == "5" else 10 if tail[1] == "10" else 0
            low, high = ((max(config.MIN_AGE, age-delta), min(config.MAX_AGE, age+delta))
                         if delta else (config.MIN_AGE, config.MAX_AGE))
            store.save_filters(pid, min_age=low, max_age=high)
        flt = store.get_filters(pid)
    return Result(screen_filters(flt, store.count_available(user_id), viewer_age=prof.get("age")))


# ── заявки на связь ──────────────────────────────────────────────────────────
def _request(store: Store, user_id: int, tail: list[str]) -> Result:
    viewer = _viewer(store, user_id)
    if not viewer:
        return Result(screen_need_profile())
    today = datetime.now().strftime("%Y-%m-%d")
    if store.requests_today(user_id) >= config.DAILY_REQUEST_LIMIT:
        return Result(screen_error("На сегодня лимит заявок исчерпан. Попробуй завтра."),
                      alert="Слишком много заявок за день")

    if tail and tail[0] == "type" and len(tail) >= 3 and tail[1] == "match":
        match = store.match_by_id(int(tail[2])) if tail[2].isdigit() else None
        if not match:
            return Result(screen_error("Мэтч не найден."))
        other = match["user_b_id"] if match["user_a_id"] == user_id else match["user_a_id"]
        return Result(screen_request_type(0, (store.user_by_id(other) or {}).get("display_name") or "игрок"))

    if tail and tail[0] == "go" and len(tail) >= 3:
        cand = _candidate_by_profile(store, tail[1])
        kind = tail[2]
        if not cand:
            return Result(screen_error("Анкета недоступна."))
        cycle = store.current_cycle(user_id)
        store.record_view(user_id, cand["user_id"], cycle, "opened",
                          cooldown_str(config.COOLDOWN_OPENED_H))
        store.upsert_deck_state(user_id, cand["user_id"], cycle_no=cycle, last_action="opened",
                                last_action_at=now_str(),
                                next_eligible_at=cooldown_str(config.COOLDOWN_OPENED_H))
        store.save_draft(user_id, f"request_{kind}_pending",
                         {"target": cand["user_id"], "profile_id": cand["profile_id"], "kind": kind})
        return Result(screen_request_compose(cand["profile_id"], kind))

    if tail and tail[0] == "type" and len(tail) >= 2:
        cand = _candidate_by_profile(store, tail[1])
        if not cand:
            return Result(screen_error("Анкета недоступна."))
        return Result(screen_request_type(cand["profile_id"], cand.get("display_name") or "игрок"))

    return Result(screen_error("Выбери формат заявки."))


def submit_request(store: Store, user_id: int, kind: str, text: Optional[str],
                   file_id: Optional[str] = None) -> Result:
    """Отправка заявки: создаёт запись и уведомляет адресата (effect notify)."""
    state, draft, _ = store.load_draft(user_id)
    target = int(draft.get("target") or 0)
    if not target:
        return Result(screen_error("Заявка устарела — открой карточку заново."))
    if store.blocked_between(user_id, target):
        return Result(screen_error("Игрок недоступен."))

    key = f"{user_id}:{target}:{kind}:{store.today_salt()}"
    request_id = store.create_request(user_id, target, kind, text, key)
    if request_id is None:
        return Result(screen_error("Такая заявка уже отправлена сегодня."))
    if file_id:
        store.add_attachment(request_id, kind, file_id, caption=text)
    store.log_event(user_id, "request_sent", source=f"cycle:{store.current_cycle(user_id)}")

    sender = store.user_by_id(user_id) or {}
    name = sender.get("display_name") or "игрок"
    preview = text or {"photo": "📷 фото", "video": "🎬 видео", "video_note": "⭕ кружок"}.get(kind, "")
    effect = {"type": "notify", "user_id": target,
              "text": (f"☄️ Заявка в пати от {name}.\n\n«{preview}»\n\n"
                       "Открой, чтобы посмотреть анкету, и при желании ответь."),
              "rows": [[("☄️ Открыть заявку", f"inbox:open:{request_id}")]]}
    if file_id:
        effect = {"type": "notify_media", "user_id": target, "kind": kind, "file_id": file_id,
                  "caption": f"☄️ Заявка в пати от {name}. «{preview}»".strip(),
                  "rows": [[("☄️ Открыть заявку", f"inbox:open:{request_id}")]]}
    store.clear_draft(user_id)
    return Result(screen_request_sent(name), effects=[effect], alert="Заявка отправлена")


# ── входящие ─────────────────────────────────────────────────────────────────
def _inbox(store: Store, user_id: int, tail: list[str]) -> Result:
    if not tail:
        return Result(screen_inbox(store.inbox_items(user_id)))

    what = tail[0]
    if not (len(tail) >= 2 and tail[1].isdigit()):
        return Result(screen_inbox([]))
    req = store.request_by_id(int(tail[1]))
    if not req or req["recipient_user_id"] != user_id:
        return Result(screen_error("Заявка не найдена."))
    sender_user = store.user_by_id(req["sender_user_id"]) or {}
    sender_prof = store.profile_by_user(req["sender_user_id"]) or {}
    sender_view = {**sender_prof, **sender_user}

    if what == "open":
        viewer = _viewer(store, user_id)
        common = []
        if viewer and sender_prof:
            common = [config.LANGUAGES.get(c, c)
                      for c in sorted(viewer.languages & set(store.profile_languages(sender_prof["id"])))]
        return Result(screen_inbox_item(store.inbox_item(req), sender_view, common))

    if what == "accept":
        store.respond_request(req["id"], "accepted")
        store.like(req["sender_user_id"], user_id)
        match = store.active_match(req["sender_user_id"], user_id)
        if not match:
            match = store.create_match(req["sender_user_id"], user_id)
        if not match:
            return Result(screen_error("Не удалось открыть контакт."))
        store.set_consent(match["id"], user_id)
        store.log_event(user_id, "request_accepted")
        from domain.validators import contact_url
        effects = [{
            "type": "notify", "user_id": req["sender_user_id"],
            "text": (f"✅ {sender_user.get('display_name') or 'Игрок'} принял твою заявку.\n\n"
                     f"Контакт: {contact_url(sender_user.get('telegram_username'), req['sender_user_id'])}\n"
                     "Напиши и договоритесь о времени."),
        }]
        side = "a" if match["user_a_id"] == user_id else "b"
        a_consent, b_consent = store.match_consents(match["id"])
        return Result(screen_match(sender_view, a_consent, b_consent,
                                   match["id"], viewer_side=side), effects=effects,
                      alert="Заявка принята — контакт открыт")

    if what == "decline":
        store.respond_request(req["id"], "rejected")
        return Result(screen_inbox(store.inbox_items(user_id)), alert="Заявка отклонена")

    if what == "block":
        store.respond_request(req["id"], "blocked")
        store.block(user_id, req["sender_user_id"], reason="request_block")
        return Result(screen_inbox(store.inbox_items(user_id)), alert="Игрок заблокирован")

    return Result(screen_inbox(store.inbox_items(user_id)))


# ── мэтчи ────────────────────────────────────────────────────────────────────
def _match(store: Store, user_id: int, tail: list[str]) -> Result:
    if not tail or not tail[-1].isdigit():
        matches = store.user_matches(user_id)
        if not matches:
            return Result(screen_error("Мэтчей пока нет."))
        match = matches[0]
    else:
        match = store.match_by_id(int(tail[-1]))
    if not match:
        return Result(screen_error("Мэтч не найден."))
    if user_id not in (match["user_a_id"], match["user_b_id"]):
        return Result(screen_error("Это не твой мэтч."))
    side = "a" if match["user_a_id"] == user_id else "b"
    other_id = match["user_b_id"] if side == "a" else match["user_a_id"]
    other_prof = store.profile_by_user(other_id) or {}
    other_user = store.user_by_id(other_id) or {}
    a_consent, b_consent = store.match_consents(match["id"])

    if tail and tail[0] == "share":
        updated = store.set_consent(match["id"], user_id)
        if not updated:
            return Result(screen_error("Мэтч не найден."))
        a_consent, b_consent = store.match_consents(match["id"])
        opened = a_consent and b_consent
        effects = []
        if opened:
            handle = other_user.get("username")
            from domain.validators import contact_url
            effects.append({"type": "notify", "user_id": other_id,
                            "text": (f"✅ Контакт открыт: "
                                     f"{contact_url((store.user_by_id(user_id) or {}).get('telegram_username'), user_id)}")})
            store.log_event(user_id, "contact_shared")
            store.log_event(other_id, "contact_shared")
        return Result(screen_match({**other_prof, **other_user}, a_consent, b_consent,
                                   match["id"], viewer_side=side),
                      effects=effects, alert="Контакт открыт" if opened else "Ждём подтверждения другой стороны")
    if tail and tail[0] == "open":
        return Result(screen_match({**other_prof, **other_user}, a_consent, b_consent,
                                   match["id"], viewer_side=side))
    return Result(screen_match({**other_prof, **other_user}, a_consent, b_consent,
                               match["id"], viewer_side=side))


# ── жалобы и блокировки ──────────────────────────────────────────────────────
def _report(store: Store, user_id: int, tail: list[str]) -> Result:
    if not tail:
        return Result(screen_error("Кого жалуемся?"))
    if tail[0] == "reason" and len(tail) >= 3:
        pid, code = tail[1], tail[2]
        prof = store.profile_by_id(int(pid)) if pid.isdigit() else None
        if not prof or code not in config.REPORT_REASONS:
            return Result(screen_error("Жалоба не отправилась."))
        store.report(user_id, prof["user_id"], code)
        store.block(user_id, prof["user_id"], reason=f"report:{code}")
        store.log_event(user_id, "report_sent")
        viewer = _viewer(store, user_id)
        name = (store.user_by_id(prof["user_id"]) or {}).get("display_name") or "игрок"
        if viewer:
            cycle = store.current_cycle(user_id)
            nxt = _serve(store, user_id, viewer, cycle)
            return Result(Screen(screen_report_done(name).text, nxt.screen.rows,
                                 nxt.screen.photo), alert="Жалоба отправлена")
        return Result(screen_report_done(name), alert="Жалоба отправлена")

    if tail[0] == "match" and len(tail) >= 2 and tail[1].isdigit():
        match = store.match_by_id(int(tail[1]))
        if not match:
            return Result(screen_error("Мэтч не найден."))
        other = match["user_b_id"] if match["user_a_id"] == user_id else match["user_a_id"]
        prof = store.profile_by_user(other)
        if not prof:
            return Result(screen_error("Профиль недоступен."))
        name = (store.user_by_id(prof["user_id"]) or {}).get("display_name") or "игрок"
        return Result(screen_report(prof["id"], name))

    if tail[0] == "open" and len(tail) >= 2 and tail[1].isdigit():
        prof = store.profile_by_id(int(tail[1]))
        if not prof:
            return Result(screen_error("Анкета недоступна."))
        name = (store.user_by_id(prof["user_id"]) or {}).get("display_name") or "игрок"
        return Result(screen_report(prof["id"], name))
    return Result(screen_error("Не понял, на кого жалоба."))


def _block(store: Store, user_id: int, tail: list[str]) -> Result:
    if len(tail) >= 2 and tail[1].isdigit():
        prof = store.profile_by_id(int(tail[1]))
        if not prof:
            return Result(screen_error("Анкета недоступна."))
        store.block(user_id, prof["user_id"], reason="manual")
        store.log_event(user_id, "block_sent")
        name = (store.user_by_id(prof["user_id"]) or {}).get("display_name") or "игрок"
        return Result(screen_report_done(name), alert="Игрок заблокирован")
    return Result(screen_error("Кого блокируем?"))


def _share(store: Store, user_id: int) -> Result:
    from ui.screens import box, TITLE
    link = config.bot_link(f"ref_{user_id}")
    lines = [
        "📨 Пригласи друга в радар.",
        "",
        "Награда начисляется после публикации анкеты друга и его 3 решений по карточкам.",
        "Один Telegram-аккаунт даёт максимум одно засчитанное приглашение.",
        "",
        f"Ссылка для друзей: {link}",
    ]
    rows = [[("📨 Поделиться", link)],
            [("🏠 В меню", "menu:home")]]
    return Result(Screen(box(TITLE, lines), rows))