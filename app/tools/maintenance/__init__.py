"""Tool: Printer Maintenance (status, ink, queue, nozzle checks) for every printer.

This package is the reference example of a tool module; see docs/development.md.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.core.context import AppContext
from app.core.tool import Tool, ToolTab
from app.tools.maintenance import schedule, store
from app.tools.maintenance.poller import Poller
from app.tools.maintenance.pages import Library
from app.tools.maintenance.api import build_blueprint
from app.tools.maintenance.runtimes import Runtimes

log = logging.getLogger(__name__)


def create(ctx: AppContext) -> Tool:
    store.migrate(ctx.db)
    log.info("nozzle check clock: %s", schedule.tz_label(ctx.settings.timezone))
    library = Library.from_settings(ctx.db, ctx.settings, ctx.clock)
    runtimes = Runtimes(ctx, library)

    poller = Poller(ctx, runtimes)

    def start() -> None:
        threading.Thread(target=poller.run_forever, name="maintenance-poller", daemon=True).start()

    return Tool(
        id="maintenance",
        title="Printer Maintenance",
        description="Status, ink levels, queue and nozzle checks",
        icon="wrench",
        blueprint=build_blueprint(runtimes, ctx.clock, library),
        ui_dir=Path(__file__).parent / "ui",
        start=start,
        order=10,
        needs_printer=True,
        # Nozzle checks get their own tab so they can grow; new utilities can be added the same way
        tabs=[ToolTab("overview", "Overview", "printer", "Printer status, ink levels, the print queue and recent prints"),
              ToolTab("nozzle", "Nozzle Care", "droplet", "When nozzle checks print, automatic printing and the pages it uses")],
    )
