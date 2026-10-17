"""Orchestration for the maintenance tool: what each API endpoint returns."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.core import ratelimit
from app.core.context import AppContext
from app.core.errors import SourceError
from app.core.ttlcache import TTLCache
from app.tools.maintenance import automation as auto
from app.tools.maintenance import idle, normalize, schedule
from app.tools.maintenance.store import Store
from app.tools.maintenance.tracker import APP_USER, Tracker

log = logging.getLogger(__name__)

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
    def __init__(self, ctx: AppContext, printer, backend, store: Store, tracker: Tracker):
        self.ctx, self.s, self.store, self.tracker = ctx, ctx.settings, store, tracker
        self.config = printer  # this printer's saved settings (address, queue, ...)
        self.printer = backend  # IPP/SNMP/CUPS access for this one printer
        self.clock = ctx.clock
        self._print_lock = threading.Lock()
        self._printer_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self._queue_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self._jobs_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self.rule = ratelimit.RateRule(self.s.print_cooldown, self.s.print_max_per_hour, 3600.0)

    # ------------------------------------------------------------ printer card
    def printer_status(self) -> dict:
        info = normalize.printer(self._printer_cache.get(self.printer.printer_info), self.config.ip)
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
        # prints that never went through CUPS (phone straight to the printer, copies), seen on the page counter
        data["direct"] = [{"ts": r["ts"], "since_ts": r["since_ts"], "pages": r["delta"]}
                          for r in self.store.recent_activity("direct", self.s.recent_jobs)]
        try:
            raw = self._jobs_cache.get(self.printer.list_jobs)
            pattern = self.tracker.automation().job_pattern
            jobs = [normalize.job(j, pattern) for j in raw]
            jobs.sort(key=lambda j: j["id"] or 0, reverse=True)
            data["jobs"] = jobs[: self.s.recent_jobs]
            data["queued_jobs"] = [j for j in jobs if j["state"] in ("pending", "held", "printing", "stopped")]
        except SourceError as e:
            data["jobs_error"] = e.to_dict()
        return data

    # ------------------------------------------------------------ when did it last print, and what is next
    def _last_print(self, now: float, cfg: auto.Automation) -> dict | None:
        """The latest print of any kind: a counter change, or a check that counts (see store.last_confirmed_check)."""
        activity = self.store.last_activity()
        check = self.store.last_confirmed_check(now - cfg.attribution_seconds)
        candidates = []
        if activity:
            candidates.append((activity["ts"], activity["kind"]))
        if check:
            candidates.append((check["ts"], "nozzle"))
        if not candidates:
            return None
        ts, kind = max(candidates)
        # a baseline only says "nothing since then", so it is a lower bound
        return {"ts": ts, "kind": kind, "approximate": kind == "baseline"}

    def _plan(self, now: float, cfg: auto.Automation, last_print: dict | None) -> schedule.Plan:
        last_poll = self.store.get("last_count_ts")
        counter_ok = (self.store.get("counter_error") is None and last_poll is not None
                      and now - float(last_poll) < max(3 * cfg.check_seconds, 900))
        failed = self.store.last_check(("auto",), include_failed=True)
        failure = failed if failed and failed["state"] == "failed" else None
        sent = self.store.last_check(("auto", "manual"))  # only checks that really went to CUPS; failures have their own back-off
        return schedule.plan(
            enabled=cfg.enabled, now=now, last_print_ts=last_print["ts"] if last_print else None,
            idle_days=cfg.idle_days, run_at=cfg.run_at, tz=schedule.tz_for(self.s.timezone), counter_ok=counter_ok,
            last_app_check_ts=sent["ts"] if sent else None, last_failure_ts=failure["ts"] if failure else None)

    def run_schedule(self) -> schedule.Plan:
        """One look at whether this printer's automatic check is due; prints it if so. Called every few seconds
        by the poller. Everything is decided again under the lock, so two callers can't both print."""
        with self._print_lock:
            now = self.clock()
            cfg = self.tracker.automation()
            plan = self._plan(now, cfg, self._last_print(now, cfg))
            if plan.action != "print":
                return plan
            try:
                self._submit("auto", now)
            except SourceError as e:
                log.warning("automatic nozzle check for %s failed: %s", self.config.name, e.message)
                return schedule.Plan("blocked", e.message, now + schedule.RETRY_AFTER_FAILURE)
            log.info("automatic nozzle check sent for %s", self.config.name)
            return plan

    # ------------------------------------------------------------ nozzle card
    def nozzle_status(self) -> dict:
        try:
            self.tracker.ensure_fresh(FRESH_SECONDS)
        except Exception:
            pass
        now = self.clock()
        settings = self.tracker.automation()
        threshold = settings.idle_days
        last_print = self._last_print(now, settings)
        plan = self._plan(now, settings, last_print)
        external = self.store.last_activity(("external", "direct"))
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
                "poll_interval": settings.check_seconds,
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
            # the automatic check: whether it is on, when it will next print, and what is in its way
            "automation": {
                "enabled": settings.enabled, "run_at": settings.run_at, "state": plan.action,
                "reason": plan.reason, "next_run_at": plan.next_run_at,
                "timezone": str(schedule.tz_for(self.s.timezone)),
            },
        }

    @staticmethod
    def _check(row) -> dict | None:
        if row is None:
            return None
        return {"ts": row["ts"], "source": row["source"], "state": row["state"],
                "job_id": row["job_id"], "message": row["message"]}

    # ------------------------------------------------------------ automation settings
    def automation_status(self) -> dict:
        base = auto.defaults(self.s)
        overrides = self.store.automation_overrides()
        return {"values": auto.effective(base, overrides).to_dict(), "defaults": base.to_dict(),
                "customised": sorted(overrides), "timezone": str(schedule.tz_for(self.s.timezone))}

    def save_automation(self, payload: dict) -> dict:
        """Set this printer's automation settings. Raises ValidationError(fields) for bad input.
        Only values that differ from the defaults are stored, so the rest keep following them."""
        clean, errors = auto.validate(payload)
        if errors:
            raise auto.ValidationError(errors)
        base = auto.defaults(self.s)
        merged = {**self.store.automation_overrides(), **clean}
        self.store.set_automation_overrides(auto.overrides_from(merged, base))
        return self.automation_status()

    def reset_automation(self) -> dict:
        self.store.set_automation_overrides({})
        return self.automation_status()

    # ------------------------------------------------------------ printing
    def _allowance(self, now: float) -> dict:
        times = self.store.manual_attempt_times(now - self.rule.window)
        v = ratelimit.check(self.rule, times, now)
        return {"allowed": v.allowed, "retry_after": v.retry_after, "reason": v.reason}

    def _submit(self, source: str, now: float) -> int:
        """Send one nozzle check to CUPS and record it (as a failed row if CUPS says no). Caller holds the lock."""
        try:
            job_id = self.printer.submit_nozzle(f"{JOB_NAME} ({source})", NOZZLE_PS, APP_USER)
        except SourceError as e:
            self.store.add_check(now, source, "failed", message=e.message)
            raise
        self.store.add_check(now, source, "submitted", job_id)
        self._queue_cache.clear()
        self._jobs_cache.clear()
        self._printer_cache.clear()
        return job_id

    def request_print(self) -> dict:
        """Print one nozzle check now (the API's manual print). Raises RateLimited or SourceError."""
        with self._print_lock:  # serialise: check + submit + record is one step
            now = self.clock()
            verdict = ratelimit.check(self.rule, self.store.manual_attempt_times(now - self.rule.window), now)
            if not verdict.allowed:
                raise RateLimited(verdict)
            return {"job_id": self._submit("manual", now), "submitted_at": now}
