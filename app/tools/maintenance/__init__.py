"""Tool: Printer Maintenance (status, ink, queue, nozzle checks) for every printer.

This package is the reference example of a tool module; see the README.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.core.context import AppContext
from app.core.tool import Tool, ToolTab
from app.tools.maintenance import automation, store
from app.tools.maintenance.api import build_blueprint
from app.tools.maintenance.runtimes import Runtimes

log = logging.getLogger(__name__)


def create(ctx: AppContext) -> Tool:
    store.migrate(ctx.db)
    runtimes = Runtimes(ctx)

    def poll_forever() -> None:
        stop = threading.Event()
        stop.wait(2)  # let the server come up first
        while True:
            now = ctx.clock()
            for rt in runtimes.all():  # every printer, including ones added while running
                try:
                    last = rt.store.get("last_poll_ts")
                    if automation.poll_due(float(last) if last else None, rt.tracker.automation().check_minutes, now):
                        rt.tracker.poll()
                except Exception:
                    log.exception("counter poll failed for %s", rt.printer.name)
            stop.wait(15)  # each printer has its own interval (Automation card), so look often and poll when due

    def start() -> None:
        threading.Thread(target=poll_forever, name="maintenance-poller", daemon=True).start()

    return Tool(
        id="maintenance",
        title="Printer Maintenance",
        description="Status, ink levels, queue and nozzle checks",
        icon="wrench",
        blueprint=build_blueprint(runtimes, ctx.clock),
        ui_dir=Path(__file__).parent / "ui",
        start=start,
        order=10,
        needs_printer=True,
        # Nozzle checks get their own tab so they can grow; new utilities can be added the same way
        tabs=[ToolTab("overview", "Overview", "printer"), ToolTab("nozzle", "Nozzle Check", "droplet")],
    )
