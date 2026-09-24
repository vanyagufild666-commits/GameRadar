"""Приёмка «незавершённой анкеты» — мои собственные тесты по описанию задачи.

Проверяю наблюдаемое поведение и сохранность данных, а не детали SQL:
прогресс не теряется при отмене/выходе/рестарте, возврат попадает на свой шаг,
явный «Начать заново» — единственное, что стирает черновик.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import types
from datetime import datetime, timedelta

import pytest

import bot
from db.store import Store
from handlers import callbacks as cb
from handlers import registration as reg


def register(store: Store, tg_id: int, username: str, age: int = 25, city: str = "Омск",
             games: str = "Valorant, Dota 2") -> dict:
    """Полный визард целиком, как это делает человек в чате (локальная копия хелпера)."""
    user = store.ensure_user(tg_id, username, username.capitalize())
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    reg.on_text(store, user["id"], str(age))
    reg.on_button(store, user["id"], "country", "RU")
    reg.on_text(store, user["id"], city)
    reg.on_button(store, user["id"], "lang", "ru")
    reg.on_button(store, user["id"], "lang_done")
    reg.on_text(store, user["id"], games)
    reg.on_text(store, user["id"], "Ищу спокойную пати, играю вечером, без токсичности")
    reg.on_button(store, user["id"], "photo_skip")
    screen, published = reg.publish(store, user["id"])
    assert published, "анкета публикуется после полного визарда"
    return user


class _FakeChat:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, text, **kw):
        self.sent.append(text)


class _FakeMessage:
    def __init__(self):
        self.photo = None
        self.chat = _FakeChat()

    async def delete(self):
        pass


class _FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.answered: list = []
        self.edited: str | None = None

    async def answer(self, text=None, **kw):
        self.answered.append(text)

    async def edit_message_text(self, text, **kw):
        self.edited = text

    async def edit_message_media(self, **kw):
        pass


class _FakeUpdate:
    def __init__(self, tg_id: int, data: str):
        self.callback_query = _FakeQuery(data)
        self.effective_user = types.SimpleNamespace(id=tg_id, username=f"u{tg_id}",
                                                    first_name=f"U{tg_id}")
        self.effective_chat = types.SimpleNamespace(id=tg_id)
        self.message = types.SimpleNamespace(reply_text=self._noop)

    async def _noop(self, *a, **kw):
        pass


class _FakeContext:
    def __init__(self):
        self.application = types.SimpleNamespace(send_message=self._noop, bot=None)

    async def _noop(self, *a, **kw):
        pass

FMT = "%Y-%m-%d %H:%M:%S"


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


class _AppBot:
    def __init__(self):
        self.texts: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.texts.append(text)

    async def send_photo(self, *a, **kw):
        pass


class _FakeApp:
    def __init__(self):
        self.bot = _AppBot()


class _Msg:
    """Сообщение пользователя: запоминает ответы бота."""

    def __init__(self):
        self.sent: list[str] = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


def _ctx() -> _FakeContext:
    """Контекст, в котором send_screen может достучаться до app.bot."""
    ctx = _FakeContext()
    ctx.application = _FakeApp()
    return ctx


def _filled(user_id: int, store: Store) -> dict:
    """Пройти визард до шага languages: заполнены пол/возраст/страна/город."""
    reg.start(store, user_id)
    reg.on_button(store, user_id, "gender", "m")
    reg.on_text(store, user_id, "29")
    reg.on_button(store, user_id, "country", "RU")
    reg.on_text(store, user_id, "Омск")
    return store.load_draft_session(user_id)


# ── прогресс живёт: четыре прежних «стирающих» пути ──────────────────────────
def test_cancel_button_preserves_draft_and_step(store: Store):
    user = store.ensure_user(15001, "btn", "Btn")
    session = _filled(user["id"], store)
    assert session and session.state == "languages"

    bot.STORE = store
    query = _FakeQuery("onb:cancel")
    update = _FakeUpdate(15001, "onb:cancel")
    update.callback_query = query
    asyncio.run(bot.on_callback(update, _FakeContext()))

    kept = store.load_draft_session(user["id"])
    assert kept is not None, "кнопка «Отмена» не должна стирать черновик"
    assert kept.state == "languages"
    assert kept.draft["gender"] == "m" and kept.draft["age"] == 29 and kept.draft["city"] == "Омск"
    assert "сохранён" in query.edited.lower() or "сохранён" in str(query.edited or "")


def test_cmd_cancel_preserves_draft(store: Store):
    user = store.ensure_user(15002, "cmd", "Cmd")
    _filled(user["id"], store)
    bot.STORE = store
    asyncio.run(bot.cmd_cancel(_FakeUpdate(15002, "x"), _ctx()))
    assert store.load_draft_session(user["id"]) is not None, "/cancel не стирает прогресс"


def test_cmd_menu_preserves_draft(store: Store):
    user = store.ensure_user(15003, "mn", "Mn")
    _filled(user["id"], store)
    bot.STORE = store
    asyncio.run(bot.cmd_menu(_FakeUpdate(15003, "x"), _ctx()))
    assert store.load_draft_session(user["id"]) is not None, "/menu не стирает прогресс"


def test_restart_start_command_does_not_overwrite_existing_draft(store: Store):
    user = store.ensure_user(15004, "rstrt", "Rstrt")
    _filled(user["id"], store)
    screen = reg.start(store, user["id"])
    actions = [a for row in screen.rows for _, a in row]
    assert "onb:resume" in actions and "onb:restart" in actions, "экран выбора: продолжить и заново"
    assert store.load_draft_session(user["id"]).state == "languages", "черновик цел"


# ── возврат и экраны ─────────────────────────────────────────────────────────
def test_resume_lands_on_saved_step(store: Store):
    user = store.ensure_user(15005, "res", "Res")
    _filled(user["id"], store)
    screen = reg.step_screen(store, user["id"])
    assert "Языки" in screen.text, "продолжение идёт с того же шага"
    screen = reg.on_button(store, user["id"], "resume")
    assert "Языки" in screen.text


def test_explicit_restart_requires_confirmation_and_keeps_draft_until_confirmed(store: Store):
    user = store.ensure_user(15006, "rr", "Rr")
    _filled(user["id"], store)

    confirm = reg.on_button(store, user["id"], "restart")
    assert "заново" in confirm.text.lower(), "спрашивают подтверждение сброса"
    kept = reg.on_button(store, user["id"], "restart_keep")
    actions = [a for row in kept.rows for _, a in row]
    assert "onb:resume" in actions, "отказ от сброса ведёт обратно на экран продолжения"
    assert store.load_draft_session(user["id"]).state == "languages", "отказ от сброса не стирает"

    reg.on_button(store, user["id"], "restart_confirm")
    fresh = store.load_draft_session(user["id"])
    assert fresh.state == "gender" and fresh.draft == {"languages": [], "games": []}, \
        "подтверждённый сброс очищает черновик"


def test_draft_survives_process_restart(store: Store):
    user = store.ensure_user(15007, "proc", "Proc")
    _filled(user["id"], store)
    store.close()

    again = Store(store.path)
    try:
        session = again.load_draft_session(user["id"])
        assert session is not None and session.state == "languages"
        screen = reg.step_screen(again, user["id"])
        assert "Языки" in screen.text, "после рестарта процесса возврат на свой шаг"
    finally:
        again.close()


def test_consent_survives_exit(store: Store):
    user = store.ensure_user(15008, "cons", "Cons")
    reg.give_consent(store, user["id"])
    _filled(user["id"], store)
    reg.exit_wizard(store, user["id"])
    assert reg.has_consent(store, user["id"]), "выход не отменяет согласие 18+"


# ── срок годности и совместимость ────────────────────────────────────────────
def test_draft_expires_after_ttl(store: Store):
    user = store.ensure_user(15009, "ttl", "Ttl")
    _filled(user["id"], store)
    old = (datetime.now() - timedelta(days=config_TTL() + 1)).strftime(FMT)
    store.x("UPDATE registration_sessions SET updated_at = ? WHERE user_id = ?", (old, user["id"]))
    assert store.load_draft_session(user["id"]) is None, "истёкший черновик не предлагается"
    assert store.q1("SELECT 1 ok FROM registration_sessions WHERE user_id = ?", (user["id"],)) is None, \
        "истёкший черновик удаляется"


def test_draft_is_resumable_before_ttl(store: Store):
    user = store.ensure_user(15010, "ttl2", "Ttl2")
    _filled(user["id"], store)
    almost = (datetime.now() - timedelta(days=config_TTL() - 1)).strftime(FMT)
    store.x("UPDATE registration_sessions SET updated_at = ? WHERE user_id = ?", (almost, user["id"]))
    assert store.load_draft_session(user["id"]) is not None


def config_TTL():
    import config
    return config.DRAFT_TTL_DAYS


def test_legacy_search_without_bio_resumes_at_search_keeping_fields(store: Store):
    user = store.ensure_user(15011, "unk", "Unk")
    reg.start(store, user["id"])
    store.save_draft(user["id"], "search", {
        "gender": "m", "age": 25, "country": "RU", "city": "Омск",
        "languages": ["ru"], "games": ["Dota 2"],
    })                                        # старый черновик дошёл до поиска, bio нет
    screen = reg.step_screen(store, user["id"])
    assert "Как искать напарников" in screen.text, "возврат на шаг поиска, а не в начало"
    reg.on_button(store, user["id"], "search_mode", "city")
    reg.on_button(store, user["id"], "search_done")
    assert reg.on_text(store, user["id"], "Играю вечерами, зову в пати") is not None


def test_legacy_search_with_bio_resumes_at_photo(store: Store):
    user = store.ensure_user(15012, "leg", "Leg")
    reg.start(store, user["id"])
    store.save_draft(user["id"], "search", {
        "gender": "m", "age": 25, "country": "RU", "city": None,
        "languages": ["ru"], "games": ["Dota 2"], "bio": "Играю вечерами, зову в пати",
    })                                        # старый визард: bio сразу, search не подтверждён
    screen = reg.step_screen(store, user["id"])
    assert "Картинка" in screen.text, "legacy-черновик с bio продолжается с фото"


# ── ошибки и публикация ──────────────────────────────────────────────────────
def test_validation_error_keeps_fields_and_step(store: Store):
    user = store.ensure_user(15013, "err", "Err")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    screen = reg.on_text(store, user["id"], "10")
    assert "18+" in screen.text
    session = store.load_draft_session(user["id"])
    assert session.state == "age" and session.draft["gender"] == "m"
    assert reg.on_text(store, user["id"], "27") is not None
    assert store.load_draft_session(user["id"]).state == "country", "валидный ответ идёт дальше"


def test_publish_with_missing_fields_points_to_first_missing_step(store: Store):
    user = store.ensure_user(15014, "half", "Half")
    reg.start(store, user["id"])
    reg.on_button(store, user["id"], "gender", "m")
    screen, ok = reg.publish(store, user["id"])
    assert not ok and "не полностью" in screen.text and "Возраст" in screen.text
    assert store.load_draft_session(user["id"]) is not None, "неудачная публикация не стирает прогресс"


def test_publish_completes_and_clears_draft(store: Store):
    user = store.ensure_user(15015, "done", "Done")
    user = register(store, 15015, "done")     # полный визард
    assert store.profile_by_user(user["id"])["status"] == "active"
    assert store.load_draft_session(user["id"]) is None, "успешная публикация очищает черновик"


# ── приватность и чужие потоки ───────────────────────────────────────────────
def test_unpublished_draft_creates_no_profile(store: Store):
    user = store.ensure_user(15016, "priv", "Priv")
    _filled(user["id"], store)
    assert store.profile_by_user(user["id"]) is None, "черновик не попадает в анкеты"
    assert store.count_available(user["id"]) == 0


def test_registration_does_not_clobber_an_active_request_flow(store: Store):
    user = store.ensure_user(15017, "req", "Req")
    store.save_draft(user["id"], "request_text_pending", {"kind": "text", "target": 5})

    reg.start(store, user["id"])               # вход в визард
    state, _, _ = store.load_draft(user["id"])
    assert state == "request_text_pending", "визард не затирает отправляемую заявку"
    assert store.load_draft_session(user["id"]) is None, "заявка не считается черновиком анкеты"


# ── сообщение о раннем этапе (просьба не уходить, пока база маленькая) ───────
def test_welcome_screen_tells_about_early_stage(store: Store):
    import config
    from ui import screens as sc
    text = sc.screen_welcome().text
    assert "раннем этапе" in text and "реклама" in text
    for line in config.EARLY_STAGE_NOTE:
        assert line in text, "все строки сообщения попадают на экран"


def test_start_shows_early_stage_note_to_new_and_returning_users(store: Store):
    import config
    bot.STORE = store

    # новый пользователь: /start → дисклеймер → приветствие с сообщением
    ctx = _ctx()
    update = _FakeUpdate(15021, "x")
    update.message = _Msg()
    asyncio.run(bot.cmd_start(update, ctx))
    assert any("18+" in t for t in ctx.application.bot.texts), "сначала дисклеймер"
    reg.give_consent(store, store.user_by_tg(15021)["id"])
    ctx = _ctx()
    update = _FakeUpdate(15021, "x")
    update.message = _Msg()
    asyncio.run(bot.cmd_start(update, ctx))
    welcome = "\n".join(ctx.application.bot.texts)
    assert "раннем этапе" in welcome and "реклама" in welcome

    # вернувшийся пользователь с анкетой: сообщение тоже приходит
    user = register(store, 15022, "early")
    ctx = _ctx()
    update = _FakeUpdate(15022, "x")
    msg = _Msg()
    update.message = msg
    asyncio.run(bot.cmd_start(update, ctx))
    assert any("раннем этапе" in t for t in msg.sent), "вернувшемуся тоже напоминаем"
