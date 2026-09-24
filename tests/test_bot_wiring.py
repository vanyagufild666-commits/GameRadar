"""Проверка сборки бота: все обработчики на месте, клавиатуры собираются корректно.

Сети не требует: Application создаётся с тестовым токеном, polling не запускается.
"""
from __future__ import annotations

import pytest

import bot
from telegram import InlineKeyboardButton
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from ui.screens import Screen


@pytest.fixture(scope="module")
def app():
    return bot.build_application("123456789:TEST-TOKEN-FOR-WIRING-ONLY")


def test_all_handlers_registered(app):
    kinds = [type(handler) for group in app.handlers.values() for handler in group]
    assert kinds.count(CommandHandler) == 6, "start/menu/help/cancel/stats/reports"
    assert kinds.count(CallbackQueryHandler) == 1, "все кнопки идут через один роутер"
    assert kinds.count(MessageHandler) == 2, "медиа и текст"


def test_single_callback_router_handles_every_namespace(app):
    router = next(h for group in app.handlers.values() for h in group
                  if isinstance(h, CallbackQueryHandler))
    for data in ("menu:home", "deck:next", "deck:like:3", "profile:pause", "request:go:3:text",
                 "inbox:open:1", "match:share:1", "report:reason:3:spam", "filters:geo:city",
                 "ref:share", "onb:start"):
        # роутер один и ловит любые callback_data — проверяем, что он не фильтрует по шаблону
        assert router.pattern is None or router.pattern.match(data) is not None


def test_keyboard_builds_url_and_callback_buttons():
    kb = bot.build_keyboard([[("❤️ В пати", "deck:like:7"), ("Контакт", "https://t.me/friend")]])
    assert kb is not None
    row = kb.inline_keyboard[0]
    assert row[0].callback_data == "deck:like:7"
    assert row[1].url == "https://t.me/friend"
    assert bot.build_keyboard([]) is None


def test_keyboard_truncates_to_telegram_limit():
    long_action = "deck:" + "x" * 100
    kb = bot.build_keyboard([[("Кнопка", long_action)]])
    assert len(kb.inline_keyboard[0][0].callback_data.encode()) == 64


def test_deliver_and_effects_are_async_callables():
    import inspect
    assert inspect.iscoroutinefunction(bot.deliver)
    assert inspect.iscoroutinefunction(bot.run_effects)
    assert inspect.iscoroutinefunction(bot.on_callback)
    assert inspect.iscoroutinefunction(bot.on_text)
    assert inspect.iscoroutinefunction(bot.on_media)
    assert inspect.iscoroutinefunction(bot.job_maintenance)


def test_job_maintenance_registered_on_post_init(app):
    assert bot.post_init is not None
    assert app.job_queue is not None, "для авто-паузы неактивных анкет нужен JobQueue"


def test_store_singleton_uses_configured_path(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "wiring.db"))
    bot.STORE = None
    try:
        assert bot.store().q1("SELECT 1 AS ok")["ok"] == 1
    finally:
        if bot.STORE:
            bot.STORE.close()
        bot.STORE = None
