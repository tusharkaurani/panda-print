"""One live set of objects (backend, store, tracker, service) per printer."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from app.core.context import AppContext
from app.core.printers import Printer
from app.tools.maintenance.service import Service
from app.tools.maintenance.store import Store
from app.tools.maintenance.tracker import Tracker


@dataclass
class Runtime:
    printer: Printer
    store: Store
    tracker: Tracker
    service: Service


class Runtimes:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._lock = threading.Lock()
        self._items: dict[str, Runtime] = {}
        ctx.printers.on_delete(self.forget)

    def get(self, printer_id: str) -> Runtime | None:
        """The runtime for a printer, rebuilt if its settings were edited; None if unknown."""
        printer = self.ctx.printers.get(printer_id)
        if printer is None:
            return None
        with self._lock:
            rt = self._items.get(printer_id)
            if rt is None or rt.printer != printer:
                rt = self._build(printer)
                self._items[printer_id] = rt
            return rt

    def _build(self, printer: Printer) -> Runtime:
        ctx = self.ctx
        backend = ctx.backend_for(printer)
        store = Store(ctx.db, printer.id)
        tracker = Tracker(ctx.settings, store, backend, ctx.clock, printer.host_state_file)
        return Runtime(printer, store, tracker, Service(ctx, printer, backend, store, tracker))

    def all(self) -> list[Runtime]:
        return [rt for p in self.ctx.printers.list() if (rt := self.get(p.id))]

    def forget(self, printer_id: str) -> None:
        """A printer was removed: drop its runtime and its history."""
        with self._lock:
            self._items.pop(printer_id, None)
        Store(self.ctx.db, printer_id).delete_all()
