"""Экраны бота: чистые функции, возвращающие Screen(text, rows, photo).

Вся вёрстка и тексты живут здесь, обработчики только собирают данные.
Кнопка — пара (подпись, действие): действие-URL отдаётся как URL-кнопка,
остальное уходит в callback_data (лимит Telegram — 64 байта на строку).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

import config
from db.store import age_window, window_for

TITLE = "PARTY RADAR"


@dataclass
class Screen:
    text: str
    rows: list[list[tuple[str, str]]] = field(default_factory=list)
    photo: Optional[str] = None


def box(title: str, lines: Iterable[str], footer: str = "") -> str:
    """Карточка в стиле HUD: рамка, заголовок, блоки строк."""
    head = f"╭─〔 {title} 〕─╮"
    body = "\n".join(l for l in lines if l is not None)
    tail = "╰────────────────╯"
    text = f"{head}\n{body}\n{tail}"
    return f"{text}\n\n{footer}" if footer else text


def sep() -> str:
    return "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"


def is_url(action: str) -> bool:
    return action.startswith(("http://", "https://", "tg://"))


# ── онбординг ────────────────────────────────────────────────────────────────
def screen_welcome(has_invite: bool = False) -> Screen:
    lines = [
        "🛰️ Найдём игрока для твоей следующей пати.",
        "",
        "Без свайпов ради флирта: только игры, язык,",
        "часовой пояс и нормальная коммуникация.",
        "",
        "За минуту соберём сигнал и покажем подходящих игроков.",
    ]
    if has_invite:
        lines += ["", "📨 Ты пришёл по приглашению — друг будет в курсе."]
    lines += ["", *config.EARLY_STAGE_NOTE]
    return Screen(box(TITLE, lines), [
        [("🎮 Создать сигнал", "onb:start")],
        [("👀 Как это работает", "onb:about")],
    ])


def screen_consent() -> Screen:
    """Дисклеймер 18+ и правила — до анкеты, как требует бренд-документ."""
    lines = [
        "Только 18+.",
        "",
        "PartyRadar создан для поиска игровых напарников — здесь ищут пати,",
        "не романтические знакомства.",
        "",
        "Запрещены буллинг, травля, угрозы, дискриминация, сексуальный контент и NSFW.",
        "Не публикуй телефон, адрес, документы и другие личные данные.",
        "Нарушителей можно заблокировать и пожаловаться — жалоба скрывает карточку сразу.",
    ]
    return Screen(box("ПЕРЕД СТАРТОМ", lines), [
        [("✅ Мне 18+ и я согласен", "onb:consent")],
        [("🚪 Не продолжать", "onb:exit")],
    ])


def screen_about() -> Screen:
    lines = [
        "Как работает радар:",
        "",
        "1️⃣ Ты собираешь карточку-сигнал: игры, язык, город, пара строк о себе.",
        "2️⃣ Бот показывает по одной анкете подходящих игроков.",
        "3️⃣ ❤️ «В пати» — если игрок ответит взаимно, откроется обмен контактами.",
        "4️⃣ 💬 «Написать / медиа» — короткая заявка: текст, фото, видео или кружок.",
        "5️⃣ ✖️ «Пропустить» — карточка уйдёт из выдачи и вернётся позже.",
        "",
        "Просмотренные анкеты не исчезают навсегда: у каждой свой таймер,",
        "поэтому колода не зацикливается и не заканчивается на второй день.",
    ]
    return Screen(box("КАК ЭТО РАБОТАЕТ", lines), [
        [("🎮 Создать сигнал", "onb:start")],
        [("🏠 В меню", "menu:home")],
    ])


# ── регистрация ──────────────────────────────────────────────────────────────
def screen_reg_step(step: int, total: int, title: str, prompt: str,
                    options: Optional[list[list[tuple[str, str]]]] = None,
                    note: str = "") -> Screen:
    lines = [f"Шаг {step} из {total} — {title}", "", prompt]
    if note:
        lines += ["", note]
    return Screen(box(TITLE, lines), options or [], )


def screen_registration_resume(step: int, total: int, title: str,
                               progress_summary: str = "") -> Screen:
    """Есть начатая анкета: продолжить с того же места или начать заново."""
    lines = [
        "\u23f8 Начатая анкета не потеряна.",
        "",
        f"Ты остановился на шаге {step} из {total} — «{title}».",
        "Можно продолжить с этого места, ничего не заполняя повторно.",
    ]
    if progress_summary:
        lines += ["", progress_summary]
    rows = [
        [("\u25b6\ufe0f Продолжить анкету", "onb:resume")],
        [("\U0001f504 Начать заново", "onb:restart")],
        [("\U0001f3e0 В меню", "menu:home")],
    ]
    return Screen(box("НЕЗАКОНЧЕННАЯ АНКЕТА", lines), rows)


def screen_registration_confirm_restart() -> Screen:
    """Подтверждение сброса: только явное действие удаляет прогресс."""
    lines = [
        "\U0001f504 Начать анкету заново?",
        "",
        "Все уже заполненные поля будут удалены — вернуть их будет нельзя.",
    ]
    rows = [
        [("\u2705 Да, начать заново", "onb:restart_confirm")],
        [("\u21a9\ufe0f Оставить как есть", "onb:restart_keep")],
    ]
    return Screen(box("ПОДТВЕРЖДЕНИЕ", lines), rows)


def screen_registration_exit(step: int, total: int, title: str) -> Screen:
    """Выход из визарда: прогресс сохранён."""
    lines = [
        "Ок, выходим из анкеты.",
        "",
        f"Прогресс сохранён: остановился на шаге {step} из {total} — «{title}».",
        f"Вернёшься — продолжишь с этого места. Черновик хранится {config.DRAFT_TTL_DAYS} дней.",
    ]
    rows = [
        [("\u25b6\ufe0f Продолжить анкету", "onb:resume")],
        [("\U0001f3e0 В меню", "menu:home")],
    ]
    return Screen(box("ВЫХОД", lines), rows)


def screen_preview(draft: dict) -> Screen:
    langs = ", ".join(config.LANGUAGES.get(c, c) for c in draft.get("languages", [])) or "—"
    pref = {"any": "любой пол", "same": "свой пол", "other": "противоположный пол"}.get(draft.get("gender_pref", "any"), "любой пол")
    mode = "по полу и городу" if draft.get("search_mode", "city") == "city" else "по жанру игр и полу"
    age = int(draft.get("age", config.MIN_AGE))
    age_low, age_high = age_window(age)
    lines = [
        "🛰️ Сигнал собран. Проверь карточку:",
        "",
        f"👤 Пол: {config.GENDERS.get(draft.get('gender', 'x'), '—')}",
        f"🎂 Возраст: {draft.get('age', '—')}",
        f"🌍 {config.COUNTRIES.get(draft.get('country', 'OTHER'), '—')} • {draft.get('city') or 'город не указан'}",
        f"🗣️ {langs}",
        f"🎮 {', '.join(draft.get('games', [])) or '—'}",
        f"🔎 Поиск: {mode}, {pref}, возраст {age_low}–{age_high}",
        "",
        f"📡 {draft.get('bio', '')}",
    ]
    rows = [[("🚀 Запустить анкету", "onb:publish"), ("✏️ Заполнить заново", "onb:restart")]]
    if not draft.get("photo_file_id"):
        rows.insert(0, [("🖼️ Добавить картинку", "onb:photo")])
    else:
        lines.insert(3, "🖼️ Картинка прикреплена")
    return Screen(box("ПРЕДПРОСМОТР", lines), rows)


# ── главное меню ─────────────────────────────────────────────────────────────
def screen_home(active: bool, shown: int, likes_out: int, likes_in: int, inbox: int = 0,
                streak: int = 0, level: str = "Новичок") -> Screen:
    status = "🟢 Анкета активна" if active else "⛔ Анкета выключена"
    lines = [
        f"{status} • просмотрено: {shown}",
        f"⚡ Серия: {streak} дн. • уровень: {level}",
        "",
        f"❤️ Лайков отправлено: {likes_out} • получено: {likes_in}",
    ]
    if inbox:
        lines.append(f"📥 Новых заявок: {inbox}")
    lines += ["", "Что делаем?"]
    rows = [[("🛰️ Искать напарника", "deck:next")]]
    if inbox:
        rows.append([(f"📥 Входящие заявки ({inbox})", "menu:inbox")])
    else:
        rows.append([("📥 Входящие заявки", "menu:inbox")])
    rows += [
        [("👤 Моя анкета", "menu:profile"), ("🏆 Мой прогресс", "menu:progress")],
        [("⚙️ Фильтры поиска", "menu:filters")],
    ]
    rows.append([("⛔ Выключить анкету", "profile:pause")] if active
                else [("🟢 Включить анкету", "profile:resume")])
    return Screen(box(TITLE, lines), rows)


# ── колода ───────────────────────────────────────────────────────────────────
def screen_card(cand: dict, compat: dict, city: str = "", viewer_city: str = "",
                remaining: int = 0) -> Screen:
    langs = ", ".join(config.LANGUAGES.get(c, c) for c in compat.get("languages", [])) or "—"
    games = ", ".join(compat.get("games", [])) or "—"
    matched_games = ", ".join(compat.get("matched_games", []))
    matched_langs = ", ".join(config.LANGUAGES.get(c, c) for c in compat.get("matched_languages", []))
    lines = [
        f"🟢 {compat.get('status', 'ОНЛАЙН')} • ищет пати",
        f"🛰️ ОСТАЛОСЬ {remaining}" if remaining else "🛰️ СИГНАЛ ПОСЛЕДНИЙ В ЭТОМ ПРОХОДЕ",
        "",
        f"🎮 Ник: {cand.get('display_name') or 'игрок'}",
        f"👤 Пол: {config.GENDERS.get(cand.get('gender', 'x'), '—')}",
        f"🎂 Возраст: {cand.get('age')}",
        f"🌍 {config.COUNTRIES.get(cand.get('country_code', 'OTHER'), '—')}"
        f"{' • ' + (cand.get('city') or '') if cand.get('city') else ''}",
        f"🗣️ {langs}",
        "",
        f"🎯 ИГРЫ",
        f"• {games}",
        "",
        "📡 ИЩЕТ",
        cand.get("bio", ""),
        "",
        "⚡ СОВПАЛО",
        f"• {'игры: ' + matched_games if matched_games else 'общая игра не отмечена'}",
        f"• {'язык: ' + matched_langs if matched_langs else 'язык: ' + langs}",
        "",
        "🛰️ Радар помнит ваши решения: показанные сигналы не возвращаются",
        "в этот проход, но вернутся позже — у каждой карточки свой таймер.",
    ]
    if viewer_city and cand.get("city") and viewer_city.strip().lower() == (cand.get("city") or "").strip().lower():
        lines.append("📍 Твой город")
    pid = cand.get("profile_id")
    rows = [
        [("❤️ В пати", f"deck:like:{pid}"), ("💬 Написать / медиа", f"deck:request:{pid}")],
        [("✖️ Пропустить", f"deck:skip:{pid}"), ("⛔ Выключить анкету", "profile:pause")],
        [("🚩 Пожаловаться", f"report:open:{pid}")],
    ]
    return Screen(box(f"{TITLE} • СИГНАЛ #{pid}", lines), rows, photo=cand.get("photo_file_id"))


def screen_exhausted(reason: str, advice: list[str], available: int = 0) -> Screen:
    explanations = {
        "no_candidates": "Под твои фильтры сейчас нет ни одной анкеты.",
        "all_shown": "Ты просмотрел все подходящие анкеты в этом проходе.",
        "all_on_cooldown": "Все подходящие игроки сейчас на таймере — они вернутся позже.",
    }
    lines = [
        "🛰️ РАДАР ЧИСТ",
        "",
        explanations.get(reason, "Новых сигналов пока нет."),
        f"Свободных анкет в базе: {available}",
    ]
    report = next((x for x in advice if x.startswith("cycle_report:")), None)
    if report:
        _, shown, likes, requests = report.split(":", 3)
        lines += [f"Скан завершён: {shown} сигналов, {likes} отклика, {requests} заявка"]
    lines += [
        "",
        "Просмотренные карточки не пропадают навсегда — таймер истечёт,",
        "и они вернутся в выдачу. Новые игроки появляются здесь автоматически.",
    ]
    rows = []
    if "switch_mode_city" in advice:
        lines.append("Общего жанра не нашлось — переключи режим поиска на «пол + город».")
        rows.append([("📍 Переключить на пол + город", "filters:mode:city")])
    rows.append([("🔄 Второй проход", "deck:second_pass")])
    if "expand_filters" in advice:
        rows.insert(0, [("🌍 Ослабить фильтры", "menu:filters")])
    rows.append([("⚙️ Фильтры поиска", "menu:filters"), ("📨 Пригласить друга", "ref:share")])
    rows.append([("🏠 В меню", "menu:home")])
    return Screen(box(TITLE, lines), rows)


# ── профиль ──────────────────────────────────────────────────────────────────
def screen_my_profile(prof: dict, langs: list[str], games: list[str], shown: int,
                      likes_out: int, likes_in: int, active: bool) -> Screen:
    lines = [
        f"{'🟢 Активна' if active else '⛔ Выключена'} • показана {shown} игрокам",
        f"❤️ Лайков отправлено: {likes_out} • получено: {likes_in}",
        f"🎮 Игр: {len(games)} • заполнение: {_fill(prof, langs, games).progress}%",
        "",
        f"👤 {config.GENDERS.get(prof.get('gender', 'x'), '—')}, {prof.get('age')}",
        f"🌍 {config.COUNTRIES.get(prof.get('country_code', 'OTHER'), '—')}"
        f"{' • ' + (prof.get('city') or '') if prof.get('city') else ''}",
        f"🗣️ {', '.join(config.LANGUAGES.get(c, c) for c in langs) or '—'}",
        f"🎮 {', '.join(games) or '—'}",
        "",
        f"📡 {prof.get('bio', '')}",
    ]
    rows = [
        [("✏️ Изменить текст", "profile:edit_bio"), ("🖼️ Сменить картинку", "profile:photo")],
        [("🎮 Изменить игры", "profile:edit_games"), ("⚙️ Фильтры", "menu:filters")],
    ]
    rows.append([("⛔ Выключить анкету", "profile:pause")] if active
                else [("🟢 Включить анкету", "profile:resume")])
    rows.append([("🏠 В меню", "menu:home")])
    return Screen(box("МОЙ СИГНАЛ", lines), rows, photo=prof.get("photo_file_id"))


@dataclass(frozen=True)
class FillItem:
    """Один пункт заполненности анкеты: без внутренних имён на экране."""
    key: str
    label: str
    points: int
    complete: bool
    detail: str = ""


@dataclass(frozen=True)
class FillReport:
    """Заполненность анкеты: обязательные поля дают процент, необязательные — подсказки."""
    progress: int
    required: tuple
    optional: tuple

    @property
    def missing(self) -> list:
        return [item for item in self.required if not item.complete]

    @property
    def improvements(self) -> list:
        return [item for item in self.optional if not item.complete]


def _fill(prof: dict, langs: list[str], games: list[str]) -> FillReport:
    """Считает полноту анкеты по обязательным полям визарда.

    Обязательные пункты в сумме дают 100: их отсутствие уменьшает процент.
    Фото, город, число игр и длина текста — улучшения, они процент не режут:
    именно их отсутствие раньше показывало 30% у полностью заполненной анкеты.
    """
    bio = (prof.get("bio") or "").strip()
    gender = prof.get("gender")
    age = prof.get("age")
    required = (
        FillItem("gender", "Пол", 15, gender in config.GENDERS,
                 "выбери пол или «не указывать»"),
        FillItem("age", "Возраст", 15,
                 bool(age) and config.MIN_AGE <= int(age) <= config.MAX_AGE,
                 f"укажи возраст от {config.MIN_AGE}"),
        FillItem("country", "Страна", 15, bool(prof.get("country_code")) and
                 prof.get("country_code") in config.COUNTRIES, "выбери страну"),
        FillItem("languages", "Языки", 15, bool(langs), "выбери хотя бы один язык"),
        FillItem("games", "Игры", 15, bool(games), "укажи хотя бы одну игру"),
        FillItem("bio", "О себе", 25, len(bio) >= config.BIO_MIN, "напиши пару строк о себе"),
    )
    optional = (
        FillItem("photo", "Картинка", 0, bool(prof.get("photo_file_id")),
                 "можно добавить картинку — карточки с ней открывают чаще"),
        FillItem("city", "Город", 0, bool(prof.get("city")),
                 "укажи город, чтобы находить игроков рядом"),
        FillItem("games_quality", "Три и более игры", 0, len(games) >= 3,
                 "больше игр — больше совпадений"),
        FillItem("bio_quality", "Подробное описание", 0, len(bio) >= 80,
                 "подробное описание помогает выбрать тебя"),
    )
    progress = min(100, sum(item.points for item in required if item.complete))
    return FillReport(progress, required, optional)


def mark_selected(label: str, selected: bool) -> str:
    """Единый вид отметки выбранного пункта: суффикс « ✅»."""
    base = label.rstrip()
    if base.endswith("\u2705"):
        base = base[:-1].rstrip()
    return f"{base} \u2705" if selected else base


def screen_paused(active: bool, inbox: int = 0) -> Screen:
    if active:
        lines = ["🟢 Анкета снова в эфире.",
                 "Тебя снова показывают подходящим игрокам."]
    else:
        lines = ["⛔ Анкета выключена.",
                 "Твоя карточка не показывается новым игрокам,",
                 "но текущие мэтчи и входящие заявки сохранены.",
                 "Включи её, когда снова будешь искать пати."]
    rows = [[("🟢 Включить анкету", "profile:resume")]] if not active else []
    if inbox:
        rows.append([(f"📥 Посмотреть заявки ({inbox})", "menu:inbox")])
    rows.append([("🏠 В меню", "menu:home")])
    return Screen(box(TITLE, lines), rows)


# ── мэтч и контакты ──────────────────────────────────────────────────────────
def screen_match(opponent: dict, a_consent: bool, b_consent: bool, match_id: int,
                 viewer_side: str = "a") -> Screen:
    my_consent = a_consent if viewer_side == "a" else b_consent
    other_consent = b_consent if viewer_side == "a" else a_consent
    name = opponent.get("display_name") or "игрок"
    lines = [
        "⚡ Взаимный сигнал!",
        "",
        f"Вы оба нажали «В пати»: {name}.",
        "",
        "Контакты не открываются автоматически:",
        "каждый подтверждает обмен отдельно.",
        "",
        f"Ты: {'✅ разрешил' if my_consent else '⏳ не подтвердил'}",
        f"Он: {'✅ разрешил' if other_consent else '⏳ не подтвердил'}",
    ]
    rows: list[list[tuple[str, str]]] = []
    if my_consent and other_consent:
        handle = opponent.get("telegram_username") or opponent.get("username")
        tg_id = opponent.get("telegram_user_id") or 0
        from domain.validators import contact_url
        lines += ["", "✅ Контакты открыты. Напиши и договоритесь о времени."]
        rows.append([(f"💬 {name}", (contact_url(handle, tg_id)))])
    elif not my_consent:
        rows.append([("🔓 Разрешить контакт", f"match:share:{match_id}")])
    rows.append([("📨 Написать через бота", f"request:type:match:{match_id}")])
    rows.append([("🚩 Пожаловаться", f"report:match:{match_id}")])
    rows.append([("🏠 В меню", "menu:home")])
    return Screen(box("ПАТИ СОБИРАЕТСЯ", lines), rows)


# ── заявки на связь ──────────────────────────────────────────────────────────
def screen_request_type(profile_id: int, name: str) -> Screen:
    lines = [
        f"Ты отправляешь игроку {name} короткую заявку.",
        "",
        "Выбери формат первого контакта.",
        "Медиа отправляется только после согласия адресата.",
    ]
    rows = [
        [("🎮 Написать", f"request:go:{profile_id}:text")],
        [("🖼️ Фото", f"request:go:{profile_id}:photo"), ("🎥 Видео", f"request:go:{profile_id}:video")],
        [("⭕ Кружок", f"request:go:{profile_id}:video_note")],
        [("🛑 Отмена", f"deck:card:{profile_id}")],
    ]
    return Screen(box("ЗАПРОС В ПАТИ", lines), rows)


def screen_request_compose(profile_id: int, kind: str) -> Screen:
    prompts = {
        "text": "Напиши короткое сообщение: «Привет! Тоже ищу пати в Valorant сегодня после 20:00».",
        "photo": "Пришли фото одним сообщением — можно сетап, скриншот из игры или картинку.",
        "video": "Пришли видео одним сообщением (до 60 секунд).",
        "video_note": "Запиши видео-кружок: короткое приветствие работает лучше всего.",
    }
    lines = [
        prompts.get(kind, "Отправь сообщение."),
        "",
        "Сообщение уйдёт игроку только как заявка — без твоего username,",
        "пока он её не примет.",
    ]
    return Screen(box("ЗАЯВКА", lines), [[("🛑 Отмена", f"deck:card:{profile_id}")]])


def screen_inbox(items: list[dict]) -> Screen:
    if not items:
        lines = ["📭 Пока пусто.",
                 "",
                 "Заявки приходят от игроков, которые хотят собрать с тобой пати."]
        return Screen(box("ВХОДЯЩИЕ ЗАЯВКИ", lines), [[("🏠 В меню", "menu:home")]])
    lines = [f"📥 Заявок в очереди: {len(items)}", ""]
    rows: list[list[tuple[str, str]]] = []
    for idx, item in enumerate(items, start=1):
        kind_icon = {"text": "🎮 Текст", "photo": "🖼️ Фото", "video": "🎥 Видео",
                     "video_note": "⭕ Кружок"}.get(item["kind"], "🎮")
        lines.append(f"{idx}. {item.get('sender_name') or 'игрок'} — {kind_icon}")
        if item.get("preview"):
            lines.append(f"    «{item['preview'][:60]}»")
    lines += ["", "Открой заявку, чтобы увидеть анкету и решить."]
    for idx, item in enumerate(items, start=1):
        rows.append([(f"👀 Открыть заявку {idx}", f"inbox:open:{item['id']}")])
    rows.append([("🏠 В меню", "menu:home")])
    return Screen(box("ВХОДЯЩИЕ ЗАЯВКИ", lines), rows)


def screen_inbox_item(item: dict, sender: dict, common: list[str]) -> Screen:
    lines = [
        f"Заявка от {sender.get('display_name') or 'игрока'}",
        f"🎂 {sender.get('age')} • 🌍 {config.COUNTRIES.get(sender.get('country_code', 'OTHER'), '—')}",
        f"🎮 {', '.join(common) or '—'}",
        "",
        f"«{item.get('preview') or '(медиа без подписи)'}»",
        "",
        "Принять — откроется обмен контактами.",
    ]
    rows = [
        [("✅ Принять", f"inbox:accept:{item['id']}")],
        [("❌ Отклонить", f"inbox:decline:{item['id']}"), ("🔒 Заблокировать", f"inbox:block:{item['id']}")],
        [("🏠 В меню", "menu:home")],
    ]
    return Screen(box("ЗАЯВКА", lines), rows)


def screen_request_sent(name: str) -> Screen:
    lines = [
        f"📨 Заявка отправлена игроку {name}.",
        "",
        "Как только он ответит, придёт уведомление.",
        "Пока заявка ждёт ответа, его карточка остаётся в твоей колоде с таймером.",
    ]
    return Screen(box(TITLE, lines), [[("🛰️ Следующая карточка", "deck:next"), ("🏠 В меню", "menu:home")]])


# ── жалобы и фильтры ─────────────────────────────────────────────────────────
def screen_report(profile_id: int, name: str) -> Screen:
    lines = [f"Что не так с игроком {name}?",
             "",
             "Жалоба скрывает карточку и отправляет сигнал модератору."]
    rows = [[(label, f"report:reason:{profile_id}:{code}")]
            for code, label in config.REPORT_REASONS.items()]
    rows.append([("🚫 Заблокировать игрока", f"block:confirm:{profile_id}")])
    rows.append([("↩️ Назад", f"deck:card:{profile_id}")])
    return Screen(box("ЖАЛОБА", lines), rows)


def screen_report_done(name: str) -> Screen:
    lines = [f"Игрок {name} скрыт.",
             "",
             "Спасибо — сигнал передан модерации.",
             "Ты больше не увидишь эту карточку."]
    return Screen(box(TITLE, lines), [[("🛰️ Следующая карточка", "deck:next"), ("🏠 В меню", "menu:home")]])


def _age_choice(flt: dict, viewer_age) -> str:
    """Какая возрастная кнопка считается выбранной.

    Порядок строгий: ±5, ±10, весь диапазон, точный custom — иначе «свой диапазон»
    без отметки, чтобы произвольные границы не выдавались за пресет.
    """
    low, high = int(flt["min_age"]), int(flt["max_age"])
    if viewer_age:
        if (low, high) == window_for(viewer_age, 5):
            return "window5"
        if (low, high) == window_for(viewer_age, 10):
            return "window10"
    if (low, high) == (config.MIN_AGE, config.MAX_AGE):
        return "all"
    if (low, high) == (18, 24):
        return "age18_24"
    return "custom"


def screen_filters(flt: dict, available: int, viewer_age: Optional[int] = None) -> Screen:
    """Фильтры с зелёной галочкой на активной настройке каждой группы.

    viewer_age нужен, чтобы отличить пресеты ±5/±10 от произвольного диапазона.
    """
    mode = flt.get("search_mode", "city")
    pref = flt.get("gender_pref", "any")
    geo_mode = flt.get("geo_mode", "any")
    choice = _age_choice(flt, viewer_age)
    low, high = int(flt["min_age"]), int(flt["max_age"])
    geo_label = {"any": "\U0001f30d Любая страна", "country": "\U0001f30d Моя страна",
                 "city": "\U0001f4cd Мой город"}.get(geo_mode, "\U0001f30d Любая страна")
    mode_label = "пол + город" if mode == "city" else "жанр игр + пол"
    pref_label = {"any": "любой", "same": "свой", "other": "противоположный"}.get(pref, "любой")
    custom = " (свой диапазон)" if choice == "custom" else ""
    lines = [
        "Настройки поиска:",
        "",
        f"\U0001f50e Режим: {mode_label}",
        f"\U0001f464 Пол: {pref_label}",
        f"\U0001f382 Возраст: {low}–{high}{custom}",
        f"{geo_label}",
        f"\U0001f5e3\ufe0f Общий язык обязателен: {'да' if flt['require_common_language'] else 'нет'}",
        f"\U0001f3ae Общая игра обязательна: {'да' if flt['require_common_game'] else 'нет'}",
        "",
        "\u2705 — что сейчас включено.",
        f"Свободных анкет по этим фильтрам: {available}",
    ]
    if available < config.SMALL_BASE:
        lines += ["", "Анкет мало — попробуй ослабить необязательные условия."]
    rows = [
        [(mark_selected("\U0001f4cd Пол + город", mode == "city"), "filters:mode:city"),
         (mark_selected("\U0001f3ae Жанр + пол", mode == "genre"), "filters:mode:genre")],
        [(mark_selected("\U0001f464 Любой", pref == "any"), "filters:gender:any"),
         (mark_selected("\U0001f464 Свой", pref == "same"), "filters:gender:same"),
         (mark_selected("\U0001f464 Противоположный", pref == "other"), "filters:gender:other")],
        [(mark_selected("\U0001f382 Возраст ±5", choice == "window5"), "filters:window:5"),
         (mark_selected("\U0001f382 Возраст ±10", choice == "window10"), "filters:window:10"),
         (mark_selected("\U0001f382 18–99", choice == "all"), "filters:window:all")],
        [(mark_selected("\U0001f382 Свой: 18–24", choice == "age18_24"), "filters:age:18:24"),
         ("\U0001f382 Свой: 18–99", "filters:age:18:99")],
        [(mark_selected("\U0001f30d Любая страна", geo_mode == "any"), "filters:geo:any"),
         (mark_selected("\U0001f30d Моя страна", geo_mode == "country"), "filters:geo:country"),
         (mark_selected("\U0001f4cd Мой город", geo_mode == "city"), "filters:geo:city")],
        [(mark_selected("\U0001f5e3\ufe0f Общий язык", bool(flt["require_common_language"])), "filters:lang"),
         (mark_selected("\U0001f3ae Общая игра", bool(flt["require_common_game"])), "filters:game")],
        [("\U0001f3e0 В меню", "menu:home")],
    ]
    return Screen(box("ФИЛЬТРЫ", lines), rows)


def screen_progress(shown: int, likes_out: int, likes_in: int, matches: int,
                    requests_in: int, streak: int, level: str,
                    fill) -> Screen:
    """Активность и разбивка заполнения анкеты.

    fill — FillReport (основной путь) или число (совместимость со старыми вызовами).
    """
    percent = fill.progress if isinstance(fill, FillReport) else int(fill)
    lines = [
        f"\u26a1 Уровень: {level} • серия: {streak} дн.",
        f"\U0001f4c8 Заполнение анкеты: {percent}%",
    ]
    if isinstance(fill, FillReport):
        earned = sum(item.points for item in fill.required if item.complete)
        done = "  ".join(f"\u2705 {item.label}" for item in fill.required if item.complete)
        lines += ["", f"Обязательные поля: {earned}/100"]
        if done:
            lines.append(done)
        for item in fill.missing:
            lines.append(f"\u26a0\ufe0f {item.label} — {item.points} б.: {item.detail}")
        if fill.improvements:
            lines += ["", "Можно улучшить: "
                      + ", ".join(item.label.lower() for item in fill.improvements) + "."]
    lines += [
        "",
        f"\U0001f440 Просмотрено карточек: {shown}",
        f"\u2764\ufe0f Лайков отправлено: {likes_out} • получено: {likes_in}",
        f"\U0001f91d Взаимных мэтчей: {matches}",
        f"\U0001f4e5 Заявок получено: {requests_in}",
        "",
        "Достижения открываются за осмысленные действия,",
        "а не за массовые лайки.",
    ]
    return Screen(box("МОЙ ПРОГРЕСС", lines), [[("\U0001f3e0 В меню", "menu:home")]])



def screen_need_profile() -> Screen:
    lines = ["Сначала нужна анкета.",
             "",
             "Соберём сигнал за минуту — без него нечего показывать другим игрокам."]
    return Screen(box(TITLE, lines), [[("🎮 Создать сигнал", "onb:start")]])


def screen_error(message: str) -> Screen:
    return Screen(box(TITLE, ["⚠️ " + message]), [[("🏠 В меню", "menu:home")]])
