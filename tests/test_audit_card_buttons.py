"""Приёмка кнопок карточки: «В пати» должно нажиматься (не молча падать).

Два дефекта, которые ловили: (1) подпись к фото-сообщению уходила в
Telegram необрезанной до 1024 символов (карточка с длинным текстом не
обновлялась вообще), (2) on_callback отвечал на нажатие пустым
query.answer() сразу, поэтому все тосты («Лайк отправлен» и прочие) не
доходили до пользователя и кнопка выглядела мёртвой.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from telegram.error import BadRequest

import bot
from db.store import Store
from handlers import registration as reg
from ui import screens as sc

CAPTION_LIMIT = 1024


def _wizard(st: Store, tg_id: int, bio: str = "Ищу спокойную пати, играю вечером.",
            photo: str | None = None) -> int:
    """Полный визард через кнопки; возвращает users.id."""
    user = st.ensure_user(tg_id, f"u{tg_id}", f"U{tg_id}")
    reg.start(st, user["id"])
    reg.on_button(st, user["id"], "gender", "m")
    reg.on_text(st, user["id"], "24")
    reg.on_button(st, user["id"], "country", "RU")
    reg.on_text(st, user["id"], "Омск")
    reg.on_button(st, user["id"], "lang", "ru")
    reg.on_button(st, user["id"], "lang_done")
    reg.on_text(st, user["id"], "Dota 2, CS2")
    reg.on_button(st, user["id"], "search_done")
    reg.on_text(st, user["id"], bio)
    if photo:
        reg.on_photo(st, user["id"], photo)
    else:
        reg.on_button(st, user["id"], "photo_skip")
    _screen, ok = reg.publish(st, user["id"])
    assert ok, "визард не опубликовался"
    return user["id"]


class _StrictChat:
    """Ведёт себя как Telegram: у подписи к фото лимит 1024 символа."""

    def __init__(self):
        self.sent_photos: list[str] = []
        self.sent_texts: list[str] = []

    async def send_photo(self, file_id, caption=None, **kw):
        if caption and len(caption) > CAPTION_LIMIT:
            raise BadRequest("Message caption is too long")
        self.sent_photos.append(file_id)

    async def send_message(self, text, **kw):
        self.sent_texts.append(text)


class _StrictMessage:
    def __init__(self, chat: _StrictChat, with_photo: bool = True):
        self.chat = chat
        self.photo = [object()] if with_photo else []

    async def delete(self):
        pass


class _StrictQuery:
    """query.answer() можно вызвать ровно один раз — как в Telegram."""

    def __init__(self, data: str, chat: _StrictChat, with_photo: bool = True):
        self.data = data
        self.message = _StrictMessage(chat, with_photo)
        self.chat = chat
        self.answers: list[str | None] = []
        self.medias: list[str] = []  # caption'ы редактируемых медиа
        self.edits: list[str] = []

    async def answer(self, text=None, **kw):
        if self.answers:
            raise BadRequest("query is too old and response timeout expired "
                             "or query ID is invalid")
        self.answers.append(text)

    async def edit_message_media(self, media=None, **_kw):
        caption = getattr(media, "caption", None)
        if caption and len(caption) > CAPTION_LIMIT:
            raise BadRequest("Message caption is too long")
        self.medias.append(caption or "")

    async def edit_message_text(self, text="", **_kw):
        self.edits.append(text)


class _Bot:
    def __init__(self):
        self.sent: list = []

    async def send_message(self, *a, **kw):
        self.sent.append((a, kw))


class _StrictUpdate:
    def __init__(self, tg_id: int, data: str, chat: _StrictChat):
        self.callback_query = _StrictQuery(data, chat)
        self.effective_user = type("U", (), {
            "id": tg_id, "username": f"u{tg_id}", "first_name": f"U{tg_id}"})()
        self.effective_chat = type("C", (), {"id": tg_id})()
        self.message = type("M", (), {"reply_text": self._noop})()

    async def _noop(self, *a, **kw):
        pass


class _Ctx:
    def __init__(self, bot_: _Bot):
        self.application = self
        self.bot = bot_
        self.job_queue = None

    async def send_message(self, *a, **kw):
        pass


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


def _serve_card(st: Store) -> str:
    """Нажимает deck:like на карточку кандидата с фото и длинным текстом."""
    long_bio = ("Ищу пати для вечерних каток, играю каждый день. " * 20).strip()  # ~950 символов
    me = _wizard(st, 51001)
    other = _wizard(st, 51002, bio=long_bio, photo="AgAC_fake_photo_123")
    pid = st.q1("SELECT id FROM profiles WHERE user_id = ?", (other,))["id"]
    screen = sc.screen_card(
        {"profile_id": pid, "display_name": "u51002", "gender": "m", "age": 24,
         "country_code": "RU", "city": "Омск", "bio": long_bio,
         "photo_file_id": "AgAC_fake_photo_123"},
        {"status": "ОНЛАЙН", "languages": ["ru"], "games": ["Dota 2"],
         "matched_games": ["Dota 2"], "matched_languages": ["ru"]},
        viewer_city="Омск", remaining=3)
    assert len(screen.text) > CAPTION_LIMIT, "предусловие: текст карточки длиннее лимита"
    return me, f"deck:like:{pid}"


import asyncio


def _press(update, ctx):
    return asyncio.get_event_loop().run_until_complete(bot.on_callback(update, ctx))


def test_like_button_updates_card_and_shows_alert(store: Store, monkeypatch):
    chat = _StrictChat()
    me, data = _serve_card(store)
    # обработчик нажатий ходит в глобальный Store — подменяем на тестовый
    monkeypatch.setattr(bot, "STORE", store, raising=False)
    app_bot = _Bot()
    update = _StrictUpdate(51001, data, chat)
    ctx = _Ctx(app_bot)
    _press(update, ctx)

    query = update.callback_query
    assert len(query.answers) == 1, "нажатие отвечает ровно один раз"
    assert query.answers[0] and "Лайк отправлен" in query.answers[0], \
        f"тост с результатом доходит, а не глотается: {query.answers!r}"
    assert query.medias, "карточка должна редактироваться как медиа"
    assert len(query.medias[0]) <= CAPTION_LIMIT, "подпись к фото не длиннее 1024"
    assert chat.sent_photos == [], "не должен был падать в повторную отправку"