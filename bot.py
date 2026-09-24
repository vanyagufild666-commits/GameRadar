"""PartyRadar — точка входа бота.

Здесь только «клей»: Telegram ↔ чистые обработчики.
Вся логика в domain/, db/, handlers/, ui/ — её проверяют тесты без сети.
"""
from __future__ import annotations

import logging
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config
from db.store import Store
from handlers import callbacks as cb
from handlers import registration as reg
from ui.screens import Screen, is_url, screen_consent, screen_welcome

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("partyradar")
CAPTION_LIMIT = 1024

STORE: Store | None = None


def store() -> Store:
    global STORE
    if STORE is None:
        STORE = Store(config.DB_PATH)
    return STORE


def build_keyboard(rows: list[list[tuple[str, str]]] | None) -> InlineKeyboardMarkup | None:
    """Собирает клавиатуру: URL-действия становятся URL-кнопками, остальное — callback."""
    if not rows:
        return None
    keyboard = []
    for row in rows:
        line = []
        for label, action in row:
            if is_url(action):
                line.append(InlineKeyboardButton(label, url=action))
            else:
                line.append(InlineKeyboardButton(label, callback_data=action[:64]))
        if line:
            keyboard.append(line)
    return InlineKeyboardMarkup(keyboard) if keyboard else None


# ── доставка экранов ─────────────────────────────────────────────────────────
async def _ack(query, alert: str | None = None) -> None:
    """Отвечает на нажатие ровно один раз: Telegram не даёт ответить дважды."""
    try:
        if alert:
            await query.answer(alert[:200], show_alert=False)
        else:
            await query.answer()
    except TelegramError:
        pass


async def deliver(query, screen: Screen) -> None:
    """Показывает экран: редактирует текущее сообщение, если это возможно."""
    text = (screen.text or "")[:4096]
    # у подписи к фото/видео лимит строже текстового: 1024 против 4096
    caption = (screen.text or "")[:CAPTION_LIMIT]
    kb = build_keyboard(screen.rows)
    msg = query.message
    try:
        if screen.photo:
            if msg.photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(screen.photo, caption=caption), reply_markup=kb)
            else:
                await msg.delete()
                await msg.chat.send_photo(screen.photo, caption=caption, reply_markup=kb)
        elif msg.photo:
            await msg.delete()
            await msg.chat.send_message(text, reply_markup=kb, disable_web_page_preview=True)
        else:
            await query.edit_message_text(text, reply_markup=kb, disable_web_page_preview=True)
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        log.warning("edit failed (%s), sending new message", exc)
        try:
            if screen.photo:
                await msg.chat.send_photo(screen.photo, caption=caption, reply_markup=kb)
            else:
                await msg.chat.send_message(text, reply_markup=kb, disable_web_page_preview=True)
        except TelegramError as inner:
            log.error("delivery failed: %s", inner)


async def send_screen(app: Application, chat_id: int, screen: Screen) -> None:
    kb = build_keyboard(screen.rows)
    try:
        if screen.photo:
            await app.bot.send_photo(chat_id, screen.photo, caption=(screen.text or "")[:1024], reply_markup=kb)
        else:
            await app.bot.send_message(chat_id, (screen.text or "")[:4096], reply_markup=kb,
                                       disable_web_page_preview=True)
    except TelegramError as exc:
        log.warning("send_screen failed for %s: %s", chat_id, exc)


def _tg_id(user_id: int) -> int | None:
    """Внутренний id → telegram id (для доставки чужих уведомлений)."""
    user = store().user_by_id(user_id)
    return int(user["telegram_user_id"]) if user else None


async def run_effects(app: Application, effects: list[dict]) -> None:
    """Отправляет уведомления другим людям: лайки, мэтчи, заявки."""
    for effect in effects or []:
        tg_id = _tg_id(int(effect.get("user_id") or 0))
        if not tg_id:
            continue
        kb = build_keyboard(effect.get("rows"))
        try:
            if effect["type"] == "notify_media" and effect.get("file_id"):
                kind = effect.get("kind")
                senders = {
                    "photo": app.bot.send_photo,
                    "video": app.bot.send_video,
                    "video_note": app.bot.send_video_note,
                }
                sender = senders.get(kind)
                if sender is None:
                    await app.bot.send_message(tg_id, effect.get("caption") or "", reply_markup=kb)
                elif kind == "video_note":
                    await sender(tg_id, effect["file_id"])
                    if kb:
                        await app.bot.send_message(tg_id, effect.get("caption") or "☄️ Заявка в пати",
                                                   reply_markup=kb)
                else:
                    await sender(tg_id, effect["file_id"], caption=(effect.get("caption") or "")[:1024],
                                 reply_markup=kb)
            else:
                await app.bot.send_message(tg_id, effect.get("text") or "", reply_markup=kb,
                                           disable_web_page_preview=True)
        except Forbidden:
            store().set_bot_blocked(effect["user_id"], True)
            log.info("user %s blocked the bot", tg_id)
        except TelegramError as exc:
            log.warning("effect failed for %s: %s", tg_id, exc)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE, result: cb.Result) -> None:
    query = update.callback_query
    if query:
        await deliver(query, result.screen)
        await _ack(query, result.alert)
    await run_effects(context.application, result.effects)


