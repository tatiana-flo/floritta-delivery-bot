"""Floritta Delivery ETA Bot — Python entry point.

Runs as a long-poll worker on Railway (or any Linux/macOS box with `python -m src.bot`).
"""
import logging

from telegram.ext import Application

from .config import Config
from .handlers import register_handlers
from .storage import Storage


def setup_logging() -> None:
    level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        level=level,
    )
    # Silence very chatty libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    setup_logging()
    log = logging.getLogger(__name__)

    log.info("Starting Floritta Delivery ETA Bot")
    log.info("Chat ID: %s", Config.CHAT_ID)
    log.info("Shop address: %s", Config.SHOP_ADDRESS)
    log.info("Whitelist enabled: %s", Config.is_whitelist_enabled())
    log.info("Database: %s", Config.DATABASE_PATH)

    storage = Storage(Config.DATABASE_PATH)
    app = Application.builder().token(Config.BOT_TOKEN).build()
    register_handlers(app, storage)

    log.info("Bot running (polling for message + callback_query)")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
