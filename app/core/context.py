"""What the core hands to every tool's create()."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from app.core.db import Database
from app.printer import PrinterBackend
from app.settings import Settings


@dataclass
class AppContext:
    settings: Settings
    db: Database
    printer: PrinterBackend
    clock: Callable[[], float] = field(default=time.time)
