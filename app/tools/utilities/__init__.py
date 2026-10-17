"""Tool: Print Utilities. Cards linking to free online tools for things worth printing, grouped by category."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify

from app.core.context import AppContext
from app.core.tool import Tool
from app.tools.utilities.catalog import Catalog

HERE = Path(__file__).parent


def create(ctx: AppContext) -> Tool:
    catalog = Catalog(HERE / "catalog.yml", ctx.settings.config_dir / "print-utilities.yml")
    bp = Blueprint("utilities", __name__)

    @bp.get("/catalog")
    def get_catalog():
        return jsonify(ok=True, data=catalog.get())

    return Tool(
        id="utilities",
        title="Print Utilities",
        description="Free online tools for photos, worksheets, colouring pages and more",
        icon="printer",
        blueprint=bp,
        ui_dir=HERE / "ui",
        order=20,
        needs_printer=False,
    )
