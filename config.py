"""PartyRadar — конфигурация: переменные окружения и все настраиваемые лимиты.

Все «магические числа» политики колоды живут здесь, чтобы доменную логику
можно было тестировать и подкручивать без правки кода.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ── окружение ────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
DB_PATH: str = os.getenv("DB_PATH", "bot.db")
CHANNEL_LINK: str = os.getenv("CHANNEL_LINK", "")
CONTACT_LINK: str = os.getenv("CONTACT_LINK", "")


def _admin_ids() -> list[int]:
    raw = (os.getenv("ADMIN_IDS") or "").replace(" ", "")
    return [int(x) for x in raw.split(",") if x.isdigit()]


ADMIN_IDS: list[int] = _admin_ids()

# ── правила выдачи карточек ──────────────────────────────────────────────────
STALE_IMPRESSION_MIN = 5          # показ без действия → expired через 5 минут
COOLDOWN_AFTER_SHOWN_H = 6        # показали, пользователь не отреагировал
COOLDOWN_SKIP_BASE_H = 24         # явный пропуск
COOLDOWN_SKIP_MAX_H = 24 * 30     # потолок прогрессивного пропуска
COOLDOWN_OPENED_H = 12            # открыл профиль и не решил
COOLDOWN_LIKE_UNANSWERED_H = 24 * 7   # лайк без взаимности
COOLDOWN_UNLIKE_H = 24            # снял лайк
ANTI_REPEAT_H = 24                # никаких повторных показов чаще, чем раз в сутки
NEW_PROFILE_BOOST_H = 48          # буст свежих анкет
NEW_PROFILE_MAX_SHARE = 0.30      # не более 30% выдачи — свежие анкеты
INACTIVITY_DAYS = 30              # авто-пауза неактивных
SMALL_BASE = 10                   # ниже этого — предлагаем ослабить фильтры
SECOND_PASS_COOLDOWN_H = 24 * 7   # «второй проход» — не чаще раза в 7 суток
DAILY_BONUS_LIMIT = 1             # карточка дня — одна в сутки

# ── сообщение о раннем этапе (показывается при /start) ───────────────────────
EARLY_STAGE_NOTE: tuple[str, ...] = (
    "\U0001f680 PartyRadar на раннем этапе развития:",
    "идёт активная реклама, игроки подтягиваются каждый день.",
    "Заглядывай почаще — твою анкету скоро увидят.",
)

# ── незавершённая анкета ────────────────────────────────────────────────────
DRAFT_TTL_DAYS = 30             # сколько дней хранится черновик без изменений
MAX_VALIDATION_ATTEMPTS = 5     # ошибок подряд на одном шаге, дальше — подсказка формата

# ── веса скоринга ────────────────────────────────────────────────────────────
W_NEW_PROFILE = 25
W_ACTIVITY_MAX = 20
W_LANG_FIRST = 20
W_LANG_NEXT = 5
W_LANG_MAX = 30
W_GAME_FIRST = 15
W_GAME_NEXT = 4
W_GAME_MAX = 27
W_GEO_CITY = 10
W_GEO_COUNTRY = 5
W_AGE_FIT = 8
W_RICH_CARD = 5
P_SHOWN_IN_CYCLE = -30
P_SKIP_PER = -4
P_SKIP_MAX = -20
P_REPEAT_24H = -50
P_STALE_LIKE = -15

# ── лимиты пользовательского ввода ───────────────────────────────────────────
BIO_MIN, BIO_MAX = 20, 1000
CITY_MAX = 60
MAX_GAMES = 5
GAME_NAME_MAX = 80
MAX_LANGUAGES = 4
MIN_AGE, MAX_AGE = 18, 99
REQUEST_TEXT_MAX = 1000
REQUESTS_PER_DAY = 20             # антиспам
LIKES_PER_DAY = 100
DAILY_REQUEST_LIMIT = REQUESTS_PER_DAY    # синоним для обработчиков
DAILY_CARD_LIMIT = 60             # сколько карточек можно посмотреть за сутки
SECOND_PASS_PER_DAY = 5           # аварийных «вторых проходов» в сутки
BOT_USERNAME = os.getenv("BOT_USERNAME", "PartyRadarBot")


def bot_link(payload: str = "") -> str:
    """Ссылка на бота (с deep-link payload для приглашений и роста)."""
    base = f"https://t.me/{BOT_USERNAME}"
    return f"{base}?start={payload}" if payload else base

AGE_BUCKETS = [("18-24", 18, 24), ("25-34", 25, 34), ("35-44", 35, 44), ("45+", 45, MAX_AGE)]

LANGUAGES: dict[str, str] = {
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English",
    "uk": "🇺🇦 Українська",
    "kk": "🇰🇿 Қазақша",
    "be": "🇧🇾 Беларуская",
    "de": "🇩🇪 Deutsch",
    "es": "🇪🇸 Español",
    "tr": "🇹🇷 Türkçe",
    "pl": "🇵🇱 Polski",
    "pt": "🇧🇷 Português",
}

COUNTRIES: dict[str, str] = {
    "RU": "🇷🇺 Россия",
    "BY": "🇧🇾 Беларусь",
    "KZ": "🇰🇿 Казахстан",
    "UA": "🇺🇦 Украина",
    "UZ": "🇺🇿 Узбекистан",
    "KG": "🇰🇬 Кыргызстан",
    "AM": "🇦🇲 Армения",
    "GE": "🇬🇪 Грузия",
    "DE": "🇩🇪 Германия",
    "US": "🇺🇸 США",
    "PL": "🇵🇱 Польша",
    "TR": "🇹🇷 Турция",
    "OTHER": "🌍 Другая",
}

GENDERS: dict[str, str] = {"m": "👤 Парень", "f": "👤 Девушка", "x": "⚪ Не указывать"}

GENRES: dict[str, str] = {"shooter": "шутеры", "moba": "MOBA", "coop": "кооператив", "sandbox": "песочницы", "mmo": "MMO", "survival": "выживание", "strategy": "стратегии", "sports": "спортивные игры"}
GAME_GENRES: dict[str, str] = {
    "cs2": "shooter", "counter-strike 2": "shooter", "valorant": "shooter", "overwatch": "shooter",
    "dota 2": "moba", "dota2": "moba", "league of legends": "moba", "lol": "moba",
    "helldivers 2": "coop", "deep rock galactic": "coop", "warframe": "coop",
    "minecraft": "sandbox", "terraria": "sandbox", "gta online": "sandbox",
    "world of warcraft": "mmo", "wow": "mmo", "ffxiv": "mmo", "final fantasy xiv": "mmo",
    "rust": "survival", "ark": "survival", "ark: survival evolved": "survival",
    "hearts of iron": "strategy", "hearts of iron iv": "strategy", "ck3": "strategy", "crusader kings 3": "strategy",
    "ea fc": "sports", "fifa": "sports",
}

def game_genres(names) -> set[str]:
    return {GAME_GENRES[str(name).strip().casefold()] for name in names if str(name).strip().casefold() in GAME_GENRES}


REPORT_REASONS: dict[str, str] = {
    "adult": "🔞 18+ контент",
    "toxicity": "🤬 Токсичность",
    "spam": "🕵️ Спам / реклама",
    "fake": "🎭 Фейк / чужая картинка",
    "other": "⚠️ Другое",
}
