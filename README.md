# panda-print

A small, self-hosted dashboard that is the front door for everything printer-related at home.
It ships with one tool, **Printer Maintenance** (status, ink gauges, lifetime page counter, CUPS queue,
recent jobs, and a nozzle-check tracker with a "Print nozzle check now" button), plus a **launcher** grid for
external tools. More tools plug in as self-contained modules.

Built for a Brother DCP-T730DW behind CUPS on a Raspberry Pi, but nothing is hard-wired: every address and
path is an environment variable.

- Python 3.12 + Flask on waitress, no build step, no CDN, no runtime internet. About 30-40 MB RAM.
- Light and dark themes (follows the OS, with a toggle), mobile friendly.
- One port (default 8095). No login: it expects an SSO reverse proxy in front. State-changing requests are CSRF-protected and printing is rate-limited.

## Try it on your laptop (mock mode)

`MOCK=1` replaces the printer and CUPS with a fake that has realistic data, so the whole UI works offline.
Printing a nozzle check in mock mode creates a fake job and bumps the fake page counter a few seconds later.

```bash
make dev        # creates .venv on first run, serves http://localhost:8095
make test       # runs the test suite
```

Without `make`:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
MOCK=1 CONFIG_DIR=config .venv/bin/python -m app
```

`MOCK_FAIL=printer,snmp,cups` makes those fake sources fail so you can see the error cards. Mock data lives in
memory and is regenerated on every start (`MOCK_PERSIST=1` keeps it in `DATA_DIR`).

## Run it on the Pi (Docker)

Every push to `main` builds a multi-arch (amd64 + arm64) image and pushes it to
`ghcr.io/tusharkaurani/panda-print` (see `.github/workflows/build.yml`; tests must pass first).

On the Pi, make a folder holding `compose.yml` (from `compose.example.yml`) and a `config/` folder (copy
`config/links.yml` from this repo and edit the URLs; add logos under `config/logos/`), then:

```bash
docker compose up -d
```

`compose.example.yml` is the whole hosting recipe: host networking (so `127.0.0.1:631` is CUPS and the printer is
reachable on the LAN), non-root uid 1000, read-only root filesystem, dropped capabilities, a 128 MB memory cap, a
named volume for `/data`, `./config` mounted read-only, and the host timer's state folder mounted read-only.
The image has its own healthcheck (`python -m app.healthcheck`).

Notes:

- New GHCR packages are private by default. Either make the package public in GitHub (Packages, Package settings), or `docker login ghcr.io` on the Pi with a token that has `read:packages`.
- Without host networking, publish the port instead (`ports: ["8095:8095"]`) and set `CUPS_URL=http://192.168.1.10:631`. CUPS must then allow requests from the container's network.
- Behind a reverse proxy the app only needs `X-Forwarded-Host`/`Host` to match the browser's `Origin` (the usual case). If your proxy rewrites `Host`, set `TRUSTED_ORIGINS=https://print.example.lan`.
- If the proxy runs on the same Pi with host networking, set `HOST=127.0.0.1` so the dashboard isn't also exposed on the LAN without SSO.

## Configuration

All settings are environment variables; see [.env.example](.env.example) for the full list with defaults.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT`, `HOST` | `8095`, `0.0.0.0` | Where the app listens (one port) |
| `PRINTER_IP` | `192.168.1.18` | Printer address (IPP at `ipp://<ip>/ipp/print` unless `PRINTER_IPP_URL` is set; SNMP on `SNMP_PORT`) |
| `SNMP_COMMUNITY`, `SNMP_COUNTER_OID` | `public`, `1.3.6.1.2.1.43.10.2.1.4.1.1` | Lifetime page counter (prtMarkerLifeCount) |
| `CUPS_URL`, `CUPS_QUEUE` | `http://127.0.0.1:631`, `T730` | CUPS server and queue |
| `IDLE_DAYS` | `7` | Idle days before the host timer prints a check (used for "next check due") |
| `POLL_INTERVAL` | `300` | Seconds between counter reads and CUPS job syncs |
| `HOST_STATE_FILE` | `/host-state/state` | The host timer's `"<count> <epoch>"` file (optional, read-only) |
| `PRINT_COOLDOWN`, `PRINT_MAX_PER_HOUR` | `120`, `5` | Rate limit for manual nozzle checks |
| `DATA_DIR`, `CONFIG_DIR` | `./data`, `./config` (image: `/data`, `/config`) | SQLite history, and `links.yml` + `logos/` |
| `SECRET_KEY` | generated | CSRF signing key; if unset a random key is stored in `DATA_DIR/secret.key` (mode 0600) |

## Launcher links

Edit `config/links.yml` (it is commented). Each entry has `name`, `url`, `description`, `logo` and `new_tab`.
Changes appear on the page within about a minute, or on reload, with no restart and no rebuild.

- `logo:` is a file name in `config/logos/` (svg, png, jpg, webp, gif, ico), a full image URL, or a bundled generic icon (`builtin:printer`, `builtin:server`, `builtin:link`, `builtin:globe`). A missing or broken logo falls back to a coloured letter tile. Logos you drop into `config/logos/` are git-ignored, and no third-party brand logos ship with the repo.
- A mistake never crashes anything. A bad entry is skipped with a note under "Quick links"; a file-level problem (for example a YAML syntax error) shows a message with the line number and keeps showing the last working tiles.

