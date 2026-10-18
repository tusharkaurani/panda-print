"""Opens each tool's page under node, against recorded answers of the real API, and does what a person would do next.

It catches the kind of break a unit test of the API never sees: a tab that throws while it opens (so a dropdown stays
empty), a stray "false" or "null" printed in the page, a control that no longer works. It needs node; without it the
tests are skipped. The DOM is a small fake (tests/js/fakedom.mjs), so this checks that the page runs and builds what it
should, not how it looks: look at it in a browser for that."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import PID, csrf_headers

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def bundle(tmp_path: Path) -> Path:
    """The browser's view of the files: /static/lib/... and /tools/<id>/..., with absolute imports made relative."""
    out = tmp_path / "bundle"
    shutil.copytree(ROOT / "app/static/lib", out / "static/lib")
    for ui_dir in (ROOT / "app/tools").glob("*/ui"):
        if ui_dir.parent.name.startswith("_"):
            continue
        dest = out / "tools" / ui_dir.parent.name
        shutil.copytree(ui_dir, dest)
        for js in dest.glob("*.js"):
            text = js.read_text()
            js.write_text(re.sub(r"from '/tools/(\w+)/([\w.-]+)'", r"from '../\1/\2'", text))
    return out


def get(client, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.get_json())
    body = r.get_json()
    return body.get("data", body)


def fixtures(app, variant: str) -> dict:
    client = app.test_client()
    h = csrf_headers(client)
    ctx = app.extensions["panda"]["ctx"]
    backend = ctx.backend_for(ctx.printers.get(PID))
    if variant == "no-cups":
        client.put(f"/api/printers/{PID}", json={"cups_queue": ""}, headers=h)
    m = f"/api/maintenance/{PID}"
    if variant == "offline":
        get(client, f"{m}/printer")  # one good answer is remembered...
        backend.fail = ("printer", "snmp")  # ...then it goes off
        from app.tools.maintenance import service as svc
        client.application.extensions["panda"]["ctx"]  # (the live cache is bypassed below by asking again)
    fx = {}
    for name in ("printer", "queue", "nozzle", "automation", "rotation"):
        fx[f"GET /maintenance/PID/{name}"] = get(client, f"{m}/{name}")
    fx["GET /utilities/catalog"] = get(client, "/api/utilities/catalog")
    fx["GET /tools"] = get(client, "/api/tools")  # the hub lists the tools that live inside it
    fx["GET /photosheet/config?printer=no"] = get(client, "/api/photosheet/config")
    fx["GET /photosheet/config?printer=yes"] = get(client, f"/api/photosheet/config?printer={PID}")
    fx["GET /photosheet/presets"] = get(client, "/api/photosheet/presets")
    base = {"paper": {"width": 210, "height": 297}, "photo": {"width": 35, "height": 45}, "margin": 5, "gap": 2, "mode": "fill"}
    for n in (1, 2, 3):
        body = {**base, "images": [{"width": 700, "height": 900}] * n, "counts": [1] * n}
        r = client.post("/api/photosheet/layout", json=body, headers=h)
        fx[f"POST /photosheet/layout#{n}"] = r.get_json()["data"]
    return fx


def run(tmp_path, fx, tool, tab="-", scenario=None):
    (tmp_path / "fx.json").write_text(json.dumps(fx))
    cmd = ["node", str(ROOT / "tests/js/smoke.mjs"), str(bundle(tmp_path)), str(tmp_path / "fx.json"), tool, tab]
    if scenario:
        cmd.append(scenario)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    last = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "{}"
    try:
        result = json.loads(last)
    except ValueError:
        result = {"problems": [f"no result: {out.stdout[-500:]} {out.stderr[-800:]}"]}
    assert out.returncode == 0 and not result["problems"], "\n".join(result["problems"]) or out.stderr
    return result


@pytest.fixture(scope="module")
def make_app_module(tmp_path_factory):
    import dataclasses
    from app.factory import create_app
    from app.settings import Settings
    from conftest import Clock
    from fakes import FakeAccess
    from conftest import add_default_printer

    def build(variant="normal"):
        base = tmp_path_factory.mktemp(f"data-{variant}")
        (base / "config").mkdir()
        s = Settings(data_dir=base / "data", config_dir=base / "config", host_state_file=base / "hs" / "state", timezone="UTC",
                     print_cooldown=0, print_max_per_hour=1000)
        clock = Clock()
        app = create_app(s, access=FakeAccess(s, clock), start_background=False, clock=clock)
        add_default_printer(app, clock)
        return app
    return build


def test_the_photo_sheets_tab_opens_and_every_dropdown_has_choices(tmp_path, make_app_module):
    result = run(tmp_path, fixtures(make_app_module("normal"), "normal"), "photosheet")
    assert result["calls"] >= 3 and result["chips"] >= 1  # it asked for its choices and showed the presets


def test_the_photo_sheets_tab_works_with_several_photos(tmp_path, make_app_module):
    """Adds three photos, uses the steppers, the dividers, crop and fit, every part of the page, units and presets."""
    result = run(tmp_path, fixtures(make_app_module("normal"), "normal"), "photosheet", scenario="photos")
    assert result["chips"] >= 1


@pytest.mark.parametrize("variant", ["normal", "no-cups", "offline"])
@pytest.mark.parametrize("tab", ["overview", "nozzle"])
def test_the_printer_maintenance_tabs_open_in_every_state(tmp_path, make_app_module, variant, tab):
    run(tmp_path, fixtures(make_app_module(variant), variant), "maintenance", tab)


def test_the_print_utilities_tab_opens_with_the_built_in_tool_and_no_chatter(tmp_path, make_app_module):
    result = run(tmp_path, fixtures(make_app_module("normal"), "normal"), "utilities")
    text = result["text"]
    assert "Built in" in text and "Passport Sheet Maker" in text  # the photo tool is a card on this page
    assert "Photos & IDs" in text and "checked" not in text and "Sites change" not in text  # and the page carries no disclaimer text


def test_the_harness_does_catch_a_page_that_prints_false(tmp_path, make_app_module):
    """A check on the check: a deliberately broken tool must fail the smoke run."""
    b = bundle(tmp_path)
    broken = b / "tools" / "broken"
    broken.mkdir()
    (broken / "ui.js").write_text("export async function mount(root, kit) { root.replaceChildren(false, 'ok', null); "
                                  "const s = kit.h('select', {}); root.append(s); }\n")
    fx = tmp_path / "fx.json"
    fx.write_text("{}")
    out = subprocess.run(["node", str(ROOT / "tests/js/smoke.mjs"), str(b), str(fx), "broken", "-"], capture_output=True, text=True, timeout=60)
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert out.returncode == 1
    joined = " ".join(result["problems"])
    assert 'stray text "false"' in joined and 'stray text "null"' in joined and "empty dropdown" in joined


def test_the_harness_catches_a_tab_that_throws_while_opening(tmp_path):
    b = bundle(tmp_path)
    broken = b / "tools" / "broken2"
    broken.mkdir()
    (broken / "ui.js").write_text("export async function mount() { const x = undefined; return x[0]; }\n")
    fx = tmp_path / "fx.json"
    fx.write_text("{}")
    out = subprocess.run(["node", str(ROOT / "tests/js/smoke.mjs"), str(b), str(fx), "broken2", "-"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 1 and "mount threw" in out.stdout
