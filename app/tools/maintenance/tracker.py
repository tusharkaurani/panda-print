"""Watches the printer's lifetime counter and records what happened.

Every poll: (1) look at the CUPS job list for nozzle checks printed by the host
timer, (2) read the counter. A counter increase is split into pages we expect
(unclaimed nozzle checks, from this app or the host timer) and everything else,
which is somebody printing. That is how "last print" and "last nozzle check"
stay separate even though SNMP only gives one number.

Accuracy notes: a change is stamped with the time we *noticed* it and the
previous read time, so it is only as precise as the poll interval.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.core.errors import SourceError
from app.printer import Attrs
from app.settings import Settings
from app.tools.maintenance import automation as auto
from app.tools.maintenance import idle
from app.tools.maintenance.normalize import first, job_name
from app.tools.maintenance.store import Store

log = logging.getLogger(__name__)

APP_USER = "panda-print"
APP_JOB_MARKER = "panda-print"  # appears in the job name of checks we submit
JOB_PROCESSING, JOB_COMPLETED, JOB_CANCELED, JOB_ABORTED = 5, 9, 7, 8
DIRECT_SLACK = 180.0  # seconds: CUPS marks a job done a little before the counter finishes moving


def split_delta(delta: int, unclaimed_checks: int) -> tuple[int, int]:
    """A counter rise of `delta` pages -> (pages that were nozzle checks, pages from others)."""
    nozzle = max(0, min(delta, unclaimed_checks))
    return nozzle, delta - nozzle


class Tracker:
    def __init__(self, settings: Settings, store: Store, printer, clock, host_state_file: str = ""):
        self.s, self.store, self.printer, self.clock = settings, store, printer, clock
        self.host_state_file = Path(host_state_file) if host_state_file else None
        self._newest_document: float | None = None  # newest finished non-nozzle job seen in CUPS
        # How many real (non-nozzle) CUPS jobs were active since the previous counter read, or None
        # when CUPS couldn't be asked. Zero while the counter moves means the pages came from
        # somewhere else: a phone printing straight to the printer, or a copy made on its glass.
        self._cups_active: int | None = None
        self._lock = threading.Lock()

    def automation(self) -> auto.Automation:
        """This printer's settings: what it overrides, else the defaults (the environment values)."""
        return auto.effective(auto.defaults(self.s), self.store.automation_overrides())

    # ------------------------------------------------------------ polling
    def poll(self) -> None:
        with self._lock:
            now = self.clock()
            self.store.put("last_poll_ts", now)
            self._cups_active = None
            try:
                jobs = self.printer.list_jobs()
                self.import_host_checks(jobs)
                self._newest_document = self.newest_document_ts(jobs)
                prev = self.store.get("last_count_ts")
                self._cups_active = self.documents_active_since(jobs, (float(prev) if prev else now) - DIRECT_SLACK)
                self.store.put("cups_error", None)
            except SourceError as e:
                self.store.put("cups_error", e.message)
            try:
                count = self.printer.read_counter()
            except SourceError as e:
                self.store.put("counter_error", e.message)
                return
            self.store.put("counter_error", None)
            self.record_counter(count, now)

    def ensure_fresh(self, max_age: float) -> None:
        """Poll now unless a poll already happened within max_age seconds."""
        last = self.store.get("last_poll_ts")
        if last is None or self.clock() - float(last) >= max_age:
            self.poll()

    # ------------------------------------------------------------ CUPS sync
    def import_host_checks(self, jobs: list[Attrs]) -> int:
        """Record nozzle-check jobs that the host timer submitted (not ours)."""
        pattern = self.automation().job_pattern.lower()
        added = 0
        for j in jobs:
            name = job_name(j)
            user = str(first(j, "job-originating-user-name", ""))
            if pattern not in name.lower():
                continue
            if APP_JOB_MARKER in name.lower() or user == APP_USER:
                continue  # one of ours; recorded when we submitted it
            job_id, created = first(j, "job-id"), first(j, "time-at-creation")
            if job_id is None or created is None:
                continue
            state = first(j, "job-state")
            if state == JOB_COMPLETED:
                ts, status, msg = first(j, "time-at-completed", created), "completed", None
            elif state in (JOB_CANCELED, JOB_ABORTED):
                ts, status, msg = created, "failed", "Job was canceled" if state == JOB_CANCELED else "Job was aborted"
            else:
                continue  # still queued or printing: import it when it finishes
            if self.store.add_check(float(ts), "host", status, int(job_id), msg, cups_key=f"{job_id}:{created}"):
                added += 1
        return added

    def is_nozzle_job(self, job: Attrs) -> bool:
        name = job_name(job).lower()
        return self.automation().job_pattern.lower() in name or APP_JOB_MARKER in name

    def newest_document_ts(self, jobs: list[Attrs]) -> float | None:
        """When the newest finished job that is not a nozzle check completed (None if CUPS has none)."""
        times = [first(j, "time-at-completed") or first(j, "time-at-creation") for j in jobs
                 if first(j, "job-state") == JOB_COMPLETED and not self.is_nozzle_job(j)]
        times = [float(t) for t in times if t]
        return max(times) if times else None

    def documents_active_since(self, jobs: list[Attrs], since: float) -> int:
        """Real (non-nozzle) CUPS jobs that are printing now or finished at/after `since`."""
        n = 0
        for j in jobs:
            if self.is_nozzle_job(j):
                continue
            state = first(j, "job-state")
            done = first(j, "time-at-completed") or first(j, "time-at-creation") or 0
            if state == JOB_PROCESSING or (state == JOB_COMPLETED and done >= since):
                n += 1
        return n

    # ------------------------------------------------------------ counter
    def host_state(self) -> tuple[idle.HostState | None, str | None]:
        """The host timer's state file, if mounted: (state, error)."""
        path = self.host_state_file
        if path is None:
            return None, None
        try:
            text = path.read_text()
        except FileNotFoundError:
            return None, None
        except OSError as e:
            return None, f"Could not read {path}: {e.strerror or e}"
        state = idle.parse_host_state(text)
        return state, (None if state else f"{path} is not in the expected '<count> <epoch>' format")

    def record_counter(self, count: int, now: float) -> None:
        last_raw = self.store.get("last_count")
        if last_raw is None:
            self._first_sample(count, now)
            return
        last, prev_ts = int(last_raw), self.store.get("last_count_ts")
        prev = float(prev_ts) if prev_ts else None
        self.store.put("last_count_ts", now)
        if count == last:
            return
        self.store.put("last_count", count)
        if count < last:
            self.store.add_activity(now, 0, count, "baseline",
                                    note=f"Counter went backwards ({last} to {count}); tracking restarted")
            return
        unclaimed = self.store.unclaimed_checks(now - self.automation().attribution_seconds)
        nozzle, other = split_delta(count - last, len(unclaimed))
        if nozzle:
            self.store.claim([r["id"] for r in unclaimed[:nozzle]], now)
            self.store.add_activity(now, nozzle, count, "nozzle", prev)
        if other:
            # pages with no CUPS job behind them came straight to the printer
            kind = "direct" if self._cups_active == 0 else "external"
            self.store.add_activity(now, other, count, kind, prev)
        log.info("counter %s -> %s (%s nozzle, %s other)", last, count, nozzle, other)

    def _first_sample(self, count: int, now: float) -> None:
        """First ever read: start the idle clock from the host timer's state if it agrees with
        the counter, else from the newest finished job in CUPS, else from now."""
        host, _ = self.host_state()
        self.store.put("first_seen_ts", now)
        self.store.put("last_count", count)
        self.store.put("last_count_ts", now)
        if host and host.count == count:
            ts, note = host.changed_at, "From the host timer's state file (can lag the real print by a day)"
        elif self._newest_document is not None and self._newest_document <= now:
            # CUPS remembers real prints: better than starting the clock at "now"
            self.store.add_activity(self._newest_document, 0, count, "external",
                                    note="Newest finished job in CUPS")
            return
        else:
            ts, note = now, "First reading; earlier prints are unknown"
        self.store.add_activity(ts, 0, count, "baseline", note=note)
