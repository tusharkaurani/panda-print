"""Tiny SQLite wrapper: one connection per operation, WAL, namespaced migrations.

Tools own their tables. A tool calls `db.migrate("<tool-id>", [sql, ...])` at
start-up; entry N of the list is migration N+1 and runs at most once.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._memory = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        with self.tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS _migrations (ns TEXT PRIMARY KEY, version INTEGER NOT NULL)")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A connection inside a transaction; commits on success, rolls back on error.
        Writes are serialised by a process-wide lock (single-process app)."""
        with self._lock:
            conn = self._memory or sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                if not self._memory:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                if not self._memory:
                    conn.close()

    def query(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
        with self.tx() as c:
            return c.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence = ()) -> int:
        with self.tx() as c:
            return c.execute(sql, params).lastrowid or 0

    # --- key/value helpers (values are stored as text)
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    def kv_set(self, key: str, value: object | None) -> None:
        if value is None:
            self.execute("DELETE FROM kv WHERE key=?", (key,))
        else:
            self.execute("INSERT INTO kv(key,value) VALUES(?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def migrate(self, ns: str, migrations: Sequence[str]) -> None:
        with self.tx() as c:
            row = c.execute("SELECT version FROM _migrations WHERE ns=?", (ns,)).fetchone()
            current = row["version"] if row else 0
            for i, script in enumerate(migrations[current:], start=current + 1):
                # executescript commits first, so write scripts idempotently (IF NOT EXISTS)
                c.executescript(script)
                c.execute("INSERT INTO _migrations(ns,version) VALUES(?,?) "
                          "ON CONFLICT(ns) DO UPDATE SET version=excluded.version", (ns, i))
