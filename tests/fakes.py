"""Test doubles: a fake printer + CUPS + network with realistic data. Used by the tests only;
nothing here ships in the app or touches a real device.

It behaves like a tiny printer: submitting a nozzle check makes a CUPS job, the
printer shows "processing" for a few seconds, then the lifetime counter goes up
by one, so the dashboard's tracking and attribution can be seen end to end.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from app.core.errors import SourceError
from app.core.printers import Printer, validate
from app.printer import Attrs
from app.settings import Settings
from app.tools.maintenance.pages import count_pdf_pages
from app.tools.maintenance.store import Store

DAY = 86400.0
PRINT_SECONDS = 8.0
FINAL_COUNT = 174

# (days ago, kind, pages, job name, user). "external" = someone printed a document,
# "host" = the host's daily timer printed a nozzle check, "manual" = from this dashboard.
HISTORY = [
    (33.0, "external", 6, "Council tax bill.pdf", "alex"),
    (30.0, "external", 3, "Recipe printout.pdf", "alex"),
    (23.0, "host", 1, "nozzle-check.ps", "root"),
    (16.0, "host", 1, "nozzle-check.ps", "root"),
    (12.0, "external", 5, "Holiday booking.pdf", "alex"),
    (11.9, "external", 4, "Boarding pass - LHR.pdf", "alex"),
    (6.0, "manual", 1, "panda-print nozzle check (manual)", "panda-print"),
    (2.6, "external", 2, "Invoice-2291.pdf", "alex"),
]
FIRST_JOB_ID = 31

# the pretend network: what the setup wizard can "find"
FAKE_DEVICES = {
    "192.168.1.18": {"model": "Brother DCP-T730DW", "queue": "T730", "rp": "ipp/print"},
    "192.168.1.23": {"model": "HP ENVY 6000 series", "queue": "HP_ENVY", "rp": "ipp/print"},
}


def history_events(now: float) -> list[dict]:
    """The scripted past, with absolute times, job ids and running counter values."""
    count = FINAL_COUNT - sum(h[2] for h in HISTORY)
    events = []
    for i, (days, kind, pages, name, user) in enumerate(HISTORY):
        count += pages
        events.append({"ts": now - days * DAY, "kind": kind, "pages": pages, "name": name,
                       "user": user, "job_id": FIRST_JOB_ID + i, "count_after": count})
    return events


class FakePrinter:
    def __init__(self, settings: Settings, printer: Printer, clock: Callable[[], float] = time.time,
                 fail: tuple[str, ...] = ()):
        self.s, self.p = settings, printer
        self.fail = tuple(fail)  # any of "printer", "snmp", "cups": that source raises SourceError
        self.model = FAKE_DEVICES.get(printer.ip, {}).get("model", "Brother DCP-T730DW")
        self.clock = clock
        self._lock = threading.Lock()
        now = clock()
        self.counter = FINAL_COUNT
        self.jobs: list[Attrs] = []
        for ev in history_events(now):
            self.jobs.append(self._job(ev["job_id"], ev["name"], ev["user"], ev["ts"] - 5, ev["ts"] + 20, ev["pages"]))
        self._next_job = FIRST_JOB_ID + len(HISTORY)
        self._pending: list[tuple[float, int, int]] = []  # (done at, job id, pages)
        self.submitted: list[dict] = []  # every document sent through submit_document, for the tests
        self.sides_supported = ("one-sided", "two-sided-long-edge", "two-sided-short-edge")  # what the CUPS queue offers
        self.options_offered: dict = {  # what the fake CUPS queue offers, modelled on a real inkjet queue
            "media-supported": ["iso_a4_210x297mm", "om_a-4-borderless_210x297mm", "iso_a5_148x210mm", "na_index-4x6_4x6in",
                                "oe_4x-6-borderless_4x6in", "na_5x7_5x7in", "na_letter_8.5x11in", "custom_215x345mm_215x345mm"],
            "media-default": ["iso_a4_210x297mm"],
            "media-type-supported": ["stationery", "photographic-glossy", "stationery-inkjet", "com.brother-bp-71"],
            "print-quality-supported": [3, 4, 5], "print-quality-default": [4],
            "printer-resolution-supported": [(300, 300, "dpi")], "printer-resolution-default": [(300, 300, "dpi")],
            "print-scaling-supported": ["auto", "auto-fit", "fill", "fit", "none"],
            "media-left-margin-supported": [300, 0], "media-right-margin-supported": [300, 0],
            "media-top-margin-supported": [300, 0, 1200], "media-bottom-margin-supported": [300, 0, 1200],
            "copies-supported": [(1, 9999)],
        }
        self.printer_sides = ("one-sided", "two-sided-long-edge", "two-sided-short-edge")  # what the printer itself can do (an automatic duplexer?)

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
        if what in self.fail:
            raise SourceError(source, message, "This is a simulated failure.")

    def _need_queue(self) -> None:
        if not self.p.cups_queue:
            raise SourceError("cups", "No CUPS queue is set for this printer.",
                              "Open the Printers page, edit this printer and choose its queue.")

    def _tick(self) -> None:
        now = self.clock()
        for done, job_id, pages in [p for p in self._pending if p[0] <= now]:
            self._pending.remove((done, job_id, pages))
            self.counter += pages
            for j in self.jobs:
                if j["job-id"][0] == job_id:
                    j.update({"job-state": [9], "job-state-reasons": ["job-completed-successfully"],
                              "time-at-completed": [int(done)], "job-media-sheets-completed": [pages]})

    # ---- PrinterBackend
    def printer_info(self) -> Attrs:
        self._fail("printer", "printer", f"The printer ({self.p.ip}) did not answer in time.")
        with self._lock:
            self._tick()
            busy = bool(self._pending)
        return {
            "printer-name": [self.model.split()[-1]], "printer-info": [self.model],
            "printer-make-and-model": [self.model], "printer-location": ["Study"],
            "printer-state": [4 if busy else 3], "printer-state-reasons": ["none"],
            "printer-is-accepting-jobs": [True],
            "printer-firmware-string-version": ["Ver.U"], "printer-up-time": [int(self.clock() % 10_000_000) + 3600],
            "printer-more-info": [f"http://{self.p.ip}/net/net/airprint.html"],
            "marker-names": ["M", "C", "Y", "BK"], "marker-levels": [94, 95, 94, 95],
            "marker-colors": ["#FF00FF", "#00FFFF", "#FFFF00", "#000000"],
            "marker-types": ["ink-cartridge"] * 4, "marker-low-levels": [10] * 4, "marker-high-levels": [100] * 4,
            "media-ready": ["iso_a4_210x297mm"], "media-default": ["iso_a4_210x297mm"],
            "sides-default": ["one-sided"], "print-color-mode-default": ["color"],
            "sides-supported": [x for x in ("one-sided", "two-sided-long-edge", "two-sided-short-edge") if x in self.printer_sides],
        }

    def read_counter(self) -> int:
        self._fail("snmp", "snmp", f"The printer's SNMP service ({self.p.ip}:{self.p.snmp_port}) did not answer in time.")
        with self._lock:
            self._tick()
            return self.counter

    def queue_info(self) -> Attrs:
        self._need_queue()
        self._fail("cups", "cups", f"CUPS ({self.p.cups_url}) refused the connection.")
        with self._lock:
            self._tick()
            busy = bool(self._pending)
        return {
            "printer-name": [self.p.cups_queue], "printer-info": [self.model],
            "printer-make-and-model": [self.model], "printer-location": ["Study"],
            "printer-state": [4 if busy else 3], "printer-state-reasons": ["none"],
            "printer-is-accepting-jobs": [True], "queued-job-count": [len(self._pending)],
            "device-uri": [self.p.ipp_endpoint],
            "sides-supported": [x for x in ("one-sided", "two-sided-long-edge", "two-sided-short-edge") if x in self.sides_supported],
            "printer-uri-supported": [f"ipp://localhost/printers/{self.p.cups_queue}"],
        }

    def list_jobs(self) -> list[Attrs]:
        self._need_queue()
        self._fail("cups", "cups", f"CUPS ({self.p.cups_url}) refused the connection.")
        with self._lock:
            self._tick()
            return [dict(j) for j in self.jobs]

    def print_options(self) -> Attrs:
        self._need_queue()
        self._fail("cups", "cups", f"CUPS ({self.p.cups_url}) refused the connection.")
        return dict(self.options_offered)

    def submit_document(self, job_name: str, document: bytes, user: str, mime: str = "application/postscript",
                        sides: str | None = None, options: list | None = None) -> int:
        self._need_queue()
        self._fail("cups", "cups", f"CUPS ({self.p.cups_url}) refused the connection.")
        if mime == "application/postscript" and not document.startswith(b"%!PS"):
            raise SourceError("cups", "The document is not PostScript.")
        if mime not in ("application/postscript", "application/pdf", "image/png", "image/jpeg"):
            raise SourceError("cups", f"CUPS can't print {mime}.")
        pages = count_pdf_pages(document) if mime == "application/pdf" else 1
        with self._lock:
            now = self.clock()
            job_id, self._next_job = self._next_job, self._next_job + 1
            self.jobs.append(self._job(job_id, job_name, user, now, None, 0))
            self.submitted.append({"job_id": job_id, "name": job_name, "mime": mime, "size": len(document), "pages": pages, "sides": sides, "document": document,
                                   "options": {name: value for _tag, name, value in options or []}})
            self._pending.append((now + PRINT_SECONDS, job_id, pages))
            return job_id


class FakeAccess:
    """A pretend network: two printers can be found, every other address times out."""

    def __init__(self, settings: Settings, clock: Callable[[], float] | None = None, delay: float = 0.0,
                 fail: tuple[str, ...] = ()):
        self.s, self.clock, self.delay, self.fail = settings, clock or time.time, delay, tuple(fail)
        self._backends: dict[str, tuple[Printer, FakePrinter]] = {}

    def backend(self, printer: Printer) -> FakePrinter:
        cached = self._backends.get(printer.id)
        if cached and cached[0] == printer:
            return cached[1]
        backend = FakePrinter(self.s, printer, self.clock, self.fail)
        self._backends[printer.id] = (printer, backend)
        return backend

    def discover(self, cups_url: str | None = None) -> dict:
        time.sleep(self.delay * 0.8)
        found = []
        for ip, dev in FAKE_DEVICES.items():
            c = {"name": dev["model"], "model": dev["model"], "ip": ip, "ipp_url": f"ipp://{ip}/{dev['rp']}"}
            if dev["queue"] == "T730":  # only the Brother is already set up in CUPS
                c.update(queue=dev["queue"], cups_url=cups_url or self.s.cups_url, sources=["cups", "mdns"],
                         device_uri=c["ipp_url"])
            else:
                c.update(sources=["mdns"])
            found.append(c)
        return {"candidates": found, "notes": []}

    def probe(self, draft: dict) -> dict:
        clean, errors = validate({**draft, "name": draft.get("name") or "probe"})
        errors.pop("name", None)
        if errors:
            return {"ok": False, "errors": errors}
        time.sleep(self.delay * 0.5)
        dev = FAKE_DEVICES.get(clean["ip"])
        queues = [{"name": d["queue"], "info": d["model"], "device_uri": f"ipp://{ip}/{d['rp']}"}
                  for ip, d in FAKE_DEVICES.items() if d["queue"] == "T730"]
        same = [q["name"] for q in queues if clean["ip"] in q["device_uri"]]
        suggested = clean["cups_queue"] if clean["cups_queue"] in [q["name"] for q in queues] else (same[0] if same else "")
        if dev:
            ipp = {"ok": True, "ipp_url": f"ipp://{clean['ip']}/{dev['rp']}", "model": dev["model"],
                   "name": dev["model"], "state": "Idle", "inks": 4}
            snmp = {"ok": True, "value": FINAL_COUNT}
        else:
            timeout = {"source": "printer", "message": f"The printer ({clean['ip']}) did not answer in time.",
                       "hint": "Is it powered on and on the network?"}
            ipp = {"ok": False, "error": timeout}
            snmp = {"ok": False, "error": {**timeout, "source": "snmp", "message": f"The printer's SNMP service ({clean['ip']}:161) did not answer in time."}}
        names = [q["name"] for q in queues]
        cups = {"ok": not clean["cups_queue"] or clean["cups_queue"] in names, "queues": queues, "suggested": suggested}
        if not cups["ok"]:
            cups["error"] = {"source": "cups", "message": f"CUPS has no queue named '{clean['cups_queue']}'.",
                             "hint": "Pick one of the queues below."}
        return {"ok": True, "ipp": ipp, "snmp": snmp, "cups": cups,
                "suggested_name": ipp.get("name") or "", "ipp_url": ipp.get("ipp_url", "")}


def seed_history(store: Store, now: float) -> None:
    """Give a fresh maintenance store the scripted past (the fake CUPS has the same jobs).
    Host-timer checks are not inserted: the tracker imports them from the fake job list."""
    if store.activity_count() or store.get("last_count") is not None:
        return
    events = history_events(now)
    first_ts = events[0]["ts"]
    store.put("first_seen_ts", first_ts)
    store.put("last_count", FINAL_COUNT)
    store.put("last_count_ts", now)
    store.add_activity(first_ts - 3600, 0, events[0]["count_after"] - events[0]["pages"], "baseline",
                       note="Test data")
    previous = first_ts - 3600
    for ev in events:
        kind = "external" if ev["kind"] == "external" else "nozzle"
        store.add_activity(ev["ts"], ev["pages"], ev["count_after"], kind, since_ts=previous)
        previous = ev["ts"]
        if ev["kind"] == "manual":
            store.add_check(ev["ts"], "manual", "submitted", ev["job_id"], claimed_ts=ev["ts"])