## How the nozzle tracking works

SNMP gives only the printer's current lifetime page count and IPP `Get-Jobs` only lists queued jobs, so the app
keeps its own history in SQLite:

1. **Counter polling.** Every `POLL_INTERVAL` seconds (and when a page is viewed and the last read is older than 30 s) the app reads the counter. A rise is recorded with the time it was noticed and the previous read time, so times are accurate to roughly the poll interval. This is more precise than the host timer's state file, which is stamped when its daily run *noticed* the change.
2. **Telling nozzle checks from other printing.** The app matches counter rises with nozzle checks it knows about: ones it submitted itself, and ones the host timer printed (found in CUPS's completed jobs: job name containing `nozzle-check`, i.e. `NOZZLE_JOB_PATTERN`, not submitted by this app). Anything left over is "someone printed". That is how *last print by someone*, *last automated check* and *last manual run* stay separate.
3. **Idle clock.** Like the host script, a nozzle check counts as a print and restarts the idle clock, so *next automated check due* is `last print of any kind + IDLE_DAYS`.
4. **First run.** If `HOST_STATE_FILE` is mounted and its count equals the printer's current count, the idle clock starts from its timestamp (a lower bound: it can lag the real print by up to a day); otherwise from the first reading. Until a print is seen the card says "at least".

The idle rule lives in `app/tools/maintenance/idle.py` with no I/O: `decide()` is the host script's `main()` minus side effects, and `parse_host_state()` reads its state format. That is the piece to reuse if you later move the scheduler into the app. **The app never prints automatically**; the host timer remains the only scheduler. A manual print uses IPP `Print-Job` straight to CUPS (no `lp` needed in the container).

## Security model

- No login. Put an SSO reverse proxy in front.
- Every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/` needs an `X-CSRF-Token` header that matches an HttpOnly, `SameSite=Strict` cookie and carries a valid HMAC signature (`GET /api/csrf` issues both). If the browser sends `Origin` or `Referer`, its host must match the request host, `X-Forwarded-Host`, or `TRUSTED_ORIGINS`.
- Manual printing is limited by `PRINT_COOLDOWN` and `PRINT_MAX_PER_HOUR`, counted from the database so restarts don't reset it (answers `429` with `Retry-After`).
- Strict Content-Security-Policy (no inline script or style, no external hosts except images you point links at), and logo files are served sandboxed.
- The SNMP community and addresses are configuration, not secrets, but nothing sensitive is committed: `.env`, `data/`, `secret.key` and your logos are git-ignored.

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

**Backend** (`__init__.py`). `create(ctx)` receives `ctx.settings`, `ctx.db`, `ctx.printer` (the same IPP/SNMP/CUPS
access, or the mock) and `ctx.clock`, and returns a `Tool`:

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
                order=20)            # tab order
```

- State-changing routes are CSRF-protected automatically; just call them with `kit.api`.
- Return `ok: false` with an `error` for upstream trouble (printer unreachable) so only that card shows an error. Raise `app.core.errors.SourceError("source", "friendly message", "hint")` for that.
- Static files in `ui/` are served at `/tools/scan/`.

**Frontend** (`ui/ui.js`). `mount(root, kit)` is the whole contract. `kit` has `api(path, {method, body})` (never throws, resolves to `{ok, data, error}`),
`h()` for building elements, `icon()`, `fmt` (ago/abs/days), `ui` (`card`, `badge`, `gauge`, `stat`, `errorBox`, `skeleton`),
`toast()`, `poll(fn, ms)` (runs while the tool is on screen) and `toolId`. See `app/tools/maintenance/ui/ui.js` for a full example.
When there is more than one tool the dashboard shows tabs (`#<id>` in the URL). Icons come from `app/static/lib/icons.js`; add a path there if you need a new one.

**Tests.** The `make_app` fixture in `tests/conftest.py` builds the app with the mock printer and a controllable clock.

## Project layout

```
app/
  factory.py            wiring: settings, db, security, tool discovery
  core/                 db, csrf, rate-limit rule, links.yml loader, tool registry, errors
  printer/              ipp.py, snmp.py (stdlib clients), live.py, mock.py (the PrinterBackend interface is in __init__.py)
  static/               index.html, app.css, app.js, lib/ (dom, api, ui kit, icons)
  tools/maintenance/    idle.py, tracker.py, store.py, normalize.py, service.py, api.py, ui/
  tools/_template/      copy me
config/                 links.yml (starter) and logos/
tests/                  pytest (config parsing, idle logic, attribution, IPP/SNMP wire formats, API, CSRF, rate limit)
Dockerfile  compose.example.yml  .github/workflows/build.yml
```

## Known limits

- IPP/SNMP were written against the protocol specs and the facts about this printer and tested with in-process fakes; they have not yet been run against the real DCP-T730DW. The first thing to check on the Pi is the three cards loading.
- If CUPS is unreachable while the host timer prints a check, that page is counted as "someone printed" (the counter can't tell the difference).
- The page counter is shared by all prints, so a manual check that never prints (for example the printer is offline) can be mistaken for a later print within `ATTRIBUTION_WINDOW` (30 minutes by default).
- Ink levels are the printer's own estimates.
