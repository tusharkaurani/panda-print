"""The real thing: IPP to the printer and CUPS, SNMP to the printer."""
from __future__ import annotations

from urllib.parse import urlsplit

from app.core.errors import SourceError, explain
from app.core.printers import Printer
from app.printer import Attrs, ipp, snmp
from app.settings import Settings

PRINTER_ATTRS = [
    "printer-name", "printer-info", "printer-location", "printer-make-and-model", "printer-state",
    "printer-state-reasons", "printer-state-message", "printer-is-accepting-jobs", "printer-uuid",
    "printer-firmware-string-version", "printer-firmware-name", "printer-more-info", "printer-up-time",
    "printer-device-id", "marker-names", "marker-levels", "marker-colors", "marker-types",
    "marker-low-levels", "marker-high-levels", "media-ready", "media-default", "sides-default",
    "print-color-mode-default", "printer-input-tray",
]
QUEUE_ATTRS = [
    "printer-name", "printer-info", "printer-location", "printer-make-and-model", "printer-state",
    "printer-state-reasons", "printer-state-message", "printer-is-accepting-jobs", "queued-job-count",
    "device-uri", "printer-uri-supported",
]
JOB_ATTRS = [
    "job-id", "job-name", "job-state", "job-state-reasons", "job-originating-user-name",
    "time-at-creation", "time-at-processing", "time-at-completed", "job-media-sheets-completed",
    "job-k-octets",
    # jobs submitted with `lp` can come back with no job-name; the file name is only here
    "document-name-supplied",
]


class LivePrinter:
    def __init__(self, settings: Settings, printer: Printer, timeout: float | None = None):
        self.s, self.p = settings, printer
        self.ipp = ipp.IppClient(timeout or settings.net_timeout)
        self.timeout = timeout or settings.net_timeout
        cups = urlsplit(printer.cups_url)
        self._cups_host = cups.hostname or "localhost"
        self._cups_port = cups.port or 631
        # The IPP URI names the queue; the HTTP URL is where we POST.
        self._queue_uri = f"ipp://{self._cups_host}:{self._cups_port}/printers/{printer.cups_queue}"
        self._queue_url = f"{printer.cups_url}/printers/{printer.cups_queue}"

    def _need_queue(self) -> None:
        if not self.p.cups_queue:
            raise SourceError("cups", "No CUPS queue is set for this printer.",
                              "Open the Printers page, edit this printer and choose its queue.")

    # -- the printer itself
    def printer_info(self) -> Attrs:
        target = f"the printer ({self.p.ip})"
        try:
            resp = self.ipp.request(self.p.ipp_endpoint, ipp.GET_PRINTER_ATTRIBUTES, self.p.ipp_endpoint,
                                    [(ipp.KEYWORD, "requested-attributes", PRINTER_ATTRS)])
        except Exception as e:
            raise self._wrap("printer", target, e) from e
        groups = resp.of(ipp.PRINTER_ATTRS)
        if not groups:
            raise SourceError("printer", "The printer answered but returned no attributes.")
        return groups[0]

    def read_counter(self) -> int:
        try:
            return snmp.get_counter(self.p.ip, self.p.counter_oid, self.p.snmp_community,
                                    self.p.snmp_port, self.timeout)
        except snmp.SnmpError as e:
            raise SourceError("snmp", f"Could not read the page counter: {e}.") from e
        except Exception as e:
            raise self._wrap("snmp", f"the printer's SNMP service ({self.p.ip}:{self.p.snmp_port})", e) from e

    # -- CUPS
    def cups_queues(self) -> list[Attrs]:
        """Every queue CUPS knows about (also used by discovery and the setup wizard)."""
        try:
            resp = self.ipp.request(self.p.cups_url + "/", ipp.CUPS_GET_PRINTERS, None,
                                    [(ipp.KEYWORD, "requested-attributes", QUEUE_ATTRS)])
        except Exception as e:
            raise self._wrap("cups", f"CUPS ({self.p.cups_url})", e) from e
        return resp.of(ipp.PRINTER_ATTRS)

    def queue_info(self) -> Attrs:
        self._need_queue()
        printers = self.cups_queues()
        for p in printers:
            if p.get("printer-name", [None])[0] == self.p.cups_queue:
                return p
        names = ", ".join(sorted(str(p.get("printer-name", ["?"])[0]) for p in printers)) or "none"
        raise SourceError("cups", f"CUPS has no queue named '{self.p.cups_queue}' (found: {names}).",
                          "Open the Printers page and pick one of those queues.")

    def list_jobs(self) -> list[Attrs]:
        self._need_queue()
        jobs: list[Attrs] = []
        for which in ("not-completed", "completed"):
            try:
                resp = self.ipp.request(self._queue_url, ipp.GET_JOBS, self._queue_uri, [
                    (ipp.KEYWORD, "which-jobs", which),
                    (ipp.KEYWORD, "requested-attributes", JOB_ATTRS),
                ])
            except Exception as e:
                raise self._wrap("cups", f"CUPS ({self.p.cups_url})", e) from e
            jobs.extend(resp.of(ipp.JOB_ATTRS))
        return jobs

    def submit_document(self, job_name: str, document: bytes, user: str, mime: str = "application/postscript") -> int:
        self._need_queue()
        # PDFs and pictures are scaled to fit the paper (CUPS converts them for the printer); PostScript is left alone
        job_attrs = [(ipp.KEYWORD, "print-scaling", "fit")] if mime != "application/postscript" else None
        try:
            resp = self.ipp.request(self._queue_url, ipp.PRINT_JOB, self._queue_uri, [
                (ipp.NAME, "requesting-user-name", user),
                (ipp.NAME, "job-name", job_name[:120]),
                (ipp.MIME_TYPE, "document-format", mime),
            ], data=document, job_attrs=job_attrs)
        except Exception as e:
            raise self._wrap("cups", f"CUPS ({self.p.cups_url})", e) from e
        for g in resp.of(ipp.JOB_ATTRS):
            if "job-id" in g:
                return int(g["job-id"][0])
        raise SourceError("cups", "CUPS accepted the request but did not return a job id.")

    @staticmethod
    def _wrap(source: str, target: str, exc: Exception) -> SourceError:
        if isinstance(exc, ipp.IppError):
            hint = "Check CUPS_QUEUE and that the queue exists." if exc.status == 0x0406 else None
            return SourceError(source, f"{target[:1].upper() + target[1:]}: {exc}", hint)
        return explain(source, target, exc)
