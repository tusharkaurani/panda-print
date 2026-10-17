"""Orchestration for the maintenance tool: what each API endpoint returns."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.core import ratelimit
from app.core.context import AppContext
from app.core.errors import SourceError
from app.core.ttlcache import TTLCache
from app.tools.maintenance import automation as auto
from app.tools.maintenance import idle, normalize, pages, schedule
from app.tools.maintenance.store import Store
from app.tools.maintenance.tracker import APP_USER, Tracker

log = logging.getLogger(__name__)

CHOICES = ("enabled", "rotate", "sides")  # what to do and how to print: not tuning settings, so a reset leaves them

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
    def __init__(self, ctx: AppContext, printer, backend, store: Store, tracker: Tracker, library: pages.Library,
                 print_lock: threading.Lock | None = None):
        self.ctx, self.s, self.store, self.tracker = ctx, ctx.settings, store, tracker
        self.library = library  # the pages shared by every printer
        self.config = printer  # this printer's saved settings (address, queue, ...)
        self.printer = backend  # IPP/SNMP/CUPS access for this one printer
        self.clock = ctx.clock
        self._print_lock = print_lock or threading.Lock()  # one per printer, kept across rebuilds (see Runtimes)
        self._printer_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self._queue_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self._jobs_cache: TTLCache = TTLCache(LIVE_CACHE_SECONDS)
        self.rule = ratelimit.RateRule(self.s.print_cooldown, self.s.print_max_per_hour, 3600.0)

    # ------------------------------------------------------------ printer card
    def printer_status(self) -> dict:
        """The printer card. Every good answer is remembered, so a printer that is switched off still shows what
        was last known (ink, model, page count) with an "offline" note, instead of an empty error card."""
        try:
            info = normalize.printer(self._printer_cache.get(self.printer.printer_info), self.config.ip)
        except SourceError as e:
            remembered = self._remembered_status()
            if remembered is None:  # never seen it answer: nothing to show but the reason
                raise
            info, as_of = remembered
            info["state"] = {"code": "offline", "label": "Offline", "health": "bad", "reasons": [],
                             "message": "", "accepting": None}
            info["offline"] = {"as_of": as_of, "error": e.to_dict()}
        else:
            self.store.put("last_status", json.dumps({"info": info, "ts": self.clock()}))
        info["counter"] = self._counter()
        return info

    def _remembered_status(self) -> tuple[dict, float] | None:
        raw = self.store.get("last_status")
        try:
            saved = json.loads(raw) if raw else None
            return (saved["info"], float(saved["ts"])) if saved else None
        except (ValueError, KeyError, TypeError):
            return None

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
    @property
    def cups_configured(self) -> bool:
        """CUPS is optional: a printer without a queue still shows its status, ink and page count."""
        return bool((self.config.cups_queue or "").strip())

    def queue_status(self) -> dict:
        if not self.cups_configured:
            return {"configured": False, "jobs": [], "direct": [], "jobs_error": None}
        data = normalize.queue(self._queue_cache.get(self.printer.queue_info))
        data["configured"] = True
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
            last_app_check_ts=sent["ts"] if sent else None, last_failure_ts=failure["ts"] if failure else None,
            can_print=self.cups_configured)

    def run_schedule(self) -> schedule.Plan:
        """One look at whether this printer's automatic check is due; prints it if so. Called every few seconds
        by the poller. Everything is decided again under the lock, so two callers can't both print."""
        with self._print_lock:
            now = self.clock()
            cfg = self.tracker.automation()
            plan = self._plan(now, cfg, self._last_print(now, cfg))
            if plan.action != "print":
                return plan
            # Due. The stored counter reading can be minutes old, so look at the printer once more: if someone has
            # printed since, the idle clock restarts; if it can't be read, "idle" isn't known and we wait.
            self.tracker.poll()
            now = self.clock()
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
            "cups": {"configured": self.cups_configured},
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
                "cups_error": self.store.get("cups_error") if self.cups_configured else None,
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
                # the time as the container's clock shows it, so it matches the "don't print before" time
                "next_run_label": schedule.format_local(plan.next_run_at, self.s.timezone) if plan.next_run_at else None,
                "timezone": schedule.tz_label(self.s.timezone),
            },
        }

    @staticmethod
    def _check(row) -> dict | None:
        if row is None:
            return None
        return {"ts": row["ts"], "source": row["source"], "state": row["state"], "job_id": row["job_id"],
                "message": row["message"], "page_name": row["page_name"], "pages": row["pages"]}

    # ------------------------------------------------------------ automation settings
    def automation_status(self) -> dict:
        base = auto.defaults(self.s)
        overrides = self.store.automation_overrides()
        # the on/off switch is not one of the settings: it is neither counted as "changed" nor cleared by a reset
        settings_changed = sorted(k for k in overrides if k not in CHOICES)
        return {"values": auto.effective(base, overrides).to_dict(), "defaults": base.to_dict(),
                "customised": settings_changed, "timezone": schedule.tz_label(self.s.timezone),
                "cups_configured": self.cups_configured}

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
        """Back to the defaults for every setting. The on/off switch stays as it is."""
        overrides = self.store.automation_overrides()
        self.store.set_automation_overrides({k: v for k, v in overrides.items() if k in CHOICES})
        return self.automation_status()

    # ------------------------------------------------------------ printing
    def _allowance(self, now: float) -> dict:
        times = self.store.manual_attempt_times(now - self.rule.window)
        v = ratelimit.check(self.rule, times, now)
        return {"allowed": v.allowed, "retry_after": v.retry_after, "reason": v.reason}

    # ------------------------------------------------------------ what to print
    def _items(self) -> list[tuple[str, bool]]:
        """This printer's pages in print order: [(id, enabled)], the standard test page included."""
        return pages.arrange([p["id"] for p in self.library.list()], self.store.rotation_rows())

    def _load(self, page_id: str):
        """(name, mime, bytes, sheets) of a page, or None if it can't be read (deleted behind our back)."""
        if page_id == pages.STANDARD:
            return pages.STANDARD_NAME, "application/postscript", NOZZLE_PS, 1
        got = self.library.read(page_id)
        if got is None:
            return None
        page, data = got
        return page["name"], page["mime"], data, page["pages"]

    def _next(self, cfg: auto.Automation) -> pages.Choice:
        """What the next automatic or 'Print now' would print (without advancing anything)."""
        if not cfg.rotate:
            return pages.Choice(pages.STANDARD)
        enabled = [pid for pid, on in self._items() if on]
        return pages.choose(enabled, self.store.get("rotation_last"), int(self.store.get("rotation_count") or 0),
                            cfg.plain_every)

    def rotation_status(self) -> dict:
        cfg = self.tracker.automation()
        library = {p["id"]: p for p in self.library.list()}
        items = []
        for pid, on in self._items():
            p = library.get(pid)
            items.append({"id": pid, "enabled": on, "standard": pid == pages.STANDARD,
                          "name": pages.STANDARD_NAME if p is None else p["name"],
                          "mime": "application/postscript" if p is None else p["mime"],
                          "pages": 1 if p is None else p["pages"], "size": None if p is None else p["size"],
                          "thumb": False if p is None else p["thumb"]})
        nxt = self._next(cfg)
        names = {i["id"]: i["name"] for i in items}
        return {"cups_configured": self.cups_configured, "sides": cfg.sides,
                "duplex_supported": self.automatic_duplex(),  # automatic two-sided printing only (it prints unattended)
                "rotate": cfg.rotate, "plain_every": cfg.plain_every, "items": items,
                "next": {"id": nxt.id, "name": names.get(nxt.id, pages.STANDARD_NAME), "plain": nxt.plain},
                "count": int(self.store.get("rotation_count") or 0), "limits": self.library.limits()}

    def save_rotation(self, items) -> dict:
        """Set this printer's order and which pages are on: [{id, enabled}] in print order."""
        if not isinstance(items, list):
            raise pages.PageError("Send the pages as a list.")
        known = {pages.STANDARD} | {p["id"] for p in self.library.list()}
        rows, seen = [], set()
        for it in items:
            pid = it.get("id") if isinstance(it, dict) else None
            if pid not in known or pid in seen:
                raise pages.PageError("That list has a page that doesn't exist, or one listed twice.")
            seen.add(pid)
            rows.append((pid, bool(it.get("enabled", True))))
        self.store.set_rotation(rows)
        return self.rotation_status()

    def _pick(self, cfg: auto.Automation, explicit: str | None):
        """-> (page id, (name, mime, data, sheets), advance the rotation afterwards?)"""
        if explicit:
            doc = self._load(explicit)
            if doc is None:
                raise pages.PageError("That page doesn't exist (any more).", 404)
            return explicit, doc, False
        if not cfg.rotate:
            return pages.STANDARD, self._load(pages.STANDARD), False
        enabled = [pid for pid, on in self._items() if on]
        last, count = self.store.get("rotation_last"), int(self.store.get("rotation_count") or 0)
        while True:
            choice = pages.choose(enabled, last, count, cfg.plain_every)
            doc = self._load(choice.id)
            if doc is not None:
                return choice.id, doc, True
            enabled = [pid for pid in enabled if pid != choice.id]  # unreadable: skip it and try the next
            if not enabled:
                return pages.STANDARD, self._load(pages.STANDARD), True

    def _submit(self, source: str, now: float, explicit: str | None = None) -> int:
        """Print one page (the next in the rotation, or the one asked for) and record it, as a failed row if CUPS
        says no. Caller holds the lock."""
        cfg = self.tracker.automation()
        page_id, (name, mime, data, sheets), advance = self._pick(cfg, explicit)
        mark = None if page_id == pages.STANDARD else page_id
        count = int(self.store.get("rotation_count") or 0)
        sides = self._sides_for(cfg, sheets)
        try:
            job_id = self.printer.submit_document(f"{JOB_NAME} ({source}) - {name}"[:120], data, APP_USER, mime, sides=sides)
        except SourceError as e:
            self.store.add_check(now, source, "failed", message=e.message, page_id=mark, page_name=name)
            raise
        self.store.add_check(now, source, "submitted", job_id, page_id=mark, page_name=name, pages=sheets)
        if advance:  # remember where the rotation is, by page id so reordering or deleting can't confuse it
            if not (cfg.plain_every and (count + 1) % cfg.plain_every == 0):  # a plain turn doesn't move the cursor
                self.store.put("rotation_last", page_id)
            self.store.put("rotation_count", count + 1)
        self._queue_cache.clear()
        self._jobs_cache.clear()
        self._printer_cache.clear()
        return job_id

    @staticmethod
    def _two_sided(attrs) -> bool:
        return any(str(x).startswith("two-sided") for x in (attrs.get("sides-supported") or []))

    def automatic_duplex(self) -> bool:
        """Can this printer print on both sides BY ITSELF? Printing here is unattended, so it must not depend on
        someone flipping the paper. Both the printer and its CUPS queue must offer two-sided printing; a printer
        without an automatic duplexer doesn't list it. If either can't be read right now (switched off), the last
        answer is used, and a printer never confirmed is treated as not capable."""
        if not self.cups_configured:
            return False
        try:
            printer = self._printer_cache.get(self.printer.printer_info)
            queue = self._queue_cache.get(self.printer.queue_info)
        except SourceError:
            return self.store.get("auto_duplex") == "1"
        ok = self._two_sided(printer) and self._two_sided(queue)
        self.store.put("auto_duplex", "1" if ok else "0")
        return ok

    def _sides_for(self, cfg: auto.Automation, sheets: int) -> str | None:
        """Both sides only for a file with more than one page, and only on a printer with an automatic duplexer."""
        if sheets < 2 or cfg.sides == "one-sided" or not self.automatic_duplex():
            return None
        return cfg.sides

    def request_print(self, page_id: str | None = None) -> dict:
        """Print now (the API's manual print): the next page in the rotation, or `page_id` if given (which
        leaves the rotation where it was). Raises RateLimited, SourceError or PageError."""
        if not self.cups_configured:
            raise SourceError("cups", "No CUPS print queue is set for this printer, so it can't print.",
                              "Install CUPS, add the printer to it, then choose its queue under Printers → Edit.")
        with self._print_lock:  # serialise: check + submit + record is one step
            now = self.clock()
            verdict = ratelimit.check(self.rule, self.store.manual_attempt_times(now - self.rule.window), now)
            if not verdict.allowed:
                raise RateLimited(verdict)
            return {"job_id": self._submit("manual", now, page_id), "submitted_at": now}
