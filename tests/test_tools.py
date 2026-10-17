import importlib
import textwrap

import pytest

import app.tools as tools_pkg
from app.factory import create_app
from app.printer.mock import MockPrinter


def write_tool(root, name, body):
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent(body))


@pytest.fixture
def extra_tools(tmp_path, monkeypatch):
    """A scratch folder that discover() treats as part of app.tools."""
    root = tmp_path / "extra_tools"
    root.mkdir()
    monkeypatch.setattr(tools_pkg, "__path__", [*tools_pkg.__path__, str(root)])
    yield root
    import sys
    for name in [m for m in sys.modules if m.startswith("app.tools.") and m.split(".")[2] in {"fine", "broken", "mismatch"}]:
        del sys.modules[name]


def test_broken_tools_are_reported_and_do_not_stop_the_app(extra_tools, settings, clock):
    write_tool(extra_tools, "broken", "def create(ctx):\n    raise RuntimeError('boom')\n")
    write_tool(extra_tools, "mismatch", """
        from app.core.tool import Tool
        def create(ctx):
            return Tool(id='other', title='x', description='', icon='info')
    """)
    write_tool(extra_tools, "fine", """
        from flask import Blueprint, jsonify
        from app.core.tool import Tool
        def create(ctx):
            bp = Blueprint('fine', __name__)
            bp.get('/ping')(lambda: jsonify(ok=True, data='pong'))
            return Tool(id='fine', title='Fine', description='', icon='info', blueprint=bp, order=1)
    """)
    app = create_app(settings, printer=MockPrinter(settings, clock), start_background=False, clock=clock)
    c = app.test_client()
    tools = {t["id"]: t for t in c.get("/api/tools").get_json()["tools"]}
    assert "boom" in tools["broken"]["error"]
    assert "must equal its folder name" in tools["mismatch"]["error"]
    assert "error" not in tools["fine"] and "error" not in tools["maintenance"]
    assert c.get("/api/fine/ping").get_json()["data"] == "pong"  # new tool's API is mounted
    assert c.get("/api/maintenance/nozzle").get_json()["ok"] is True
    # tabs are ordered by Tool.order
    assert [t["id"] for t in c.get("/api/tools").get_json()["tools"]][:2] == ["fine", "maintenance"]


def test_template_is_a_working_tool_but_is_never_auto_loaded(settings, clock):
    app = create_app(settings, printer=MockPrinter(settings, clock), start_background=False, clock=clock)
    assert "_template" not in [t["id"] for t in app.test_client().get("/api/tools").get_json()["tools"]]

    # what copying the template does: the same code under another folder name
    template = importlib.import_module("app.tools._template")
    ctx = app.extensions["panda"]["ctx"]
    tool = template.create(ctx)
    assert tool.blueprint and (tool.ui_dir / "ui.js").is_file()
