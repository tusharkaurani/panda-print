"""Orchestration for the maintenance tool: what each API endpoint returns."""
from __future__ import annotations

import threading
from pathlib import Path

from app.core import ratelimit
from app.core.context import AppContext
from app.core.errors import SourceError
from app.core.ttlcache import TTLCache
from app.tools.maintenance import idle, normalize
from app.tools.maintenance.store import Store
from app.tools.maintenance.tracker import APP_USER, Tracker

NOZZLE_PS = (Path(__file__).with_name("nozzle-check.ps")).read_bytes()
JOB_NAME = "panda-print nozzle check"
HISTORY_LIMIT = 10
LIVE_CACHE_SECONDS = 10  # many open tabs shouldn't mean many requests to the printer
FRESH_SECONDS = 30  # how stale the counter may be before a page view re-reads it


class RateLimited(Exception):
    def __init__(self, verdict: ratelimit.Verdict):
        super().__init__(verdict.reason)
        self.verdict = verdict


class Service:
    def __init__(self, ctx: AppContext, store: Store, tracker: Tracker):
        self.ctx, self.s, self.store, self.tracker = ctx, ctx.settings, store, tracker
        self.printer = ctx.printer
        self.clock = ctx.clock
        self._print_lock = threading.Lock()
        self._printer_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self._queue_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self._jobs_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self.rule = ratelimit.RateRule(self.s.print_cooldown, self.s.print_max_per_hour, 3600.0)

    # ------------------------------------------------------------ printer card
    def printer_status(self) -> dict:
        info = normalize.printer(self._printer_cache.get(self.printer.printer_info))
        info["counter"] = self._counter()
        return info

    def _counter(self) -> dict:
        try:
            self.tracker.ensure_fresh(FRESH_SECONDS)
        except Exception:  # a bug in tracking must not blank the printer card
            pass
        value = self.store.get("last_count")
        as_of = self.store.get("last_count_ts")
        return {"value": int(value) if value is not None else None,
                "as_of": float(as_of) if as_of else None,
                "error": self.store.get("counter_error")}

    # ------------------------------------------------------------ queue card
    def queue_status(self) -> dict:
        data = normalize.queue(self._queue_cache.get(self.printer.queue_info))
        data["jobs"], data["jobs_error"] = [], None
        try:
            raw = self._jobs_cache.get(self.printer.list_jobs)
            jobs = [normalize.job(j, self.s.nozzle_job_pattern) for j in raw]
            jobs.sort(key=lambda j: j["id"] or 0, reverse=True)
            data["jobs"] = jobs[: self.s.recent_jobs]
            data["queued_jobs"] = [j for j in jobs if j["state"] in ("pending", "held", "printing", "stopped")]
        except SourceError as e:
            data["jobs_error"] = e.to_dict()
        return data

    # ------------------------------------------------------------ nozzle card
    def nozzle_status(self) -> dict:
        try:
            self.tracker.ensure_fresh(FRESH_SECONDS)
        except Exception:
            pass
        now = self.clock()
        threshold = self.s.idle_days
        activity = self.store.last_activity()
        check = self.store.last_check(("manual", "auto", "host"))
        # The latest print of any kind: a counter change, or a check we know was submitted.
        candidates = []
        if activity:
            candidates.append((activity["ts"], activity["kind"]))
        if check:
            candidates.append((check["ts"], "nozzle"))
        last_print = None
        if candidates:
            ts, kind = max(candidates)
            # a baseline only says "nothing since then", so it is a lower bound
            last_print = {"ts": ts, "kind": kind, "approximate": kind == "baseline"}
        external = self.store.last_activity("external")
        auto = self.store.last_check(("auto", "host"))
        manual = self.store.last_check(("manual",), include_failed=True)

        nxt = idle.next_check(last_print["ts"], now, threshold) if last_print else None
        host, host_err = self.tracker.host_state()
        first_seen = self.store.get("first_seen_ts")
        return {
            "idle_days_threshold": threshold,
            "last_print": last_print,
            "days_since_last_print": idle.idle_days(last_print["ts"], now) if last_print else None,
            "last_external_print": external["ts"] if external else None,
            "next_check": ({"due_at": nxt.due_at, "days_until": nxt.days_until, "overdue": nxt.overdue}
                           if nxt else None),
            "last_auto": self._check(auto),
            "last_manual": self._check(manual),
            "history": [self._check(r) for r in self.store.recent_checks(HISTORY_LIMIT)],
            "tracking": {
                "since": float(first_seen) if first_seen else None,
                "last_poll": float(self.store.get("last_poll_ts") or 0) or None,
                "poll_interval": self.s.poll_interval,
                "counter_error": self.store.get("counter_error"),
                "cups_error": self.store.get("cups_error"),
            },
            "host_state": {
                "configured": host is not None or host_err is not None,
                "count": host.count if host else None,
                "noticed_at": host.changed_at if host else None,
                "error": host_err,
            },
            "print": self._allowance(now),
        }

    @staticmethod
    def _check(row) -> dict | None:
        if row is None:
            return None
        return {"ts": row["ts"], "source": row["source"], "state": row["state"],
                "job_id": row["job_id"], "message": row["message"]}

    # ------------------------------------------------------------ printing
    def _allowance(self, now: float) -> dict:
        times = self.store.manual_attempt_times(now - self.rule.window)
        v = ratelimit.check(self.rule, times, now)
        return {"allowed": v.allowed, "retry_after": v.retry_after, "reason": v.reason}

    def request_print(self) -> dict:
        """Print one nozzle check now. Raises RateLimited or SourceError."""
        with self._print_lock:  # serialise: check + submit + record is one step
            now = self.clock()
            verdict = ratelimit.check(self.rule, self.store.manual_attempt_times(now - self.rule.window), now)
            if not verdict.allowed:
                raise RateLimited(verdict)
            try:
                job_id = self.printer.submit_nozzle(JOB_NAME + " (manual)", NOZZLE_PS, APP_USER)
            except SourceError as e:
                self.store.add_check(now, "manual", "failed", message=e.message)
                raise
            self.store.add_check(now, "manual", "submitted", job_id)
            self._queue_cache.clear()
            self._jobs_cache.clear()
            self._printer_cache.clear()
            return {"job_id": job_id, "submitted_at": now}