# ── команды ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    payload = context.args[0] if getattr(context, "args", None) else None
    user = store().ensure_user(tg_user.id, tg_user.username, tg_user.first_name, source=payload)
    store().touch_activity(user["id"])
    if payload and payload.startswith("ref_") and payload[4:].isdigit():
        store().record_referral(int(payload[4:]), tg_user.id, source=payload)
    if store().is_registered(user["id"]):
        await update.message.reply_text(
            "С возвращением в радар.\n\n" + "\n".join(config.EARLY_STAGE_NOTE))
        result = cb.route(store(), user["id"], "menu:home")
        await send_screen(context.application, update.effective_chat.id, result.screen)
        return
    if not reg.has_consent(store(), user["id"]):
        await send_screen(context.application, update.effective_chat.id, screen_consent())
        return
    if store().load_draft_session(user["id"]):
        # Начатая анкета не должна начинаться заново
        await send_screen(context.application, update.effective_chat.id,
                          reg.resume_screen(store(), user["id"]))
        return
    await send_screen(context.application, update.effective_chat.id,
                      screen_welcome(has_invite=bool(payload)))


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _user(update)
    # Черновик регистрации не трогаем: меню — не отказ от анкеты
    await send_screen(context.application, update.effective_chat.id,
                      cb.route(store(), user["id"], "menu:home").screen)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _user(update)
    # Выход без потери прогресса: черновик остаётся, вернуться можно в любой момент
    await send_screen(context.application, update.effective_chat.id,
                      reg.exit_wizard(store(), user["id"]))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ui.screens import screen_about
    await send_screen(context.application, update.effective_chat.id, screen_about())


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _user(update)
    if user["telegram_user_id"] not in config.ADMIN_IDS:
        return
    s = store().stats()
    text = "\n".join(f"{key}: {value}" for key, value in s.items())
    await update.message.reply_text(f"📊 Статистика\n\n{text}")


async def cmd_reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _user(update)
    if user["telegram_user_id"] not in config.ADMIN_IDS:
        return
    reports = store().new_reports()
    if not reports:
        await update.message.reply_text("Жалоб нет.")
        return
    lines = [f"#{r['id']} {r['reason']} — {r['reporter_user_id']} → {r['target_user_id']}"
             for r in reports]
    for r in reports:
        lines.append("")
        lines.append(f"#{r['id']}: действия")
    rows = [[(f"#{r['id']} приостановить анкету", f"mod:suspend:{r['id']}"),
             (f"#{r['id']} бан", f"mod:ban:{r['id']}"),
             (f"#{r['id']} отклонить жалобу", f"mod:reject:{r['id']}")] for r in reports]
    await update.message.reply_text("🚩 Жалобы:\n\n" + "\n".join(lines),
                                    reply_markup=build_keyboard(rows))


def _user(update: Update) -> dict:
    tg_user = update.effective_user
    user = store().ensure_user(tg_user.id, tg_user.username, tg_user.first_name)
    store().touch_activity(user["id"])
    return user


