"""The real thing: IPP to the printer and CUPS, SNMP to the printer."""
from __future__ import annotations

from urllib.parse import urlsplit

from app.core.errors import SourceError, explain
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
]


class LivePrinter:
    def __init__(self, settings: Settings):
        self.s = settings
        self.ipp = ipp.IppClient(settings.net_timeout)
        cups = urlsplit(settings.cups_url)
        self._cups_host = cups.hostname or "localhost"
        self._cups_port = cups.port or 631
        # The IPP URI names the queue; the HTTP URL is where we POST.
        self._queue_uri = f"ipp://{self._cups_host}:{self._cups_port}/printers/{settings.cups_queue}"
        self._queue_url = f"{settings.cups_url}/printers/{settings.cups_queue}"

    # -- the printer itself
    def printer_info(self) -> Attrs:
        target = f"the printer ({self.s.printer_ip})"
        try:
            resp = self.ipp.request(self.s.ipp_url, ipp.GET_PRINTER_ATTRIBUTES, self.s.ipp_url,
                                    [(ipp.KEYWORD, "requested-attributes", PRINTER_ATTRS)])
        except Exception as e:
            raise self._wrap("printer", target, e) from e
        groups = resp.of(ipp.PRINTER_ATTRS)
        if not groups:
            raise SourceError("printer", "The printer answered but returned no attributes.")
        return groups[0]

    def read_counter(self) -> int:
        try:
            return snmp.get_counter(self.s.printer_ip, self.s.snmp_counter_oid, self.s.snmp_community,
                                    self.s.snmp_port, self.s.net_timeout)
        except snmp.SnmpError as e:
            raise SourceError("snmp", f"Could not read the page counter: {e}.") from e
        except Exception as e:
            raise self._wrap("snmp", f"the printer's SNMP service ({self.s.printer_ip}:{self.s.snmp_port})", e) from e

    # -- CUPS
    def queue_info(self) -> Attrs:
        try:
            resp = self.ipp.request(self.s.cups_url + "/", ipp.CUPS_GET_PRINTERS, None,
                                    [(ipp.KEYWORD, "requested-attributes", QUEUE_ATTRS)])
        except Exception as e:
            raise self._wrap("cups", f"CUPS ({self.s.cups_url})", e) from e
        printers = resp.of(ipp.PRINTER_ATTRS)
        for p in printers:
            if p.get("printer-name", [None])[0] == self.s.cups_queue:
                return p
        names = ", ".join(sorted(str(p.get("printer-name", ["?"])[0]) for p in printers)) or "none"
        raise SourceError("cups", f"CUPS has no queue named '{self.s.cups_queue}' (found: {names}).",
                          "Set CUPS_QUEUE to one of those names.")

    def list_jobs(self) -> list[Attrs]:
        jobs: list[Attrs] = []
        for which in ("not-completed", "completed"):
            try:
                resp = self.ipp.request(self._queue_url, ipp.GET_JOBS, self._queue_uri, [
                    (ipp.KEYWORD, "which-jobs", which),
                    (ipp.KEYWORD, "requested-attributes", JOB_ATTRS),
                ])
            except Exception as e:
                raise self._wrap("cups", f"CUPS ({self.s.cups_url})", e) from e
            jobs.extend(resp.of(ipp.JOB_ATTRS))
        return jobs

    def submit_nozzle(self, job_name: str, document: bytes, user: str) -> int:
        try:
            resp = self.ipp.request(self._queue_url, ipp.PRINT_JOB, self._queue_uri, [
                (ipp.NAME, "requesting-user-name", user),
                (ipp.NAME, "job-name", job_name),
                (ipp.MIME_TYPE, "document-format", "application/postscript"),
            ], data=document)
        except Exception as e:
            raise self._wrap("cups", f"CUPS ({self.s.cups_url})", e) from e
        for g in resp.of(ipp.JOB_ATTRS):
            if "job-id" in g:
                return int(g["job-id"][0])
        raise SourceError("cups", "CUPS accepted the request but did not return a job id.")

    @staticmethod
    def _wrap(source: str, target: str, exc: Exception) -> SourceError:
        if isinstance(exc, ipp.IppError):
            hint = "Check CUPS_QUEUE and that the queue exists." if exc.status == 0x0406 else None
            return SourceError(source, f"{target}: {exc}", hint)
        return explain(source, target, exc)
