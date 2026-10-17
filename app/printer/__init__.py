"""Printer I/O behind one interface, so the dashboard runs the same code against
the real printer + CUPS (LivePrinter). Tests substitute fakes (tests/fakes.py).

All methods raise app.core.errors.SourceError with a message fit for the UI.
IPP attributes come back as {name: [values]} exactly as the printer sent them.
"""
from __future__ import annotations

from typing import Any, Protocol

from app.core.printers import Printer
from app.settings import Settings

Attrs = dict[str, list[Any]]


class PrinterBackend(Protocol):
    def printer_info(self) -> Attrs:
        """IPP Get-Printer-Attributes against the printer itself."""

    def read_counter(self) -> int:
        """Lifetime page counter over SNMP (prtMarkerLifeCount)."""

    def queue_info(self) -> Attrs:
        """The CUPS queue's attributes (IPP Get-Printers, filtered to our queue)."""

    def list_jobs(self) -> list[Attrs]:
        """CUPS jobs, queued and completed."""

    def submit_document(self, job_name: str, document: bytes, user: str, mime: str = "application/postscript") -> int:
        """IPP Print-Job of a document (PostScript, PDF, PNG or JPEG) to the CUPS queue; returns the job id."""


class PrinterAccess(Protocol):
    """Everything that touches the network on behalf of printers (the real one, or a fake in tests)."""

    def backend(self, printer: Printer) -> PrinterBackend: ...

    def discover(self, cups_url: str | None = None) -> dict:
        """{'candidates': [...], 'notes': [...]}: printers found in CUPS and on the network."""

    def probe(self, draft: dict) -> dict:
        """Check a not-yet-saved printer config: IPP, SNMP and CUPS results."""


def make_access(settings: Settings) -> PrinterAccess:
    from app.printer.access import LiveAccess
    return LiveAccess(settings)
