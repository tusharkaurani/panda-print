"""Tool: Printer Maintenance (status, ink, queue, nozzle checks).

This package is the reference example of a tool module; see the README.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.core.context import AppContext
from app.core.tool import Tool
from app.tools.maintenance import mock_seed
from app.tools.maintenance.api import build_blueprint
from app.tools.maintenance.service import Service
from app.tools.maintenance.store import Store
from app.tools.maintenance.tracker import Tracker

log = logging.getLogger(__name__)


def create(ctx: AppContext) -> Tool:
    store = Store(ctx.db)
    tracker = Tracker(ctx.settings, store, ctx.printer, ctx.clock)
    if ctx.settings.mock:
        mock_seed.seed(store, ctx.clock())
    service = Service(ctx, store, tracker)

    def poll_forever() -> None:
        stop = threading.Event()
        stop.wait(2)  # let the server come up first
        while True:
            try:
                tracker.poll()
            except Exception:
                log.exception("counter poll failed")
            stop.wait(ctx.settings.poll_interval)

    def start() -> None:
        threading.Thread(target=poll_forever, name="maintenance-poller", daemon=True).start()

    return Tool(
        id="maintenance",
        title="Printer Maintenance",
        description="Status, ink levels, queue and nozzle checks",
        icon="wrench",
        blueprint=build_blueprint(service),
        ui_dir=Path(__file__).parent / "ui",
        start=start,
        order=10,
    )
