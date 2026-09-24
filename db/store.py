"""Хранилище: SQLite-доступ, схема и все запросы бота.

Один класс Store, никакой ORM: запросы читаемы, транзакции короткие,
WAL включён, foreign_keys обязателен (см. db/schema.sql).
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

import config

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
FMT = "%Y-%m-%d %H:%M:%S"
REQUEST_STATUSES = frozenset({"pending", "accepted", "rejected", "blocked"})


def now_str() -> str:
    return datetime.now().strftime(FMT)


def window_for(age: int, delta: int) -> tuple[int, int]:
    """Границы возраста ±delta с теми же зажимами, что и у фильтрации."""
    return max(config.MIN_AGE, int(age) - int(delta)), min(config.MAX_AGE, int(age) + int(delta))


def age_window(age: int) -> tuple[int, int]:
    return window_for(age, 5)


# Состояния визарда регистрации. Таблица registration_sessions общая с заявками
# (request_*_pending) и редактированием (edit_*): черновики регистрации читаются
# и удаляются только по этим состояниям, чтобы отмена анкеты не сносила чужие.
REGISTRATION_STATES = frozenset({
    "gender", "age", "country", "city", "languages", "games",
    "search", "bio", "photo", "preview",
})


@dataclass(frozen=True)
class DraftSession:
    """Снимок черновика регистрации. revision — монотонный счётчик для CAS."""
    state: str
    draft: dict
    attempts: int
    revision: int
    created_at: str
    updated_at: str


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], FMT)
    except ValueError:
        return None


class Store:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or config.DB_PATH
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            self.conn.executescript(fh.read())
        self._migrate_profile_filters()
        self._migrate_registration_sessions()

    def _migrate_profile_filters(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(profile_filters)")}
        if "search_mode" not in columns:
            self.conn.execute("ALTER TABLE profile_filters ADD COLUMN search_mode TEXT NOT NULL DEFAULT 'city'")
        if "gender_pref" not in columns:
            self.conn.execute("ALTER TABLE profile_filters ADD COLUMN gender_pref TEXT NOT NULL DEFAULT 'any'")

    def _migrate_registration_sessions(self) -> None:
        """Аддитивная миграция: счётчик ревизий для CAS у черновиков регистрации."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(registration_sessions)")}
        if "revision" not in columns:
            self.conn.execute("ALTER TABLE registration_sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")

    # ── низкий уровень ───────────────────────────────────────────────────────
    def q(self, sql: str, args: Iterable = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def q1(self, sql: str, args: Iterable = ()) -> Optional[dict]:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: Iterable = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(args))

    def close(self) -> None:
        self.conn.close()

    def log_event(self, user_id: Optional[int], name: str, source: Optional[str] = None) -> None:
        self.x("INSERT INTO events (user_id, name, source, created_at) VALUES (?, ?, ?, ?)",
               (user_id, name, source, now_str()))

    def has_event(self, user_id: int, name: str) -> bool:
        """Проверка разового согласия (например подтверждения 18+)."""
        return bool(self.q1("SELECT 1 AS ok FROM events WHERE user_id = ? AND name = ? LIMIT 1",
                            (user_id, name)))

    # ── пользователи ─────────────────────────────────────────────────────────
    def ensure_user(self, tg_id: int, username: Optional[str], first_name: Optional[str],
                    source: Optional[str] = None) -> dict:
        row = self.q1("SELECT * FROM users WHERE telegram_user_id = ?", (tg_id,))
        if row:
            self.x("UPDATE users SET telegram_username = ?, first_name = ?, last_seen_at = ?, "
                   "is_bot_blocked = 0 WHERE id = ?",
                   (username, first_name, now_str(), row["id"]))
            return self.q1("SELECT * FROM users WHERE id = ?", (row["id"],)) or row
        self.x("INSERT INTO users (telegram_user_id, telegram_username, first_name, created_at, last_seen_at) "
               "VALUES (?, ?, ?, ?, ?)", (tg_id, username, first_name, now_str(), now_str()))
        row = self.q1("SELECT * FROM users WHERE telegram_user_id = ?", (tg_id,)) or {}
        if source and row.get("id"):
            self.x("INSERT OR IGNORE INTO events (user_id, name, source, created_at) VALUES (?, 'start', ?, ?)",
                   (row["id"], source, now_str()))
        return row

    def user_by_tg(self, tg_id: int) -> Optional[dict]:
        row = self.q1("SELECT * FROM users WHERE telegram_user_id = ?", (tg_id,))
        return self._named(row)

    def record_referral(self, referrer_user_id: int, invited_tg_id: int, source: str = "deep_link") -> bool:
        """Засчитывает deep-link один раз на Telegram-аккаунт."""
        if not self.user_by_id(referrer_user_id):
            return False
        invited = self.user_by_tg(invited_tg_id)
        if not invited or invited["id"] == referrer_user_id:
            return False
        try:
            self.x("INSERT INTO referrals (referrer_user_id, invited_user_id, source, created_at) "
                   "VALUES (?, ?, ?, ?)", (referrer_user_id, invited["id"], source, now_str()))
        except sqlite3.IntegrityError:
            return False
        return True

    def confirm_referral(self, invited_user_id: int) -> Optional[dict]:
        """Подтверждает первое подходящее приглашение и возвращает его запись."""
        row = self.q1("""SELECT r.* FROM referrals r
                         JOIN profiles p ON p.user_id = r.invited_user_id
                         WHERE r.invited_user_id = ? AND r.rewarded = 0
                           AND p.status = 'active' AND p.is_visible = 1
                           AND (SELECT COUNT(*) FROM view_events v
                                WHERE v.viewer_user_id = r.invited_user_id
                                  AND v.action IN ('skip', 'like')) >= 3
                         LIMIT 1""", (invited_user_id,))
        if not row:
            return None
        since = (datetime.now() - timedelta(days=7)).strftime(FMT)
        count = self.q1("SELECT COUNT(*) AS c FROM referrals r JOIN referral_bonuses b ON b.referral_id = r.id "
                        "WHERE r.referrer_user_id = ? AND b.confirmed_at >= ?", (row["referrer_user_id"], since))
        if count and int(count["c"]) >= 5:
            return None
        cur = self.x("UPDATE referrals SET rewarded = 1 WHERE id = ? AND rewarded = 0", (row["id"],))
        if not cur.rowcount:
            return None
        self.x("INSERT INTO referral_bonuses (referral_id) VALUES (?)", (row["id"],))
        return self.q1("SELECT * FROM referrals WHERE id = ?", (row["id"],))

    def daily_card_limit(self, user_id: int) -> int:
        """Базовый лимит плюс доступный бонус, сохраняя текущий extra-день."""
        row = self.q1("SELECT COALESCE(SUM(granted_cards - consumed_cards), 0) AS bonus, "
                      "COALESCE(SUM(granted_cards), 0) AS granted, "
                      "COALESCE(SUM(consumed_cards), 0) AS consumed "
                      "FROM referral_bonuses b JOIN referrals r ON r.id = b.referral_id "
                      "WHERE r.referrer_user_id = ?", (user_id,)) or {}
        used_today = max(0, self.served_today(user_id) - config.DAILY_CARD_LIMIT)
        used_today = min(used_today, int(row["consumed"] or 0))
        return config.DAILY_CARD_LIMIT + int(row["bonus"] or 0) + used_today

    def consume_referral_cards(self, user_id: int, count: int) -> None:
        """Списывает бонус только по фактическим показам сверх базового лимита."""
        target = max(0, count - config.DAILY_CARD_LIMIT)
        current = self.q1("SELECT COALESCE(SUM(consumed_cards), 0) AS consumed, "
                          "COALESCE(SUM(granted_cards), 0) AS granted FROM referral_bonuses b "
                          "JOIN referrals r ON r.id = b.referral_id WHERE r.referrer_user_id = ?",
                          (user_id,)) or {}
        extra = min(target, int(current["granted"] or 0)) - int(current["consumed"] or 0)
        if extra <= 0:
            return
        rows = self.q("SELECT b.referral_id, b.granted_cards, b.consumed_cards FROM referral_bonuses b "
                      "JOIN referrals r ON r.id = b.referral_id WHERE r.referrer_user_id = ? "
                      "AND b.consumed_cards < b.granted_cards ORDER BY b.confirmed_at, b.referral_id", (user_id,))
        for row in rows:
            take = min(extra, int(row["granted_cards"]) - int(row["consumed_cards"]))
            self.x("UPDATE referral_bonuses SET consumed_cards = consumed_cards + ? WHERE referral_id = ?",
                   (take, row["referral_id"]))
            extra -= take
            if extra <= 0:
                break

    def referral_for_invited(self, invited_user_id: int) -> Optional[dict]:
        return self.q1("SELECT * FROM referrals WHERE invited_user_id = ?", (invited_user_id,))

    def referral_notification_pending(self, referrer_user_id: int) -> Optional[dict]:
        return self.q1("""SELECT r.* FROM referrals r JOIN referral_bonuses b ON b.referral_id = r.id
                         WHERE r.referrer_user_id = ? AND b.notification_sent = 0 LIMIT 1""",
                       (referrer_user_id,))

    def mark_referral_notification_sent(self, referral_id: int) -> None:
        self.x("UPDATE referral_bonuses SET notification_sent = 1 WHERE referral_id = ?", (referral_id,))

    def user_by_id(self, user_id: int) -> Optional[dict]:
        return self._named(self.q1("SELECT * FROM users WHERE id = ?", (user_id,)))

    @staticmethod
    def _named(row: Optional[dict]) -> Optional[dict]:
        """Добавляет display_name — экраны не должны собирать имя сами."""
        if row is not None:
            row["display_name"] = _display_name(row)
        return row

    def touch_activity(self, user_id: int) -> None:
        self.x("UPDATE users SET last_seen_at = ? WHERE id = ?", (now_str(), user_id))
        self.x("UPDATE profiles SET last_active_at = ? WHERE user_id = ?", (now_str(), user_id))

    def set_bot_blocked(self, user_id: int, blocked: bool = True) -> None:
        self.x("UPDATE users SET is_bot_blocked = ? WHERE id = ?", (1 if blocked else 0, user_id))

    # ── регистрация (черновик) ───────────────────────────────────────────────
    def save_draft(self, user_id: int, state: str, draft: dict, attempts: int = 0) -> None:
        self.x("INSERT INTO registration_sessions (user_id, state, draft_json, attempts, created_at, updated_at) "
               "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
               "state = excluded.state, draft_json = excluded.draft_json, "
               "attempts = excluded.attempts, updated_at = excluded.updated_at",
               (user_id, state, json.dumps(draft, ensure_ascii=False), attempts, now_str(), now_str()))

    def load_draft(self, user_id: int) -> tuple[str, dict, int]:
        row = self.q1("SELECT * FROM registration_sessions WHERE user_id = ?", (user_id,))
        if not row:
            return "", {}, 0
        return row["state"], json.loads(row["draft_json"] or "{}"), row["attempts"]

    def clear_draft(self, user_id: int) -> None:
        self.x("DELETE FROM registration_sessions WHERE user_id = ?", (user_id,))

    # ── черновик регистрации: продолжение после отмены/ошибки ────────────────
    def load_draft_session(self, user_id: int) -> Optional[DraftSession]:
        """Действующий черновик регистрации: None, если записи нет, она не
        регистрационная (заявка/редактирование) или истёк срок хранения."""
        row = self.q1("SELECT * FROM registration_sessions WHERE user_id = ?", (user_id,))
        if not row or row["state"] not in REGISTRATION_STATES:
            return None
        if self._draft_expired(row["updated_at"]):
            self.x("DELETE FROM registration_sessions WHERE user_id = ? "
                   "AND updated_at = ? AND revision = ?",
                   (user_id, row["updated_at"], row["revision"]))
            return None
        return DraftSession(state=row["state"], draft=json.loads(row["draft_json"] or "{}"),
                            attempts=row["attempts"], revision=row["revision"],
                            created_at=row["created_at"], updated_at=row["updated_at"])

    def _draft_expired(self, updated_at: Optional[str]) -> bool:
        """Fail closed: неизвестная дата считается протухшей."""
        dt = parse_dt(updated_at)
        if not dt:
            return True
        return datetime.now() - dt > timedelta(days=config.DRAFT_TTL_DAYS)

    def save_draft_session(self, user_id: int, state: str, draft: dict, attempts: int = 0,
                           expected_revision: Optional[int] = None) -> bool:
        """Пишет черновик регистрации. С expected_revision — compare-and-swap:
        применяется, только если запись не изменилась с момента чтения."""
        if state not in REGISTRATION_STATES:
            return False
        attempts = max(0, min(int(attempts), config.MAX_VALIDATION_ATTEMPTS))
        payload = json.dumps(draft, ensure_ascii=False)
        if expected_revision is None:
            self.x(
                "INSERT INTO registration_sessions (user_id, state, draft_json, attempts, "
                "created_at, updated_at, revision) VALUES (?,?,?,?,?,?,0) "
                "ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, "
                "draft_json = excluded.draft_json, attempts = excluded.attempts, "
                "updated_at = excluded.updated_at, revision = registration_sessions.revision + 1",
                (user_id, state, payload, attempts, now_str(), now_str()))
            return True
        cur = self.conn.execute(
            "UPDATE registration_sessions SET state = ?, draft_json = ?, attempts = ?, "
            "updated_at = ?, revision = revision + 1 WHERE user_id = ? AND revision = ?",
            (state, payload, attempts, now_str(), user_id, expected_revision))
        return cur.rowcount == 1

    def restart_draft(self, user_id: int) -> DraftSession:
        """Явный сброс: одна операция вместо clear+create, чтобы между ними
        не вклинились параллельные нажатия.

        Чужой поток (отправка заявки, редактирование анкеты) в этой же таблице
        не затирается: регистрация не имеет права сносить его состояние.
        """
        row = self.q1("SELECT state FROM registration_sessions WHERE user_id = ?", (user_id,))
        if row and row["state"] not in REGISTRATION_STATES:
            return DraftSession(row["state"], {}, 0, 0, "", "")
        ts = now_str()
        fresh: dict = {"languages": [], "games": []}
        self.x(
            "INSERT INTO registration_sessions (user_id, state, draft_json, attempts, "
            "created_at, updated_at, revision) VALUES (?,?,?,?,?,?,0) "
            "ON CONFLICT(user_id) DO UPDATE SET state = 'gender', draft_json = excluded.draft_json, "
            "attempts = 0, created_at = excluded.created_at, updated_at = excluded.updated_at, "
            "revision = registration_sessions.revision + 1",
            (user_id, "gender", json.dumps(fresh, ensure_ascii=False), 0, ts, ts))
        return DraftSession("gender", fresh, 0, 0, ts, ts)

    def expire_draft(self, user_id: int) -> bool:
        """Удаляет черновик, только если он уже истёк (повторная проверка срока)."""
        row = self.q1("SELECT updated_at FROM registration_sessions WHERE user_id = ?", (user_id,))
        if not row or not self._draft_expired(row["updated_at"]):
            return False
        cur = self.conn.execute("DELETE FROM registration_sessions WHERE user_id = ? AND updated_at = ?",
                                (user_id, row["updated_at"]))
        return cur.rowcount == 1


    # ── анкеты ───────────────────────────────────────────────────────────────
    def create_profile(self, user_id: int, draft: dict, publish: bool = False) -> int:
        ts = now_str()
        self.x(
            "INSERT INTO profiles (user_id, gender, age, country_code, city, bio, photo_file_id, status, "
            "is_visible, last_active_at, published_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET gender=excluded.gender, age=excluded.age, "
            "country_code=excluded.country_code, city=excluded.city, bio=excluded.bio, "
            "photo_file_id=excluded.photo_file_id, status=excluded.status, is_visible=excluded.is_visible, "
            "published_at=COALESCE(profiles.published_at, excluded.published_at), updated_at=excluded.updated_at, "
            "card_revision = profiles.card_revision + 1",
            (user_id, draft.get("gender", "x"), int(draft.get("age", 18)), draft.get("country", "OTHER"),
             draft.get("city"), draft.get("bio", ""), draft.get("photo_file_id"),
             "active" if publish else "draft", 1 if publish else 0,
             ts, ts if publish else None, ts),
        )
        prof = self.profile_by_user(user_id)
        pid = prof["id"]
        self.x("DELETE FROM profile_languages WHERE profile_id = ?", (pid,))
        for i, code in enumerate(draft.get("languages") or ["ru"]):
            self.x("INSERT OR IGNORE INTO profile_languages (profile_id, language_code, is_primary) VALUES (?,?,?)",
                   (pid, code, 1 if i == 0 else 0))
        self.x("DELETE FROM profile_games WHERE profile_id = ?", (pid,))
        for game in (draft.get("games") or [])[:config.MAX_GAMES]:
            self.x("INSERT OR IGNORE INTO profile_games (profile_id, game_name) VALUES (?,?)", (pid, game))
        self.x("INSERT OR IGNORE INTO profile_filters (profile_id) VALUES (?)", (pid,))
        mode = draft.get("search_mode")
        if draft.get("registration_wizard"):
            mode = mode or "city"
            min_age, max_age = age_window(int(draft.get("age", 18)))
            # Города нет — режим остаётся «пол + город», но география не режет выдачу в ноль.
            has_city = bool((draft.get("city") or "").strip())
            geo_mode = "city" if (mode == "city" and has_city) else "any"
            common_language = 1 if mode == "city" else 0
            gender_pref = draft.get("gender_pref", "any")
        else:
            # Preserve pre-feature behavior for callers creating/updating profiles directly.
            old_filters = self.get_filters(pid)
            min_age, max_age = old_filters["min_age"], old_filters["max_age"]
            geo_mode = old_filters["geo_mode"]
            common_language = old_filters["require_common_language"]
            gender_pref = old_filters["gender_pref"]
            mode = old_filters["search_mode"]
        self.save_filters(pid, min_age=min_age, max_age=max_age, search_mode=mode,
                          gender_pref=gender_pref, geo_mode=geo_mode,
                          require_common_language=common_language)
        return pid

    def profile_by_user(self, user_id: int) -> Optional[dict]:
        return self.q1("SELECT * FROM profiles WHERE user_id = ?", (user_id,))

    def profile_by_id(self, profile_id: int) -> Optional[dict]:
        return self.q1("SELECT * FROM profiles WHERE id = ?", (profile_id,))

    def is_registered(self, user_id: int) -> bool:
        row = self.q1("SELECT status FROM profiles WHERE user_id = ?", (user_id,))
        return bool(row) and row["status"] in ("active", "paused")

    def set_profile_status(self, profile_id: int, status: str, visible: Optional[int] = None,
                           reason: Optional[str] = None) -> None:
        fields = ["status = ?", "updated_at = ?", "paused_reason = ?"]
        args: list[Any] = [status, now_str(), reason]
        if visible is not None:
            fields.append("is_visible = ?")
            args.append(visible)
        if status == "active":
            fields += ["paused_until = NULL", "auto_paused_at = NULL"]
        args.append(profile_id)
        self.x(f"UPDATE profiles SET {', '.join(fields)} WHERE id = ?", args)

    def profile_languages(self, profile_id: int) -> list[str]:
        return [r["language_code"] for r in
                self.q("SELECT language_code FROM profile_languages WHERE profile_id = ?", (profile_id,))]

    def profile_games(self, profile_id: int) -> list[str]:
        return [r["game_name"] for r in
                self.q("SELECT game_name FROM profile_games WHERE profile_id = ? ORDER BY game_name", (profile_id,))]

    def get_filters(self, profile_id: int) -> dict:
        row = self.q1("SELECT * FROM profile_filters WHERE profile_id = ?", (profile_id,))
        if row is None:
            self.x("INSERT OR IGNORE INTO profile_filters (profile_id) VALUES (?)", (profile_id,))
            row = self.q1("SELECT * FROM profile_filters WHERE profile_id = ?", (profile_id,)) or {}
        return row

    def save_filters(self, profile_id: int, **kw: Any) -> None:
        allowed = {"min_age", "max_age", "geo_mode", "require_common_language", "require_common_game",
                   "only_active_recent", "search_mode", "gender_pref"}
        sets, args = [], []
        for key, value in kw.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                args.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        args += [now_str(), profile_id]
        self.x(f"UPDATE profile_filters SET {', '.join(sets)} WHERE profile_id = ?", args)

    # ── пул кандидатов и состояние колоды ────────────────────────────────────
    def candidate_pool(self, viewer_user_id: int, min_age: int, max_age: int,
                       only_active_recent: bool = False) -> list[dict]:
        """Кандидаты со всеми полями для фильтрации и скоринга.

        Базовые условия (активность, видимость, не сам, не заблокирован, нет мэтча)
        отсекаются в SQL; языки/игры/гео/кулдауны — в domain.deck, чтобы логика
        была чистой и тестируемой.
        """
        sql = """
        SELECT p.id AS profile_id, p.user_id, p.gender, p.age, p.country_code, p.city, p.bio,
               p.photo_file_id, p.published_at, p.last_active_at,
               u.telegram_username, u.first_name,
               COALESCE(ds.skip_count, 0) AS skip_count,
               COALESCE(ds.impression_count, 0) AS impression_count,
               ds.last_shown_at, ds.next_eligible_at, ds.is_exhausted,
               COALESCE(ds.cycle_no, 0) AS deck_cycle, ds.last_action
        FROM profiles p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN deck_state ds ON ds.viewer_user_id = ? AND ds.candidate_user_id = p.user_id
        WHERE p.status = 'active' AND p.is_visible = 1
          AND p.user_id <> ?
          AND p.age BETWEEN ? AND ?
          AND NOT EXISTS (SELECT 1 FROM user_blocks b
                          WHERE (b.blocker_user_id = ? AND b.blocked_user_id = p.user_id)
                             OR (b.blocker_user_id = p.user_id AND b.blocked_user_id = ?))
          AND NOT EXISTS (SELECT 1 FROM matches m
                          WHERE m.status = 'active'
                            AND ((m.user_a_id = ? AND m.user_b_id = p.user_id)
                              OR (m.user_b_id = ? AND m.user_a_id = p.user_id)))
          AND (? = 0 OR p.last_active_at >= datetime('now', '-7 day'))
        """
        rows = self.q(sql, (viewer_user_id, viewer_user_id, min_age, max_age,
                            viewer_user_id, viewer_user_id, viewer_user_id, viewer_user_id,
                            1 if only_active_recent else 0))
        if not rows:
            return []
        ids = [r["profile_id"] for r in rows]
        marks = ",".join("?" * len(ids))
        langs: dict[int, set[str]] = {i: set() for i in ids}
        for r in self.q(f"SELECT profile_id, language_code FROM profile_languages WHERE profile_id IN ({marks})", ids):
            langs[r["profile_id"]].add(r["language_code"])
        games: dict[int, set[str]] = {i: set() for i in ids}
        for r in self.q(f"SELECT profile_id, game_name FROM profile_games WHERE profile_id IN ({marks})", ids):
            games[r["profile_id"]].add(r["game_name"].lower())
        for r in rows:
            r["languages"] = langs.get(r["profile_id"], set())
            r["games"] = games.get(r["profile_id"], set())
            r["display_name"] = _display_name(r)
        return rows

    def upsert_deck_state(self, viewer_user_id: int, candidate_user_id: int, **fields: Any) -> None:
        allowed = ("cycle_no", "impression_count", "skip_count", "last_action", "last_shown_at",
                   "last_action_at", "next_eligible_at", "like_pending_until", "is_exhausted")
        self.x("INSERT OR IGNORE INTO deck_state (viewer_user_id, candidate_user_id) VALUES (?, ?)",
               (viewer_user_id, candidate_user_id))
        sets, args = [], []
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                args.append(value)
        if not sets:
            return
        args += [viewer_user_id, candidate_user_id]
        self.x(f"UPDATE deck_state SET {', '.join(sets)} WHERE viewer_user_id = ? AND candidate_user_id = ?", args)

    def deck_row(self, viewer_user_id: int, candidate_user_id: int) -> Optional[dict]:
        return self.q1("SELECT * FROM deck_state WHERE viewer_user_id = ? AND candidate_user_id = ?",
                       (viewer_user_id, candidate_user_id))

    def record_view(self, viewer_user_id: int, candidate_user_id: int, cycle_no: int,
                    action: str, cooldown_until: Optional[str] = None) -> None:
        self.x("INSERT INTO view_events (viewer_user_id, candidate_user_id, cycle_no, action, shown_at, "
               "action_at, cooldown_until) VALUES (?,?,?,?,?,?,?)",
               (viewer_user_id, candidate_user_id, cycle_no, action, now_str(), now_str(), cooldown_until))

    def open_impression(self, viewer_user_id: int) -> Optional[dict]:
        """Текущий незакрытый показ (action = 'shown' и нет действия после него)."""
        return self.q1("""
            SELECT v.* FROM view_events v
            WHERE v.viewer_user_id = ? AND v.action = 'shown'
              AND NOT EXISTS (SELECT 1 FROM view_events a
                              WHERE a.viewer_user_id = v.viewer_user_id
                                AND a.candidate_user_id = v.candidate_user_id
                                AND a.id > v.id)
            ORDER BY v.id DESC LIMIT 1
        """, (viewer_user_id,))

    def current_cycle(self, viewer_user_id: int) -> int:
        row = self.q1("SELECT COALESCE(MAX(cycle_no), 0) AS c FROM deck_sessions WHERE viewer_user_id = ?",
                      (viewer_user_id,))
        c = int(row["c"]) if row and row["c"] else 0
        if c == 0:
            self.start_cycle(viewer_user_id, 1)
            return 1
        return c

    def start_cycle(self, viewer_user_id: int, cycle_no: int) -> None:
        self.x("INSERT OR IGNORE INTO deck_sessions (viewer_user_id, cycle_no, started_at) VALUES (?,?,?)",
               (viewer_user_id, cycle_no, now_str()))

    def mark_served(self, viewer_user_id: int, cycle_no: int, candidate_user_id: int) -> None:
        """Фиксирует фактический показ: событие в view_events + состояние колоды."""
        self.record_view(viewer_user_id, candidate_user_id, cycle_no, "shown")
        self.x("UPDATE deck_sessions SET last_served_at = ? WHERE viewer_user_id = ? AND cycle_no = ?",
               (now_str(), viewer_user_id, cycle_no))
        self.consume_referral_cards(viewer_user_id, self.served_today(viewer_user_id))
        row = self.deck_row(viewer_user_id, candidate_user_id) or {}
        self.upsert_deck_state(viewer_user_id, candidate_user_id, cycle_no=cycle_no,
                               last_shown_at=now_str(), last_action="shown",
                               impression_count=int(row.get("impression_count") or 0) + 1)

    def shown_in_cycle(self, viewer_user_id: int, cycle_no: int) -> set[int]:
        rows = self.q("SELECT DISTINCT candidate_user_id FROM view_events WHERE viewer_user_id = ? AND cycle_no = ? "
                      "AND action IN ('shown','skip','like','opened','expired','mutual')",
                      (viewer_user_id, cycle_no))
        return {r["candidate_user_id"] for r in rows}

    def last_served_candidate(self, viewer_user_id: int, cycle_no: Optional[int] = None) -> Optional[int]:
        """Последняя показанная анкета. С cycle_no — антиповтор внутри текущего прохода."""
        if cycle_no is None:
            row = self.q1("SELECT candidate_user_id FROM view_events WHERE viewer_user_id = ? "
                          "ORDER BY id DESC LIMIT 1", (viewer_user_id,))
        else:
            row = self.q1("SELECT candidate_user_id FROM view_events WHERE viewer_user_id = ? "
                          "AND cycle_no = ? ORDER BY id DESC LIMIT 1", (viewer_user_id, cycle_no))
        return int(row["candidate_user_id"]) if row else None

    def viewer(self, user_id: int):
        """Смотрящий для домена: профиль + фильтры + языки и игры.

        Локальный импорт: domain.deck сам импортирует db.store, наверху их связывать нельзя.
        Один источник правды для колоды и для счётчиков вроде count_available.
        """
        from domain.deck import Viewer

        prof = self.profile_by_user(user_id)
        if not prof:
            return None
        flt = self.get_filters(prof["id"])
        return Viewer(
            user_id=user_id, profile_id=prof["id"], age=prof["age"],
            country_code=prof["country_code"], city=prof.get("city"),
            languages=set(self.profile_languages(prof["id"])),
            games=set(self.profile_games(prof["id"])),
            min_age=flt["min_age"], max_age=flt["max_age"], geo_mode=flt["geo_mode"],
            require_common_language=bool(flt["require_common_language"]),
            require_common_game=bool(flt["require_common_game"]),
            only_active_recent=bool(flt["only_active_recent"]),
            search_mode=flt["search_mode"], gender=prof["gender"], gender_pref=flt["gender_pref"],
        )

    def count_available(self, viewer_user_id: int) -> int:
        """Сколько анкет реально может прийти в колоду.

        Считает теми же жёсткими фильтрами, что и выдача: иначе счётчик обещает карточки,
        которых человек никогда не увидит (город/жанр/пол).
        """
        from domain.deck import passes_hard_filters

        viewer = self.viewer(viewer_user_id)
        if not viewer:
            return 0
        pool = self.candidate_pool(viewer_user_id, viewer.min_age, viewer.max_age,
                                   viewer.only_active_recent)
        cyc = self.current_cycle(viewer_user_id)
        shown = self.shown_in_cycle(viewer_user_id, cyc)
        now = datetime.now()
        return sum(1 for c in pool
                   if c["user_id"] not in shown and c["is_exhausted"] != 1
                   and not _cooldown_active(c.get("next_eligible_at"))
                   and passes_hard_filters(viewer, c, now))

    def cycle_report(self, viewer_user_id: int, cycle_no: Optional[int] = None) -> dict:
        cycle = cycle_no or self.current_cycle(viewer_user_id)
        row = self.q1("""SELECT
            SUM(CASE WHEN action = 'shown' THEN 1 ELSE 0 END) AS shown,
            SUM(CASE WHEN action = 'like' THEN 1 ELSE 0 END) AS likes
            FROM view_events WHERE viewer_user_id = ? AND cycle_no = ?""", (viewer_user_id, cycle)) or {}
        request_row = self.q1("SELECT COUNT(*) AS c FROM events WHERE user_id = ? AND name = 'request_sent' "
                              "AND source = ?", (viewer_user_id, f"cycle:{cycle}")) or {}
        return {"cycle_no": cycle, "shown": int(row.get("shown") or 0),
                "likes": int(row.get("likes") or 0), "requests": int(request_row.get("c") or 0)}

    def moderation_action(self, report_id: int, moderator_user_id: int, action: str) -> Optional[dict]:
        if action not in {"suspend", "ban", "reject"}:
            raise ValueError("Недопустимое действие модерации")
        report = self.q1("SELECT * FROM reports WHERE id = ? AND status = 'new'", (report_id,))
        if not report:
            return None
        self.x("INSERT INTO moderation_actions (report_id, moderator_user_id, action, created_at) VALUES (?,?,?,?)",
               (report_id, moderator_user_id, action, now_str()))
        if action == "suspend":
            prof = self.profile_by_user(report["target_user_id"])
            if prof:
                self.set_profile_status(prof["id"], "paused", visible=0, reason="moderation")
            status = "resolved"
        elif action == "ban":
            prof = self.profile_by_user(report["target_user_id"])
            if prof:
                self.set_profile_status(prof["id"], "banned", visible=0, reason="moderation")
            status = "resolved"
        else:
            status = "rejected"
        self.resolve_report(report_id, status)
        return self.q1("SELECT * FROM reports WHERE id = ?", (report_id,))

    def reminder_candidates(self, limit: int = 100) -> list[dict]:
        rows = self.q("""SELECT u.id, u.telegram_user_id, p.id AS profile_id
            FROM users u JOIN profiles p ON p.user_id = u.id
            WHERE p.status = 'active' AND p.is_visible = 1 AND u.is_bot_blocked = 0
              AND NOT EXISTS (SELECT 1 FROM events e WHERE e.user_id = u.id
                              AND e.name = 'reminder_sent' AND e.created_at >= datetime('now', '-1 day'))
              AND EXISTS (SELECT 1 FROM events e WHERE e.user_id = u.id
                          AND e.name IN ('card_skipped', 'card_liked', 'request_sent', 'match_created')
                          AND e.created_at BETWEEN datetime('now', '-7 day') AND datetime('now', '-1 day'))
              AND NOT EXISTS (SELECT 1 FROM events e WHERE e.user_id = u.id
                              AND e.name IN ('card_skipped', 'card_liked', 'request_sent', 'match_created')
                              AND e.created_at >= datetime('now', '-1 day'))
            ORDER BY u.id LIMIT ?""", (limit,))
        result = []
        for row in rows:
            available = self.count_available(row["id"])
            if available > 0:
                row["available"] = available
                result.append(row)
        return result

    def record_reminder(self, user_id: int) -> bool:
        if self.has_event_since(user_id, "reminder_sent", timedelta(days=1)):
            return False
        self.log_event(user_id, "reminder_sent")
        return True

    def has_event_since(self, user_id: int, name: str, age: timedelta) -> bool:
        since = (datetime.now() - age).strftime(FMT)
        return bool(self.q1("SELECT 1 FROM events WHERE user_id = ? AND name = ? AND created_at >= ? LIMIT 1",
                            (user_id, name, since)))

    def like(self, from_user_id: int, to_user_id: int) -> bool:
        if from_user_id == to_user_id:
            return False
        self.x("INSERT INTO likes (from_user_id, to_user_id, status, created_at) VALUES (?,?,'active',?) "
               "ON CONFLICT(from_user_id, to_user_id) DO UPDATE SET status='active', updated_at=?",
               (from_user_id, to_user_id, now_str(), now_str()))
        return True

    def unlike(self, from_user_id: int, to_user_id: int) -> None:
        self.x("UPDATE likes SET status='withdrawn', updated_at=? WHERE from_user_id=? AND to_user_id=?",
               (now_str(), from_user_id, to_user_id))

    def has_like(self, from_user_id: int, to_user_id: int) -> bool:
        row = self.q1("SELECT 1 AS ok FROM likes WHERE from_user_id=? AND to_user_id=? AND status='active'",
                      (from_user_id, to_user_id))
        return bool(row)

    def likes_given(self, user_id: int) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM likes WHERE from_user_id=? AND status='active'", (user_id,))
        return int(row["c"]) if row else 0

    def likes_received(self, user_id: int) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM likes WHERE to_user_id=? AND status='active'", (user_id,))
        return int(row["c"]) if row else 0

    def create_match(self, a: int, b: int) -> Optional[dict]:
        if a == b:
            return None
        lo, hi = min(a, b), max(a, b)
        self.x("INSERT OR IGNORE INTO matches (user_a_id, user_b_id, status, matched_at) VALUES (?,?,'active',?)",
               (lo, hi, now_str()))
        return self.q1("SELECT * FROM matches WHERE user_a_id=? AND user_b_id=?", (lo, hi))

    def active_match(self, a: int, b: int) -> Optional[dict]:
        lo, hi = min(a, b), max(a, b)
        return self.q1("SELECT * FROM matches WHERE user_a_id=? AND user_b_id=? AND status='active'", (lo, hi))

    def match_by_id(self, match_id: int) -> Optional[dict]:
        return self.q1("SELECT * FROM matches WHERE id=?", (match_id,))

    def set_consent(self, match_id: int, user_id: int) -> dict:
        row = self.match_by_id(match_id)
        if not row:
            return {}
        if user_id == row["user_a_id"]:
            self.x("UPDATE matches SET a_consent=1 WHERE id=?", (match_id,))
        elif user_id == row["user_b_id"]:
            self.x("UPDATE matches SET b_consent=1 WHERE id=?", (match_id,))
        return self.match_by_id(match_id) or {}

    def close_match(self, match_id: int, status: str = "closed") -> None:
        self.x("UPDATE matches SET status=?, closed_at=? WHERE id=?", (status, now_str(), match_id))

    def block(self, blocker_user_id: int, blocked_user_id: int, reason: Optional[str] = None) -> None:
        self.x("INSERT OR IGNORE INTO user_blocks (blocker_user_id, blocked_user_id, reason, created_at) "
               "VALUES (?,?,?,?)", (blocker_user_id, blocked_user_id, reason, now_str()))
        m = self.active_match(blocker_user_id, blocked_user_id)
        if m:
            self.close_match(m["id"], "blocked")

    def blocked_between(self, a: int, b: int) -> bool:
        row = self.q1("SELECT 1 AS ok FROM user_blocks WHERE (blocker_user_id=? AND blocked_user_id=?) "
                      "OR (blocker_user_id=? AND blocked_user_id=?)", (a, b, b, a))
        return bool(row)

    # ── жалобы ───────────────────────────────────────────────────────────────
    def report(self, reporter_user_id: int, target_user_id: int, reason: str,
               comment: Optional[str] = None) -> None:
        self.x("INSERT INTO reports (reporter_user_id, target_user_id, reason, comment, created_at) "
               "VALUES (?,?,?,?,?)", (reporter_user_id, target_user_id, reason, comment, now_str()))

    def new_reports(self, limit: int = 20) -> list[dict]:
        return self.q("SELECT * FROM reports WHERE status='new' ORDER BY id DESC LIMIT ?", (limit,))

    def resolve_report(self, report_id: int, status: str = "resolved") -> None:
        self.x("UPDATE reports SET status=?, resolved_at=? WHERE id=?", (status, now_str(), report_id))

    # ── заявки на связь ──────────────────────────────────────────────────────
    def create_request(self, sender_user_id: int, recipient_user_id: int, kind: str,
                       text_content: Optional[str], idempotency_key: str,
                       match_id: Optional[int] = None) -> Optional[int]:
        try:
            cur = self.x("INSERT INTO message_requests (sender_user_id, recipient_user_id, match_id, kind, "
                         "text_content, status, idempotency_key, created_at) VALUES (?,?,?,?,?,'pending',?,?)",
                         (sender_user_id, recipient_user_id, match_id, kind, text_content,
                          idempotency_key, now_str()))
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid or 0)

    def add_attachment(self, request_id: int, media_type: str, file_id: str,
                       caption: Optional[str] = None) -> None:
        self.x("INSERT INTO message_attachments (request_id, media_type, telegram_file_id, caption, created_at) "
               "VALUES (?,?,?,?,?)", (request_id, media_type, file_id, caption, now_str()))

    def request_by_id(self, request_id: int) -> Optional[dict]:
        return self.q1("SELECT * FROM message_requests WHERE id=?", (request_id,))

    def inbox(self, user_id: int, limit: int = 10) -> list[dict]:
        return self.q("SELECT * FROM message_requests WHERE recipient_user_id=? AND status='pending' "
                      "ORDER BY id DESC LIMIT ?", (user_id, limit))

    def outbox_pending(self, user_id: int) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM message_requests WHERE sender_user_id=? AND status='pending'",
                      (user_id,))
        return int(row["c"]) if row else 0

    def requests_today(self, user_id: int) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM message_requests WHERE sender_user_id=? "
                      "AND created_at >= date('now')", (user_id,))
        return int(row["c"]) if row else 0

    def respond_request(self, request_id: int, status: str) -> None:
        if status not in REQUEST_STATUSES:
            raise ValueError(f"Недопустимый статус заявки: {status}")
        self.x("UPDATE message_requests SET status=?, responded_at=? WHERE id=?",
               (status, now_str(), request_id))

    def inbox_item(self, req: dict) -> dict:
        """Единый вид заявки для экранов: text_content/вложение → preview, users → имя.

        Нормализация на границе Store → UI, чтобы рендер не знал имён колонок.
        """
        sender = self.user_by_id(req["sender_user_id"]) or {}
        preview = req.get("text_content")
        if not preview:
            attachment = self.q1("SELECT media_type, caption FROM message_attachments "
                                 "WHERE request_id = ? ORDER BY id DESC LIMIT 1", (req["id"],)) or {}
            preview = attachment.get("caption") or {
                "photo": "📷 фото без подписи",
                "video": "🎬 видео без подписи",
                "video_note": "⭕ видео-кружок",
            }.get(attachment.get("media_type") or req["kind"], "(без текста)")
        return {
            "id": req["id"],
            "kind": req["kind"],
            "preview": preview,
            "sender_name": sender.get("display_name") or "игрок",
            "sender_user_id": req["sender_user_id"],
        }

    def inbox_items(self, user_id: int, limit: int = 10) -> list[dict]:
        return [self.inbox_item(req) for req in self.inbox(user_id, limit)]

    # ── аналитика и мелкие выборки (используются экранами) ───────────────────
    def second_pass_allowed(self, viewer_user_id: int) -> bool:
        """«Второй проход» — не чаще раза в SECOND_PASS_COOLDOWN_H.

        Опорой служит deck_sessions: цикл с cycle_no > 1 и есть второй проход,
        поэтому отдельного поля в схеме не нужно.
        """
        row = self.q1("SELECT started_at FROM deck_sessions WHERE viewer_user_id = ? AND cycle_no > 1 "
                      "ORDER BY cycle_no DESC LIMIT 1", (viewer_user_id,))
        if not row:
            return True
        started = parse_dt(row["started_at"])
        return not started or (datetime.now() - started) >= timedelta(hours=config.SECOND_PASS_COOLDOWN_H)

    def relax_soft_cooldowns(self, viewer_user_id: int) -> int:
        """Снимает только «мягкие» таймеры (показали, человек не отреагировал).

        Явный пропуск и лайк без ответа остаются: иначе второй проход ломал бы
        антизацикливание и позволял бы «пропускать бесконечно».
        """
        cur = self.x("UPDATE deck_state SET next_eligible_at = NULL, last_action_at = ? "
                     "WHERE viewer_user_id = ? AND last_action IN ('shown', 'expired') "
                     "AND next_eligible_at IS NOT NULL", (now_str(), viewer_user_id))
        return cur.rowcount or 0

    def likes_today(self, user_id: int) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM likes WHERE from_user_id = ? AND created_at >= date('now')",
                      (user_id,))
        return int(row["c"]) if row else 0

    def auto_pause_inactive(self, days: int = 30) -> int:
        """Ставит на паузу анкеты, которые давно не заходили: их показ бессмысленен."""
        cur = self.x("UPDATE profiles SET status = 'paused', is_visible = 0, "
                     "auto_paused_at = ?, paused_reason = 'inactivity', updated_at = ? "
                     "WHERE status = 'active' AND last_active_at < datetime('now', ?)",
                     (now_str(), now_str(), f"-{int(days)} day"))
        return cur.rowcount or 0

    def expire_stale_impressions(self, minutes: int = 5) -> int:
        """Закрывает показы без реакции: карточка уходит на кулдаун «показана, но не открыта»."""
        rows = self.q("""SELECT v.id, v.viewer_user_id, v.candidate_user_id, v.cycle_no
                         FROM view_events v
                         WHERE v.action = 'shown' AND v.shown_at < datetime('now', ?)
                           AND NOT EXISTS (SELECT 1 FROM view_events a
                                           WHERE a.viewer_user_id = v.viewer_user_id
                                             AND a.candidate_user_id = v.candidate_user_id
                                             AND a.id > v.id)""", (f"-{int(minutes)} minute",))
        for row in rows:
            self.record_view(row["viewer_user_id"], row["candidate_user_id"], row["cycle_no"],
                             "expired", cooldown_str(config.COOLDOWN_AFTER_SHOWN_H))
            self.upsert_deck_state(row["viewer_user_id"], row["candidate_user_id"],
                                   last_action="expired", last_action_at=now_str(),
                                   next_eligible_at=cooldown_str(config.COOLDOWN_AFTER_SHOWN_H))
        return len(rows)

    def today_salt(self) -> str:
        """Соль дня: при равных скорах порядок выдачи обновляется каждые сутки."""
        return datetime.now().strftime("%Y-%m-%d")

    def served_today(self, viewer_user_id: int, day: Optional[str] = None) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM view_events WHERE viewer_user_id = ? AND action = 'shown' "
                      "AND shown_at >= ?", (viewer_user_id, (day or self.today_salt()) + " 00:00:00"))
        return int(row["c"]) if row else 0

    def new_served_today(self, viewer_user_id: int, day: Optional[str] = None) -> int:
        """Сколько сегодня показано «свежих» анкет — для ограничения их доли."""
        row = self.q1("SELECT COUNT(*) AS c FROM view_events v "
                      "JOIN profiles p ON p.user_id = v.candidate_user_id "
                      "WHERE v.viewer_user_id = ? AND v.action = 'shown' AND v.shown_at >= ? "
                      "AND p.published_at >= datetime(?, '-48 hours')",
                      (viewer_user_id, (day or self.today_salt()) + " 00:00:00", now_str()))
        return int(row["c"]) if row else 0

    def views_made(self, viewer_user_id: int) -> int:
        row = self.q1("SELECT COUNT(DISTINCT candidate_user_id) AS c FROM view_events "
                      "WHERE viewer_user_id = ? AND action = 'shown'", (viewer_user_id,))
        return int(row["c"]) if row else 0

    def times_shown(self, profile_id: int) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM view_events v "
                      "JOIN profiles p ON p.user_id = v.candidate_user_id "
                      "WHERE p.id = ? AND v.action = 'shown'", (profile_id,))
        return int(row["c"]) if row else 0

    def matches_count(self, user_id: int) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM matches WHERE status = 'active' "
                      "AND (user_a_id = ? OR user_b_id = ?)", (user_id, user_id))
        return int(row["c"]) if row else 0

    def user_matches(self, user_id: int, limit: int = 10) -> list[dict]:
        return self.q("SELECT * FROM matches WHERE status = 'active' AND (user_a_id = ? OR user_b_id = ?) "
                      "ORDER BY id DESC LIMIT ?", (user_id, user_id, limit))

    def requests_received(self, user_id: int) -> int:
        row = self.q1("SELECT COUNT(*) AS c FROM message_requests WHERE recipient_user_id = ?", (user_id,))
        return int(row["c"]) if row else 0

    def match_consents(self, match_id: int) -> tuple[int, int]:
        row = self.match_by_id(match_id) or {}
        return int(row.get("a_consent") or 0), int(row.get("b_consent") or 0)

    def like_back_and_match(self, from_user_id: int, to_user_id: int) -> Optional[dict]:
        """Взаимный лайк → мэтч (идемпотентно). Иначе None."""
        if not self.has_like(to_user_id, from_user_id):
            return None
        return self.active_match(from_user_id, to_user_id) or self.create_match(from_user_id, to_user_id)

    def streak(self, user_id: int) -> int:
        """Серия дней подряд с любым действием в боте."""
        rows = self.q("SELECT DISTINCT substr(created_at, 1, 10) AS d FROM events WHERE user_id = ? "
                      "ORDER BY d DESC LIMIT 90", (user_id,))
        days = {r["d"] for r in rows if r["d"]}
        cursor = datetime.now().date()
        streak = 0
        while cursor.isoformat() in days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def level(self, user_id: int) -> str:
        matches = self.matches_count(user_id)
        if matches >= 10:
            return "Легенда пати"
        if matches >= 4:
            return "Капитан отряда"
        if matches >= 1:
            return "Напарник"
        return "Новичок"

    def candidate_view(self, candidate_user_id: int, viewer_user_id: int = 0) -> Optional[dict]:
        """Один кандидат в том же виде, что выдаёт candidate_pool (для перерисовки карточки)."""
        row = self.q1("""
            SELECT p.id AS profile_id, p.user_id, p.gender, p.age, p.country_code, p.city, p.bio,
                   p.photo_file_id, p.published_at, p.last_active_at,
                   COALESCE(ds.skip_count, 0) AS skip_count,
                   COALESCE(ds.impression_count, 0) AS impression_count,
                   ds.last_shown_at, ds.next_eligible_at, ds.is_exhausted, ds.last_action
            FROM profiles p
            LEFT JOIN deck_state ds ON ds.viewer_user_id = ? AND ds.candidate_user_id = p.user_id
            WHERE p.user_id = ? AND p.status = 'active' AND p.is_visible = 1
        """, (viewer_user_id, candidate_user_id))
        if not row:
            return None
        row["languages"] = set(self.profile_languages(row["profile_id"]))
        row["games"] = set(self.profile_games(row["profile_id"]))
        row["display_name"] = _display_name(self.user_by_id(candidate_user_id) or {})
        return row

    def set_profile_games(self, profile_id: int, games: list[str]) -> None:
        self.x("DELETE FROM profile_games WHERE profile_id = ?", (profile_id,))
        for game in games[:config.MAX_GAMES]:
            self.x("INSERT OR IGNORE INTO profile_games (profile_id, game_name) VALUES (?, ?)",
                   (profile_id, game))
        self.x("UPDATE profiles SET updated_at = ? WHERE id = ?", (now_str(), profile_id))

    def update_profile_fields(self, profile_id: int, **fields: Any) -> None:
        allowed = {"bio", "photo_file_id", "city", "age", "gender", "country_code"}
        sets, args = [], []
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                args.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        args += [now_str(), profile_id]
        self.x(f"UPDATE profiles SET {', '.join(sets)} WHERE id = ?", args)

    # ── статистика ───────────────────────────────────────────────────────────
    def stats(self) -> dict:
        def one(sql: str, args: Iterable = ()) -> int:
            row = self.q1(sql, args)
            return int(list(row.values())[0]) if row else 0

        return {
            "profiles_active": one("SELECT COUNT(*) FROM profiles WHERE status='active'"),
            "profiles_total": one("SELECT COUNT(*) FROM profiles"),
            "likes": one("SELECT COUNT(*) FROM likes WHERE status='active'"),
            "matches": one("SELECT COUNT(*) FROM matches WHERE status='active'"),
            "requests": one("SELECT COUNT(*) FROM message_requests"),
            "reports_new": one("SELECT COUNT(*) FROM reports WHERE status='new'"),
        }


def _cooldown_active(value: Optional[str]) -> bool:
    dt = parse_dt(value)
    return bool(dt and dt > datetime.now())


def cooldown_str(hours: float, cap_hours: Optional[float] = None) -> str:
    if cap_hours is not None:
        hours = min(hours, cap_hours)
    return (datetime.now() + timedelta(hours=hours)).strftime(FMT)


def _display_name(user: dict) -> str:
    """Отображаемое имя: имя из Telegram, иначе @username, иначе нейтральное."""
    name = (user.get("first_name") or "").strip()
    if name:
        return name
    handle = (user.get("telegram_username") or "").strip()
    if handle:
        return f"@{handle}"
    return f"игрок #{user.get('id') or '?'}"
