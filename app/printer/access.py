"""Live access: real IPP/SNMP/CUPS plus discovery."""
from __future__ import annotations

from app.core.printers import Printer
from app.printer import discovery
from app.printer.live import LivePrinter
from app.settings import Settings


class LiveAccess:
    def __init__(self, settings: Settings):
        self.s = settings

    def backend(self, printer: Printer) -> LivePrinter:
        return LivePrinter(self.s, printer)

    def discover(self, cups_url: str | None = None) -> dict:
        return discovery.discover(self.s, cups_url)

    def probe(self, draft: dict) -> dict:
        return discovery.probe(self.s, draft)
