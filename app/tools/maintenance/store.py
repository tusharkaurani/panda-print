"""Storage for the maintenance tool (tables are prefixed mt_)."""
from __future__ import annotations

import sqlite3

from app.core.db import Database

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS mt_activity (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,          -- when we noticed the counter move
        since_ts REAL,             -- previous successful read (the change happened in between)
        delta INTEGER NOT NULL,    -- pages
        count INTEGER,             -- counter after the change
        kind TEXT NOT NULL,        -- external | nozzle | baseline
        note TEXT
    );
    CREATE INDEX IF NOT EXISTS mt_activity_ts ON mt_activity(ts);
    CREATE TABLE IF NOT EXISTS mt_checks (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,          -- submitted (app) or completed (host) time
        source TEXT NOT NULL,      -- manual | auto | host
        state TEXT NOT NULL,       -- submitted | completed | failed
        job_id INTEGER,
        cups_key TEXT UNIQUE,      -- '<job id>:<creation time>' for jobs imported from CUPS
        message TEXT,
        claimed_ts REAL            -- set once a counter change has been matched to this check
    );
    CREATE INDEX IF NOT EXISTS mt_checks_ts ON mt_checks(ts);
    """,
]

OK_STATES = ("submitted", "completed")


class Store:
    def __init__(self, db: Database):
        self.db = db
        db.migrate("maintenance", MIGRATIONS)

    # --- kv with a namespace
    def get(self, key: str, default=None):
        return self.db.kv_get(f"maintenance.{key}", default)

    def put(self, key: str, value) -> None:
        self.db.kv_set(f"maintenance.{key}", value)

    # --- activity (counter changes)
    def add_activity(self, ts: float, delta: int, count: int | None, kind: str,
                     since_ts: float | None = None, note: str | None = None) -> None:
        self.db.execute("INSERT INTO mt_activity(ts,since_ts,delta,count,kind,note) VALUES(?,?,?,?,?,?)",
                        (ts, since_ts, delta, count, kind, note))

    def last_activity(self, kind: str | None = None) -> sqlite3.Row | None:
        if kind:
            return self.db.one("SELECT * FROM mt_activity WHERE kind=? ORDER BY ts DESC LIMIT 1", (kind,))
        return self.db.one("SELECT * FROM mt_activity ORDER BY ts DESC LIMIT 1")

    def activity_count(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM mt_activity")["n"]

    # --- nozzle checks
    def add_check(self, ts: float, source: str, state: str, job_id: int | None = None,
                  message: str | None = None, cups_key: str | None = None,
                  claimed_ts: float | None = None) -> bool:
        """Insert a check; returns False if a row with this cups_key already exists."""
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO mt_checks(ts,source,state,job_id,cups_key,message,claimed_ts) "
                "VALUES(?,?,?,?,?,?,?)", (ts, source, state, job_id, cups_key, message, claimed_ts))
            return cur.rowcount > 0

    def unclaimed_checks(self, since_ts: float) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(OK_STATES))
        return self.db.query(
            f"SELECT * FROM mt_checks WHERE claimed_ts IS NULL AND ts>=? AND state IN ({marks}) ORDER BY ts",
            (since_ts, *OK_STATES))

    def claim(self, ids: list[int], when: float) -> None:
        if ids:
            with self.db.tx() as c:
                c.executemany("UPDATE mt_checks SET claimed_ts=? WHERE id=?", [(when, i) for i in ids])

    def last_check(self, sources: tuple[str, ...], include_failed: bool = False) -> sqlite3.Row | None:
        marks = ",".join("?" * len(sources))
        extra = "" if include_failed else " AND state!='failed'"
        return self.db.one(f"SELECT * FROM mt_checks WHERE source IN ({marks}){extra} ORDER BY ts DESC LIMIT 1",
                           sources)

    def recent_checks(self, limit: int) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM mt_checks ORDER BY ts DESC LIMIT ?", (limit,))

    def manual_attempt_times(self, since_ts: float) -> list[float]:
        rows = self.db.query("SELECT ts FROM mt_checks WHERE source='manual' AND ts>=?", (since_ts,))
        return [r["ts"] for r in rows]
