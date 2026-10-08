"""Progress memory (SQLite).  This is what lets a run resume after Ctrl+C or a crash.

Two things are remembered:
  * the finished result for each source (website / Facebook page / Asset Map page), and
  * every model answer already received (so a half-finished website does not repeat its model calls).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional


class State:
    def __init__(self, path: Path, fresh: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if fresh and self.path.exists():
            self.path.unlink()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS sources (key TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT, updated REAL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, payload TEXT)")
        self._db.commit()

    def get_source(self, key: str, fingerprint: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT fingerprint, payload FROM sources WHERE key=?", (key,)).fetchone()
        if not row or row[0] != fingerprint:
            return None
        try:
            return json.loads(row[1])
        except json.JSONDecodeError:
            return None

    def put_source(self, key: str, fingerprint: str, payload: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?)",
                             (key, fingerprint, json.dumps(payload, ensure_ascii=False), time.time()))
            self._db.commit()

    def llm_get(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT payload FROM llm_cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def llm_put(self, key: str, payload: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?)", (key, json.dumps(payload, ensure_ascii=False)))
            self._db.commit()

    def count_sources(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            try:
                self._db.commit()
                self._db.close()
            except sqlite3.Error:
                pass
