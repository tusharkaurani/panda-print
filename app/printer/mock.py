"""Fake printer + CUPS with realistic data, for MOCK=1 and for tests.

It behaves like a tiny printer: submitting a nozzle check makes a CUPS job, the
printer shows "processing" for a few seconds, then the lifetime counter goes up
by one, so the dashboard's tracking and attribution can be seen end to end.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from app.core.errors import SourceError
from app.printer import Attrs
from app.settings import Settings

DAY = 86400.0
PRINT_SECONDS = 8.0
FINAL_COUNT = 174

# (days ago, kind, pages, job name, user). "external" = someone printed a document,
# "host" = the host's daily timer printed a nozzle check, "manual" = from this dashboard.
HISTORY = [
    (33.0, "external", 6, "Council tax bill.pdf", "tushar"),
    (30.0, "external", 3, "Recipe printout.pdf", "tushar"),
    (23.0, "host", 1, "nozzle-check.ps", "root"),
    (16.0, "host", 1, "nozzle-check.ps", "root"),
    (12.0, "external", 5, "Holiday booking.pdf", "tushar"),
    (11.9, "external", 4, "Boarding pass - LHR.pdf", "tushar"),
    (6.0, "manual", 1, "panda-print nozzle check (manual)", "panda-print"),
    (2.6, "external", 2, "Invoice-2291.pdf", "tushar"),
]
FIRST_JOB_ID = 31


def history_events(now: float) -> list[dict]:
    """The scripted past, with absolute times, job ids and running counter values."""
    count = FINAL_COUNT - sum(h[2] for h in HISTORY)
    events = []
    for i, (days, kind, pages, name, user) in enumerate(HISTORY):
        count += pages
        events.append({"ts": now - days * DAY, "kind": kind, "pages": pages, "name": name,
                       "user": user, "job_id": FIRST_JOB_ID + i, "count_after": count})
    return events


class MockPrinter:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time):
        self.s = settings
        self.clock = clock
        self._lock = threading.Lock()
        now = clock()
        self.counter = FINAL_COUNT
        self.jobs: list[Attrs] = []
        for ev in history_events(now):
            self.jobs.append(self._job(ev["job_id"], ev["name"], ev["user"], ev["ts"] - 5, ev["ts"] + 20, ev["pages"]))
        self._next_job = FIRST_JOB_ID + len(HISTORY)
        self._pending: list[tuple[float, int]] = []  # (done at, job id)

    # ---- helpers
    @staticmethod
    def _job(job_id: int, name: str, user: str, created: float, completed: float | None, pages: int) -> Attrs:
        job: Attrs = {
            "job-id": [job_id], "job-name": [name], "job-originating-user-name": [user],
            "time-at-creation": [int(created)], "job-media-sheets-completed": [pages],
            "job-k-octets": [pages * 48],
        }
        if completed is None:
            job.update({"job-state": [5], "job-state-reasons": ["job-printing"]})
        else:
            job.update({"job-state": [9], "job-state-reasons": ["job-completed-successfully"],
                        "time-at-completed": [int(completed)]})
        return job

    def _fail(self, what: str, source: str, message: str) -> None:
        if what in self.s.mock_fail:
            raise SourceError(source, message, "This is a simulated failure (MOCK_FAIL).")

    def _tick(self) -> None:
        now = self.clock()
        for done, job_id in [p for p in self._pending if p[0] <= now]:
            self._pending.remove((done, job_id))
            self.counter += 1
            for j in self.jobs:
                if j["job-id"][0] == job_id:
                    j.update({"job-state": [9], "job-state-reasons": ["job-completed-successfully"],
                              "time-at-completed": [int(done)], "job-media-sheets-completed": [1]})

    # ---- PrinterBackend
    def printer_info(self) -> Attrs:
        self._fail("printer", "printer", "The printer (192.168.1.18) did not answer in time.")
        with self._lock:
            self._tick()
            busy = bool(self._pending)
        return {
            "printer-name": ["DCP-T730DW"], "printer-info": ["Brother DCP-T730DW"],
            "printer-make-and-model": ["Brother DCP-T730DW"], "printer-location": ["Study"],
            "printer-state": [4 if busy else 3], "printer-state-reasons": ["none"],
            "printer-is-accepting-jobs": [True],
            "printer-firmware-string-version": ["Ver.U"], "printer-up-time": [int(self.clock() % 10_000_000)],
            "printer-more-info": ["http://192.168.1.18/"],
            "marker-names": ["M", "C", "Y", "BK"], "marker-levels": [94, 95, 94, 95],
            "marker-colors": ["#FF00FF", "#00FFFF", "#FFFF00", "#000000"],
            "marker-types": ["ink-cartridge"] * 4, "marker-low-levels": [10] * 4, "marker-high-levels": [100] * 4,
            "media-ready": ["iso_a4_210x297mm"], "media-default": ["iso_a4_210x297mm"],
            "sides-default": ["one-sided"], "print-color-mode-default": ["color"],
        }

    def read_counter(self) -> int:
        self._fail("snmp", "snmp", "The printer's SNMP service (192.168.1.18:161) did not answer in time.")
        with self._lock:
            self._tick()
            return self.counter

    def queue_info(self) -> Attrs:
        self._fail("cups", "cups", "CUPS (http://127.0.0.1:631) refused the connection.")
        with self._lock:
            self._tick()
            busy = bool(self._pending)
        return {
            "printer-name": [self.s.cups_queue], "printer-info": ["Brother DCP-T730DW"],
            "printer-make-and-model": ["Brother DCP-T730DW"], "printer-location": ["Study"],
            "printer-state": [4 if busy else 3], "printer-state-reasons": ["none"],
            "printer-is-accepting-jobs": [True], "queued-job-count": [len(self._pending)],
            "device-uri": ["ipp://192.168.1.18/ipp/print"],
            "printer-uri-supported": [f"ipp://localhost/printers/{self.s.cups_queue}"],
        }

    def list_jobs(self) -> list[Attrs]:
        self._fail("cups", "cups", "CUPS (http://127.0.0.1:631) refused the connection.")
        with self._lock:
            self._tick()
            return [dict(j) for j in self.jobs]

    def submit_nozzle(self, job_name: str, document: bytes, user: str) -> int:
        self._fail("cups", "cups", "CUPS (http://127.0.0.1:631) refused the connection.")
        if not document.startswith(b"%!PS"):
            raise SourceError("cups", "The document is not PostScript.")
        with self._lock:
            now = self.clock()
            job_id, self._next_job = self._next_job, self._next_job + 1
            self.jobs.append(self._job(job_id, job_name, user, now, None, 0))
            self._pending.append((now + PRINT_SECONDS, job_id))
            return job_id
