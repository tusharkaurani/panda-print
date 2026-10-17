"""What the core hands to every tool's create()."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from app.core.db import Database
from app.core.printers import Printer, PrinterRegistry
from app.printer import PrinterAccess, PrinterBackend
from app.settings import Settings


@dataclass
class AppContext:
    settings: Settings
    db: Database
    printers: PrinterRegistry  # the printers added in the UI; tools never hard-code one
    access: PrinterAccess  # network access: backends, discovery, probing
    clock: Callable[[], float] = field(default=time.time)

    def backend_for(self, printer: Printer) -> PrinterBackend:
        """IPP/SNMP/CUPS access for one printer (or its mock)."""
        return self.access.backend(printer)
