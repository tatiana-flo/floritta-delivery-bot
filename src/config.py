"""Centralized config loading from environment variables.

Все секреты только через env. На локали — через .env (см. .env.example).
На Railway — через Project Variables.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _csv_str(name: str) -> set[str]:
    raw = os.environ.get(name, "")
    return {
        item.strip().lstrip("@").lower()
        for item in raw.split(",")
        if item.strip()
    }


def _csv_int(name: str) -> set[int]:
    raw = os.environ.get(name, "")
    out: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.add(int(item))
        except ValueError:
            pass
    return out


class Config:
    BOT_TOKEN: str = _required("TELEGRAM_BOT_TOKEN_DELIVERY_ETA")
    GOOGLE_API_KEY: str = _required("GOOGLE_API_KEY_DELIVERY_ETA")
    CHAT_ID: int = int(_required("CHAT_ID_DELIVERY_ETA"))
    SHOP_ADDRESS: str = _required("SHOP_ADDRESS_DELIVERY_ETA")

    MANAGER_USERNAMES: set[str] = _csv_str("MANAGER_USERNAMES_DELIVERY_ETA")
    MANAGER_IDS: set[int] = _csv_int("MANAGER_IDS_DELIVERY_ETA")

    PARKING_MIN: int = int(os.environ.get("PARKING_MIN_DELIVERY_ETA", "7"))
    DATABASE_PATH: str = os.environ.get("DATABASE_PATH_DELIVERY_ETA", "./bot.db")
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    @classmethod
    def is_whitelist_enabled(cls) -> bool:
        return bool(cls.MANAGER_USERNAMES or cls.MANAGER_IDS)

    @classmethod
    def is_manager(cls, user_id: int, username: str | None) -> bool:
        """True if whitelist not configured OR user matches."""
        if not cls.is_whitelist_enabled():
            return True
        if user_id in cls.MANAGER_IDS:
            return True
        if username and username.lower() in cls.MANAGER_USERNAMES:
            return True
        return False
