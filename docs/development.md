# Development

[← Back to the README](../README.md)

## Run it locally

```bash
make dev        # creates .venv on first run, serves http://localhost:8095 (data in .local/data)
make test       # runs the test suite
```

Without `make`:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
DATA_DIR=.local/data CONFIG_DIR=config .venv/bin/python -m app
```

It talks to your real network: the wizard discovers printers on the LAN and the cards read live data. Opening the
dashboard, the wizard and the Printers page only ever *read* from printers. The one thing that can print is the automatic nozzle check, and only for a printer where
you switched it on (it is off by default). To start over,
delete `.local/data`.

The tests never touch a real device or print anything: they use fakes in `tests/fakes.py` (a fake printer, CUPS
server and network, with realistic data).

## Adding a tool

A tool is a folder under `app/tools/` that the app discovers at start-up. It owns its API routes, its tables
and its UI, and a tool that fails to load shows an error card instead of taking the dashboard down.

```bash
cp -r app/tools/_template app/tools/scan     # the folder name is the tool's id
```

```
app/tools/scan/
  __init__.py     create(ctx) -> Tool(...)    the module's entry point
  ui/ui.js        export function mount(root, kit)
  ui/ui.css       optional, loaded automatically
```

**Each utility is a tab.** The dashboard shows a tab bar (the address becomes `#<tool>` or `#<tool>/<tab>`). A new tool
is a new tab with no other change. A tool can also declare several tabs of its own, as Printer Maintenance does with
**Overview** (printer, ink, queue, recent prints) and **Nozzle Care**:

```python
from app.core.tool import Tool, ToolTab
Tool(id="maintenance", ..., tabs=[ToolTab("overview", "Overview", "printer"), ToolTab("nozzle", "Nozzle Care", "droplet")])
```

The shell calls `mount(root, kit)` once per tab, the first time it is opened, and `kit.tabId` says which one. Keep
each tab in its own module (see `app/tools/maintenance/ui/`: `ui.js` picks `overview.js` or `nozzle.js`, and
`shared.js` holds what they share), so a new feature is a new file plus one `ToolTab`. Tabs of tools that set
`needs_printer` appear once a printer exists, and reload when the printer selection changes.

**Backend** (`__init__.py`). `create(ctx)` receives `ctx.settings`, `ctx.db`, `ctx.printers` (the printer registry),
`ctx.backend_for(printer)` (IPP/SNMP/CUPS access for one printer) and `ctx.clock`, and returns a `Tool`:

```python
from pathlib import Path
from flask import Blueprint, jsonify
from app.core.tool import Tool

TOOL_ID = Path(__file__).parent.name

def create(ctx):
    ctx.db.migrate(TOOL_ID, ["CREATE TABLE IF NOT EXISTS scan_log (ts REAL, note TEXT)"])  # your own tables
    bp = Blueprint(TOOL_ID, __name__)                  # mounted at /api/scan/

    @bp.get("/hello")
    def hello():
        return jsonify(ok=True, data={"message": "hi"})   # {ok, data} / {ok: false, error: {message, hint}}

    return Tool(id=TOOL_ID, title="Scanner", description="Scan to PDF", icon="file",
                blueprint=bp, ui_dir=Path(__file__).parent / "ui",
                start=None,          # optional callable to launch background threads
                order=20,            # tab order
                needs_printer=True)  # hidden until a printer exists; UI gets a printer selector
```

- State-changing routes are CSRF-protected automatically; just call them with `kit.api`.
- A tool about printers keeps its data **per printer** (a `printer_id` column) and puts the id in its routes, like `/api/maintenance/<printer id>/nozzle`. Register `ctx.printers.on_delete(fn)` to remove that data when a printer is removed. See `app/tools/maintenance/` (`store.py`, `runtimes.py`). Do background work per printer on its own thread, as `poller.py` does, so one printer's trouble can't stall the rest.
- Return `ok: false` with an `error` for upstream trouble (printer unreachable) so only that card shows an error. Raise `app.core.errors.SourceError("source", "friendly message", "hint")` for that.
- Static files in `ui/` are served at `/tools/scan/`.

**Frontend** (`ui/ui.js`). `mount(root, kit)` is the whole contract. `kit` has `api(path, {method, body})` (never throws, resolves to `{ok, data, error}`),
`h()` for building elements, `icon()`, `fmt` (ago/abs/days), `ui` (`card`, `badge`, `gauge`, `stat`, `errorBox`, `skeleton`),
`toast()`, `poll(fn, ms)` (runs while the tab is on screen), `toolId`, `tabId`, and `printer` (`printer.current` is the selected printer, `printer.subscribe(fn)` fires when the selection or its settings change; reload your cards there). See `app/tools/maintenance/ui/ui.js` for a full example.
Icons come from `app/static/lib/icons.js`; add a path there if you need a new one.

**Tests.** The `make_app` fixture in `tests/conftest.py` builds the app with a fake printer (`tests/fakes.py`) and a controllable clock.

## Project layout

```
app/
  factory.py            wiring: settings, db, security, tool discovery
  core/                 db, csrf, rate-limit rule, links.yml loader, tool registry, printer registry + API, errors
  printer/              ipp.py, snmp.py, mdns.py (stdlib clients), live.py, discovery.py (wizard: find + probe)
  static/               index.html, app.css, app.js, printers.js (wizard + admin), lib/ (dom, api, ui kit, icons)
  tools/maintenance/    idle.py, schedule.py, pages.py, tracker.py, store.py, runtimes.py, normalize.py, service.py, api.py, ui/ (overview.js, nozzle.js, pages.js, shared.js)
  tools/photosheet/     layout.py (sheet maths), pdfsheet.py (JPEG reader + PDF writer), capabilities.py (what a CUPS queue offers), ui/
  tools/utilities/      catalog.yml + loader, cards ui
  tools/_template/      copy me
config/                 links.yml (starter) and logos/
tests/                  fakes.py (fake printer/CUPS/network) and pytest (config, idle logic, attribution, IPP/SNMP/mDNS wire formats, printer registry and API, CSRF, rate limit)
Dockerfile  compose.example.yml  .github/workflows/build.yml
```
