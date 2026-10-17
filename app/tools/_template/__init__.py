"""Starter for a new tool. See "Adding a tool" in the README.

    cp -r app/tools/_template app/tools/<your-id>

The folder name becomes the tool's id, its API prefix (/api/<id>/) and its asset
prefix (/tools/<id>/). Folders starting with "_" are never loaded, so this one is inert.
"""
from pathlib import Path

from flask import Blueprint, jsonify

from app.core.context import AppContext
from app.core.tool import Tool

TOOL_ID = Path(__file__).parent.name


def create(ctx: AppContext) -> Tool:
    # ctx.settings: env-derived settings   ctx.db: SQLite (see db.migrate)
    # ctx.printers: the printers added in the UI   ctx.backend_for(printer): IPP/SNMP/CUPS access
    # ctx.clock: time.time, overridable in tests
    bp = Blueprint(TOOL_ID, __name__)

    @bp.get("/hello")
    def hello():
        return jsonify(ok=True, data={"message": "Hello from a new tool"})

    # POST/PUT/DELETE routes are CSRF-protected automatically: call them from the UI
    # with kit.api(path, {method: 'POST', body}).

    return Tool(
        id=TOOL_ID,
        title="My tool",
        description="One line about what it does",
        icon="info",  # any name from app/static/lib/icons.js
        blueprint=bp,
        ui_dir=Path(__file__).parent / "ui",
        order=100,  # tab order when there are several tools
        needs_printer=False,  # True: hidden until a printer exists, and the UI gets kit.printer (see README)
    )
