"""SQLite persistence for saved postcode lists per chat."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Storage:
    def __init__(self, path: str):
        self.path = path
        # Ensure parent dir exists (for /data on Railway with mounted volume)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path)
        try:
            yield c
        finally:
            c.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS lists (
                    chat_id INTEGER PRIMARY KEY,
                    stops_json TEXT NOT NULL,
                    saved_at INTEGER NOT NULL,
                    saved_by INTEGER
                )
                """
            )
            c.commit()

    def save_list(self, chat_id: int, stops: list[dict], saved_by: int) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO lists (chat_id, stops_json, saved_at, saved_by) VALUES (?, ?, ?, ?)",
                (chat_id, json.dumps(stops, ensure_ascii=False), int(time.time()), saved_by),
            )
            c.commit()

    def get_list(self, chat_id: int) -> list[dict] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT stops_json FROM lists WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def update_list(self, chat_id: int, stops: list[dict]) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE lists SET stops_json = ?, saved_at = ? WHERE chat_id = ?",
                (json.dumps(stops, ensure_ascii=False), int(time.time()), chat_id),
            )
            c.commit()

    def delete_list(self, chat_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM lists WHERE chat_id = ?", (chat_id,))
            c.commit()
