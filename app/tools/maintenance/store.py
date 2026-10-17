"""Storage for the maintenance tool (tables are prefixed mt_)."""
from __future__ import annotations

import json
import sqlite3

from app.core.db import Database

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS mt_activity (
        id INTEGER PRIMARY KEY,
        printer_id TEXT NOT NULL,
        ts REAL NOT NULL,          -- when we noticed the counter move
        since_ts REAL,             -- previous successful read (the change happened in between)
        delta INTEGER NOT NULL,    -- pages
        count INTEGER,             -- counter after the change
        kind TEXT NOT NULL,        -- external (via CUPS or unknown) | direct (not via CUPS) | nozzle | baseline
        note TEXT
    );
    CREATE INDEX IF NOT EXISTS mt_activity_ts ON mt_activity(printer_id, ts);
    CREATE TABLE IF NOT EXISTS mt_checks (
        id INTEGER PRIMARY KEY,
        printer_id TEXT NOT NULL,
        ts REAL NOT NULL,          -- submitted (app) or completed (host) time
        source TEXT NOT NULL,      -- manual | auto | host
        state TEXT NOT NULL,       -- submitted | completed | failed
        job_id INTEGER,
        cups_key TEXT,             -- '<job id>:<creation time>' for jobs imported from CUPS
        message TEXT,
        claimed_ts REAL            -- set once a counter change has been matched to this check
    );
    CREATE INDEX IF NOT EXISTS mt_checks_ts ON mt_checks(printer_id, ts);
    CREATE UNIQUE INDEX IF NOT EXISTS mt_checks_key ON mt_checks(printer_id, cups_key);
    """,
]

MIGRATIONS.append("""
    CREATE TABLE IF NOT EXISTS mt_automation (
        printer_id TEXT PRIMARY KEY,
        data TEXT NOT NULL          -- JSON of the settings this printer overrides (see automation.py)
    );
""")

from app.tools.maintenance import pages as _pages  # noqa: E402

MIGRATIONS.append(_pages.MIGRATION)  # the shared page library and each printer's rotation

OK_STATES = ("submitted", "completed")


def migrate(db: Database) -> None:
    db.migrate("maintenance", MIGRATIONS)


class Store:
    """One printer's maintenance history. Every query is scoped to its printer id."""

    def __init__(self, db: Database, printer_id: str):
        self.db, self.pid = db, printer_id

    def _key(self, key: str) -> str:
        return f"maintenance.{self.pid}.{key}"

    # --- kv with a namespace
    def get(self, key: str, default=None):
        return self.db.kv_get(self._key(key), default)

    def put(self, key: str, value) -> None:
        self.db.kv_set(self._key(key), value)

    def delete_all(self) -> None:
        """Forget everything about this printer (it was removed)."""
        with self.db.tx() as c:
            c.execute("DELETE FROM mt_activity WHERE printer_id=?", (self.pid,))
            c.execute("DELETE FROM mt_checks WHERE printer_id=?", (self.pid,))
            c.execute("DELETE FROM mt_automation WHERE printer_id=?", (self.pid,))
            prefix = self._key("")
            c.execute("DELETE FROM kv WHERE substr(key, 1, ?) = ?", (len(prefix), prefix))

    # --- automation settings this printer overrides
    def automation_overrides(self) -> dict:
        row = self.db.one("SELECT data FROM mt_automation WHERE printer_id=?", (self.pid,))
        try:
            data = json.loads(row["data"]) if row else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def set_automation_overrides(self, overrides: dict) -> None:
        if not overrides:
            self.db.execute("DELETE FROM mt_automation WHERE printer_id=?", (self.pid,))
        else:
            self.db.execute("INSERT INTO mt_automation(printer_id,data) VALUES(?,?) "
                            "ON CONFLICT(printer_id) DO UPDATE SET data=excluded.data",
                            (self.pid, json.dumps(overrides)))

    # --- activity (counter changes)
    def add_activity(self, ts: float, delta: int, count: int | None, kind: str,
                     since_ts: float | None = None, note: str | None = None) -> None:
        self.db.execute("INSERT INTO mt_activity(printer_id,ts,since_ts,delta,count,kind,note) VALUES(?,?,?,?,?,?,?)",
                        (self.pid, ts, since_ts, delta, count, kind, note))

    def last_activity(self, kind: str | tuple[str, ...] | None = None) -> sqlite3.Row | None:
        if kind:
            kinds = (kind,) if isinstance(kind, str) else kind
            marks = ",".join("?" * len(kinds))
            return self.db.one(f"SELECT * FROM mt_activity WHERE printer_id=? AND kind IN ({marks}) "
                               "ORDER BY ts DESC LIMIT 1", (self.pid, *kinds))
        return self.db.one("SELECT * FROM mt_activity WHERE printer_id=? ORDER BY ts DESC LIMIT 1", (self.pid,))

    def recent_activity(self, kind: str, limit: int) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM mt_activity WHERE printer_id=? AND kind=? AND delta>0 "
                             "ORDER BY ts DESC LIMIT ?", (self.pid, kind, limit))

    def activity_count(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM mt_activity WHERE printer_id=?", (self.pid,))["n"]

    # --- nozzle checks
    def add_check(self, ts: float, source: str, state: str, job_id: int | None = None,
                  message: str | None = None, cups_key: str | None = None,
                  claimed_ts: float | None = None) -> bool:
        """Insert a check; returns False if a row with this cups_key already exists."""
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO mt_checks(printer_id,ts,source,state,job_id,cups_key,message,claimed_ts) "
                "VALUES(?,?,?,?,?,?,?,?)", (self.pid, ts, source, state, job_id, cups_key, message, claimed_ts))
            return cur.rowcount > 0

    def unclaimed_checks(self, since_ts: float) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(OK_STATES))
        return self.db.query(
            f"SELECT * FROM mt_checks WHERE printer_id=? AND claimed_ts IS NULL AND ts>=? AND state IN ({marks}) "
            "ORDER BY ts", (self.pid, since_ts, *OK_STATES))

    def claim(self, ids: list[int], when: float) -> None:
        if ids:
            with self.db.tx() as c:
                c.executemany("UPDATE mt_checks SET claimed_ts=? WHERE id=?", [(when, i) for i in ids])

    def last_check(self, sources: tuple[str, ...], include_failed: bool = False) -> sqlite3.Row | None:
        marks = ",".join("?" * len(sources))
        extra = "" if include_failed else " AND state!='failed'"
        return self.db.one(f"SELECT * FROM mt_checks WHERE printer_id=? AND source IN ({marks}){extra} "
                           "ORDER BY ts DESC LIMIT 1", (self.pid, *sources))

    def last_confirmed_check(self, unconfirmed_since: float) -> sqlite3.Row | None:
        """The newest check that really counts as a print: one the host timer printed, one the counter
        confirmed, or one still within its matching window. An app-sent check whose page never showed up
        on the counter doesn't restart the idle clock."""
        return self.db.one(
            "SELECT * FROM mt_checks WHERE printer_id=? AND state!='failed' "
            "AND (source='host' OR claimed_ts IS NOT NULL OR ts>=?) ORDER BY ts DESC LIMIT 1",
            (self.pid, unconfirmed_since))

    def recent_checks(self, limit: int) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM mt_checks WHERE printer_id=? ORDER BY ts DESC LIMIT ?", (self.pid, limit))

    def manual_attempt_times(self, since_ts: float) -> list[float]:
        rows = self.db.query("SELECT ts FROM mt_checks WHERE printer_id=? AND source='manual' AND ts>=?",
                             (self.pid, since_ts))
        return [r["ts"] for r in rows]
