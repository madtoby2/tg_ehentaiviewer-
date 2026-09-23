"""Durable, user-private reading history for HentaiViewer."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class ReaderHistoryStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS reader_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT NOT NULL,
                title TEXT NOT NULL,
                reader_url TEXT NOT NULL,
                page_count INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                saved INTEGER NOT NULL DEFAULT 0
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reader_history_user_time "
                         "ON reader_history(tg_user_id, history_id DESC)")

    def record(self, tg_user_id: int, source: str, source_url: str, title: str,
               reader_url: str, page_count: int) -> int:
        with self._connect() as conn:
            cur = conn.execute("""INSERT INTO reader_history
                (tg_user_id,source,source_url,title,reader_url,page_count,created_at)
                VALUES(?,?,?,?,?,?,?)""", (int(tg_user_id), str(source), str(source_url),
                  str(title), str(reader_url), max(0, int(page_count)), int(time.time())))
            # Retain the most recent 200 records for this user only.
            conn.execute("""DELETE FROM reader_history WHERE tg_user_id=? AND history_id NOT IN
                (SELECT history_id FROM reader_history WHERE tg_user_id=?
                 ORDER BY history_id DESC LIMIT 200)""", (int(tg_user_id), int(tg_user_id)))
            return int(cur.lastrowid)

    def list_for(self, tg_user_id: int, limit: int = 20) -> list[dict]:
        limit = min(max(int(limit), 1), 50)
        with self._connect() as conn:
            rows = conn.execute("""SELECT history_id,source,title,reader_url,page_count,created_at,saved
                FROM reader_history WHERE tg_user_id=? ORDER BY history_id DESC LIMIT ?""",
                (int(tg_user_id), limit)).fetchall()
        return [dict(x) for x in rows]

    def remove(self, tg_user_id: int, history_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM reader_history WHERE history_id=? AND tg_user_id=?",
                               (int(history_id), int(tg_user_id)))
            return cur.rowcount == 1