async def on_mod_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = _user(update)
    if user["telegram_user_id"] not in config.ADMIN_IDS:
        await query.answer("Недоступно", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit() or parts[1] not in {"suspend", "ban", "reject"}:
        await _ack(query)
        return
    result = store().moderation_action(int(parts[2]), user["id"], parts[1])
    await query.answer("Готово" if result else "Жалоба уже обработана", show_alert=False)
    await query.edit_message_text("Жалоба обработана.")


# ── кнопки ───────────────────────────────────────────────────────────────────
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = _user(update)
    data = query.data or ""

    try:
        if data.startswith("onb:"):
            action, _, value = data[4:].partition(":")
            if action == "about":
                from ui.screens import screen_about
                await deliver(query, screen_about())
                await _ack(query)
                return
            if action == "consent":
                reg.give_consent(store(), user["id"])
                await deliver(query, screen_welcome())
                await _ack(query)
                return
            if action == "exit":
                await deliver(query, reg.exit_wizard(store(), user["id"]))
                await _ack(query)
                return
            if action == "start":
                if not reg.has_consent(store(), user["id"]):
                    await deliver(query, screen_consent())
                    await _ack(query)
                    return
                await deliver(query, reg.start(store(), user["id"]))
                await _ack(query)
                return
            if action == "cancel":
                # «Отмена» = выход из визарда, прогресс остаётся
                await deliver(query, reg.exit_wizard(store(), user["id"]))
                await _ack(query)
                return
            if action == "publish":
                screen, ok = reg.publish(store(), user["id"])
                await deliver(query, screen)
                await _ack(query, "Анкета опубликована \U0001f680" if ok else None)
                if ok:
                    await run_effects(context.application, cb.referral_effects(store(), user["id"]))
                return
            await deliver(query, reg.on_button(store(), user["id"], action, value))
            await _ack(query)
            return
        if data.startswith("mod:"):
            await on_mod_callback(update, context)
            return
        await show(update, context, cb.route(store(), user["id"], data))
    except Exception:
        log.exception("callback failed: %s", data)
        try:
            await query.answer("Что-то сломалось, попробуй ещё раз", show_alert=True)
        except TelegramError:
            pass


# ── входящие сообщения ───────────────────────────────────────────────────────
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _user(update)
    text = (update.message.text or "").strip()
    state, draft, _ = store().load_draft(user["id"])

    if state.startswith("request_") and state.endswith("_pending"):
        kind = draft.get("kind") or "text"
        if kind == "text":
            result = cb.submit_request(store(), user["id"], "text", text)
            await update.message.chat.send_message(result.screen.text,
                                                  reply_markup=build_keyboard(result.screen.rows))
            await run_effects(context.application, result.effects)
            return

    screen = reg.on_text(store(), user["id"], text)
    if screen is not None:
        await update.message.chat.send_message(screen.text, reply_markup=build_keyboard(screen.rows))
        return

    result = cb.on_edit_text(store(), user["id"], text)
    if result is not None:
        await update.message.chat.send_message(result.screen.text,
                                              reply_markup=build_keyboard(result.screen.rows))
        if result.alert:
            await update.message.reply_text(result.alert)
        return

    hint = "Кнопки ниже — основной способ управления. Начни с «Искать напарника»."
    result = cb.route(store(), user["id"], "menu:home")
    await update.message.chat.send_message(f"{result.screen.text}\n\n{hint}",
                                          reply_markup=build_keyboard(result.screen.rows))


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _user(update)
    message = update.message
    state, draft, _ = store().load_draft(user["id"])

    if message.photo:
        kind, file_id = "photo", message.photo[-1].file_id
    elif message.video_note:
        kind, file_id = "video_note", message.video_note.file_id
    elif message.video:
        kind, file_id = "video", message.video.file_id
    else:
        return

    if kind == "photo":
        screen = reg.on_photo(store(), user["id"], file_id)
        if screen is not None:
            await message.chat.send_photo(file_id, caption=screen.text[:1024],
                                          reply_markup=build_keyboard(screen.rows))
            return
        result = cb.on_edit_photo(store(), user["id"], file_id)
        if result is not None:
            await message.chat.send_photo(file_id, caption=result.screen.text[:1024],
                                          reply_markup=build_keyboard(result.screen.rows))
            return

    if state.startswith("request_") and state.endswith("_pending"):
        expected = draft.get("kind")
        if expected == kind:
            result = cb.submit_request(store(), user["id"], kind,
                                       message.caption, file_id=file_id)
            await message.chat.send_message(result.screen.text,
                                           reply_markup=build_keyboard(result.screen.rows))
            await run_effects(context.application, result.effects)
            return

    await message.chat.send_message(
        "Принял медиа. Чтобы отправить его как заявку, открой карточку и выбери «Написать / медиа».")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await on_media(update, context)


# ── фоновые задачи ───────────────────────────────────────────────────────────
async def job_maintenance(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Авто-пауза, истечение показов и разовые напоминания активным игрокам."""
    s = store()
    paused = s.auto_pause_inactive(config.INACTIVITY_DAYS)
    expired = s.expire_stale_impressions(config.STALE_IMPRESSION_MIN)
    effects = []
    for candidate in s.reminder_candidates():
        if s.record_reminder(candidate["id"]):
            effects.append({"type": "notify", "user_id": candidate["id"],
                            "text": f"🛰️ В радаре {candidate['available']} новых сигналов. Загляни, когда будет удобно."})
    await run_effects(context.application, effects)
    log.info("maintenance: auto-paused %s profiles, expired %s impressions, reminders %s",
             paused, expired, len(effects))


async def post_init(app: Application) -> None:
    # Реальный @username бота: ссылки-приглашения всегда ведут на этого бота,
    # даже если в .env осталось старое/чужое значение BOT_USERNAME.
    username = getattr(app.bot, "username", None)
    if username:
        config.BOT_USERNAME = username
    if app.job_queue:
        app.job_queue.run_repeating(job_maintenance, interval=3600, first=60)


def build_application(token: str) -> Application:
    """Собирает Application со всеми обработчиками (без запуска polling — удобно тестировать)."""
    app = (ApplicationBuilder()
           .token(token)
           .post_init(post_init)
           .build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.VIDEO_NOTE, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    if not config.BOT_TOKEN:
        print("BOT_TOKEN не задан. Заполни .env (см. .env.example) и запусти снова.", file=sys.stderr)
        raise SystemExit(2)
    app = build_application(config.BOT_TOKEN)
    log.info("PartyRadar запускается…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
