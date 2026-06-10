"""Telegram message and callback handlers.

Mirrors the n8n Classify Message → Switch → Send flow as a single Python module.
"""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .classifier import (
    CANCEL_RE,
    DEPART_RE,
    ETA_QUERY_RE,
    PRIORITY_RE,
    Stop,
    extract_note,
    find_postcodes,
    is_short_message,
    looks_like_list,
)
from .config import Config
from .routes import build_route_text
from .storage import Storage

log = logging.getLogger(__name__)

START_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🚀 Старт доставки / Start delivery", callback_data="start_route")]]
)


# --- Commands (DM) ---

async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Привет! Я бот расчёта маршрута доставки Floritta.\n\n"
        f"Я работаю в группе chat_id {Config.CHAT_ID}.\n"
        "Менеджер отправляет туда список посткодов — я сохраняю.\n"
        "Курьер нажимает «🚀 Старт» под подтверждением и получает маршрут с ETA.\n\n"
        "Команды в группе:\n"
        "• список посткодов (≥2 или 1 с переводом строки) — сохранить\n"
        "• «<посткод> приоритет» — поднять приоритет, пересчитать маршрут\n"
        "• «<посткод> отменили» — удалить из маршрута\n"
        "• «Выехал» — построить маршрут (альтернатива кнопке)"
    )


# --- Group messages ---

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if msg is None or msg.chat is None:
        return
    if msg.chat.id != Config.CHAT_ID:
        return

    text = (msg.text or msg.caption or "").strip()
    if not text:
        return

    storage: Storage = ctx.bot_data["storage"]
    chat_id = msg.chat.id
    user = msg.from_user
    user_id = user.id if user else 0
    username = user.username if user else None
    is_manager = Config.is_manager(user_id, username)

    found = find_postcodes(text)
    current_raw = storage.get_list(chat_id)
    current_stops: list[Stop] = (
        [Stop.from_dict(s) for s in current_raw] if current_raw else []
    )

    # 1) Cancel — only manager
    if found and CANCEL_RE.search(text):
        if not is_manager:
            return
        if not current_stops:
            await msg.reply_text("⚠️ Список пуст, нечего удалять.", disable_notification=True)
            return
        rem = {f.code for f in found}
        new_stops = [s for s in current_stops if s.code not in rem]
        if not new_stops:
            storage.delete_list(chat_id)
        else:
            storage.update_list(chat_id, [s.to_dict() for s in new_stops])
        await msg.reply_text(
            f"🗑 Удалено из маршрута: {', '.join(sorted(rem))}",
            disable_notification=True,
        )
        return

    # 2) ETA query (1 postcode + слово ETA) → skip
    if len(found) == 1 and ETA_QUERY_RE.search(text):
        return

    # 2a) Priority update — short msg OR reply to bot, all codes already in list
    is_reply_to_bot = (
        msg.reply_to_message is not None
        and msg.reply_to_message.from_user is not None
        and msg.reply_to_message.from_user.is_bot
    )
    if (
        found
        and PRIORITY_RE.search(text)
        and (is_reply_to_bot or is_short_message(text))
        and current_stops
    ):
        existing_codes = {s.code for s in current_stops}
        msg_codes = {f.code for f in found}
        if msg_codes.issubset(existing_codes):
            if not is_manager:
                return
            updated = [
                Stop(
                    code=s.code,
                    priority=(s.priority or s.code in msg_codes),
                    note=s.note,
                )
                for s in current_stops
            ]
            storage.update_list(chat_id, [s.to_dict() for s in updated])
            route_text = await build_route_text(
                Config.GOOGLE_API_KEY,
                Config.SHOP_ADDRESS,
                updated,
                Config.PARKING_MIN,
            )
            await msg.reply_text(route_text)
            return

    # 3) New list — only manager
    if looks_like_list(found, text):
        if not is_manager:
            return
        new_stops = [
            Stop(
                code=f.code,
                priority=bool(PRIORITY_RE.search(f.line)),
                note=extract_note(f.line),
            )
            for f in found
        ]
        storage.save_list(chat_id, [s.to_dict() for s in new_stops], user_id)
        await msg.reply_text(
            f"📋 Принято {len(new_stops)} посткод(ов).\n"
            "Курьер — нажми кнопку «🚀 Старт» когда выезжаешь, "
            "и получишь маршрут с ETA.",
            reply_markup=START_KEYBOARD,
            disable_notification=True,
        )
        return

    # 4) Legacy text trigger «Выехал» — anyone
    if DEPART_RE.search(text):
        if not current_stops:
            await msg.reply_text(
                "⚠️ Список посткодов ещё не получен.\n"
                "Сначала отправь список, дождись подтверждения, "
                "затем нажми кнопку «🚀 Старт».",
                disable_notification=True,
            )
            return
        route_text = await build_route_text(
            Config.GOOGLE_API_KEY,
            Config.SHOP_ADDRESS,
            current_stops,
            Config.PARKING_MIN,
        )
        await msg.reply_text(route_text)
        return

    # otherwise — silent skip (e.g. regular chat)


# --- Inline button ---

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cbq = update.callback_query
    if cbq is None or cbq.data != "start_route":
        return
    await cbq.answer()  # remove spinner immediately

    if cbq.message is None or cbq.message.chat is None:
        return
    chat_id = cbq.message.chat.id
    if chat_id != Config.CHAT_ID:
        return

    storage: Storage = ctx.bot_data["storage"]
    current_raw = storage.get_list(chat_id)
    if not current_raw:
        await cbq.message.reply_text(
            "⚠️ Список посткодов ещё не получен.\n"
            "Сначала отправь список с посткодами в этот чат."
        )
        return
    stops = [Stop.from_dict(s) for s in current_raw]
    route_text = await build_route_text(
        Config.GOOGLE_API_KEY,
        Config.SHOP_ADDRESS,
        stops,
        Config.PARKING_MIN,
    )
    await cbq.message.reply_text(route_text)


# --- Wiring ---

def register_handlers(app, storage: Storage) -> None:
    app.bot_data["storage"] = storage
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            handle_message,
        )
    )
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^start_route$"))
