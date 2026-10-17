"""The background loop: for every printer, read the page counter when it is due and print the automatic check
when that is due. One printer's trouble (an unreachable address, a slow CUPS) must never delay another's, so
each printer's turn runs on its own worker thread, and a printer whose previous turn is still running is skipped."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from app.tools.maintenance import automation
from app.tools.maintenance.runtimes import Runtime, Runtimes

log = logging.getLogger(__name__)

TICK_SECONDS = 15  # each printer has its own interval (Automation card), so look often and poll when due


class Poller:
    def __init__(self, ctx, runtimes: Runtimes, workers: int = 8):
        self.ctx, self.runtimes = ctx, runtimes
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="maintenance-printer")
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def cycle(self) -> list[Future]:
        """Start one turn for every printer that isn't still busy with its last one."""
        futures = []
        for rt in self.runtimes.all():  # every printer, including ones added while running
            with self._lock:
                if rt.printer.id in self._busy:
                    continue
                self._busy.add(rt.printer.id)
            futures.append(self._pool.submit(self._turn, rt))
        return futures

    def _turn(self, rt: Runtime) -> None:
        pid = rt.printer.id
        try:
            if self.ctx.printers.get(pid) is None:
                return  # removed while we waited
            try:
                last = rt.store.get("last_poll_ts")
                if automation.poll_due(float(last) if last else None, rt.tracker.automation().check_minutes, self.ctx.clock()):
                    rt.tracker.poll()
            except Exception:
                log.exception("counter poll failed for %s", rt.printer.name)
            try:
                rt.service.run_schedule()  # prints the automatic check when it is due (only if switched on)
            except Exception:
                log.exception("automatic nozzle check failed for %s", rt.printer.name)
        finally:
            with self._lock:
                self._busy.discard(pid)

    def run_forever(self) -> None:
        stop = threading.Event()
        stop.wait(2)  # let the server come up first
        while True:
            try:
                self.cycle()
            except Exception:
                log.exception("poller cycle failed")
            stop.wait(TICK_SECONDS)
