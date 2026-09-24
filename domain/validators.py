"""Валидация пользовательского ввода при регистрации и редактировании анкеты."""
from __future__ import annotations

import re
from typing import Optional

import config

GAME_SPLIT_RE = re.compile(r"[,;\n]+")


def ok(value):
    return True, value


def fail(message: str):
    return False, message


def validate_age(text: str) -> tuple[bool, object]:
    raw = (text or "").strip()
    if not raw.isdigit():
        return fail("Возраст — это число, например 22. Попробуй ещё раз.")
    age = int(raw)
    if age < config.MIN_AGE:
        return fail(f"Бот только для игроков {config.MIN_AGE}+. Укажи возраст от {config.MIN_AGE}.")
    if age > config.MAX_AGE:
        return fail("Слишком большое число — так не бывает.")
    return ok(age)


def validate_city(text: str) -> tuple[bool, object]:
    raw = (text or "").strip()
    if len(raw) < 2:
        return fail("Название города слишком короткое (минимум 2 символа).")
    if len(raw) > config.CITY_MAX:
        return fail(f"Слишком длинное название (максимум {config.CITY_MAX} символов).")
    return ok(raw)


def validate_bio(text: str) -> tuple[bool, object]:
    raw = " ".join((text or "").split())
    if len(raw) < config.BIO_MIN:
        return fail(f"Расскажи чуть подробнее — минимум {config.BIO_MIN} символов.")
    if len(raw) > config.BIO_MAX:
        return fail(f"Слишком длинный текст (максимум {config.BIO_MAX} символов).")
    return ok(raw)


def validate_games(text: str) -> tuple[bool, object]:
    parts = [p.strip() for p in GAME_SPLIT_RE.split(text or "") if p.strip()]
    cleaned: list[str] = []
    for part in parts:
        if len(part) > config.GAME_NAME_MAX:
            return fail(f"Название «{part[:30]}…» слишком длинное.")
        if part.lower() not in (c.lower() for c in cleaned):
            cleaned.append(part)
    if not cleaned:
        return fail("Не разобрал ни одной игры. Напиши через запятую, например: Valorant, Dota 2.")
    if len(cleaned) > config.MAX_GAMES:
        return fail(f"Максимум {config.MAX_GAMES} игр. Оставь самые важные.")
    return ok(cleaned)


def validate_languages(codes: list[str]) -> tuple[bool, object]:
    unique = [c for c in dict.fromkeys(codes) if c in config.LANGUAGES]
    if not unique:
        return fail("Выбери хотя бы один язык общения.")
    if len(unique) > config.MAX_LANGUAGES:
        return fail(f"Максимум {config.MAX_LANGUAGES} языка.")
    return ok(unique)


def validate_request_text(text: str) -> tuple[bool, object]:
    raw = " ".join((text or "").split())
    if not raw:
        return fail("Сообщение не должно быть пустым.")
    if len(raw) > config.REQUEST_TEXT_MAX:
        return fail(f"Слишком длинное сообщение (максимум {config.REQUEST_TEXT_MAX} символов).")
    return ok(raw)


def normalize_username(raw: Optional[str]) -> str:
    handle = (raw or "").strip().lstrip("@")
    return handle if handle else ""


def contact_url(username: Optional[str], tg_id: int) -> str:
    """Кликабельная ссылка на контакт: username, иначе глубокий линк по id."""
    handle = normalize_username(username)
    if handle:
        return f"https://t.me/{handle}"
    return f"tg://user?id={tg_id}"


def esc(text: Optional[str]) -> str:
    """Экранирование под parse_mode=HTML."""
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def age_bucket(age: int) -> str:
    for label, lo, hi in config.AGE_BUCKETS:
        if lo <= age <= hi:
            return label
    return f"{config.MIN_AGE}+"
