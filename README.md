# panda-print

A small, self-hosted dashboard that is the front door for everything printer-related at home.
It ships with one tool, **Printer Maintenance** (status, ink gauges, lifetime page counter, CUPS queue,
recent prints, and a nozzle-check tracker that can also print the nozzle check for you when a printer has sat idle), plus a **launcher** grid for
external tools. More tools plug in as self-contained modules.

Built for a Brother DCP-T730DW behind CUPS on a Raspberry Pi, but no printer is hard-wired: the first visit opens
a **setup wizard** that finds your printer (or asks for its IP), and a **Printers** page lets you add, edit, test and
remove printers later. Everything else (ports, paths, timeouts) is an environment variable.

- Python 3.12 + Flask on waitress, no build step, no CDN, no runtime internet. About 30-40 MB RAM.
- Light and dark themes (follows the OS, with a toggle), mobile friendly.
- One port (default 8095). No login: it expects an SSO reverse proxy in front. State-changing requests are CSRF-protected and printing is rate-limited.

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

## Setting up printers

Open the dashboard. With no printer added, you get the wizard, and nothing else is shown until one exists.

1. **Find your printer.** panda-print searches in two places at once: the queues CUPS already has (the printer's address is read from each queue's device URI) and the network itself (mDNS/Bonjour `_ipp._tcp`). Pick one, or type an IP address or host name (pasting `http://192.168.1.18/` works too).
2. **Connect and name.** It checks three things in parallel and shows each result: the printer over IPP (model, state), the page counter over SNMP, and the CUPS queue (it picks the queue whose device points at the same address). Name and queue are filled in for you; **Advanced settings** has the IPP address, SNMP community/port/OID, the CUPS server, and an optional nozzle-check timer state file. If something fails you can still save ("Add anyway") and fix it later.

The **printer dropdown** in the header lists your printers (the first is preselected) and ends with **Manage printers…**,
which opens the Printers page: every printer with a live check, and edit, re-test and remove. Removing a printer
deletes its nozzle-check history (not the printer or its CUPS queue). With zero printers the wizard comes back.

Discovery notes: mDNS needs multicast, which works with host networking (the compose example) but usually not in a
bridged Docker network; the wizard says so and CUPS plus manual entry still work. `PRINTER_IP` (optional) adds a first
printer automatically at start-up for unattended installs.

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
named volume for `/data`, `./config` mounted read-only, and, optionally, an old timer's state folder mounted read-only.
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
| `SNMP_COMMUNITY`, `SNMP_PORT`, `SNMP_COUNTER_OID` | `public`, `161`, `1.3.6.1.2.1.43.10.2.1.4.1.1` | Defaults prefilled for new printers (page counter is prtMarkerLifeCount) |
| `CUPS_URL` | `http://127.0.0.1:631` | Default CUPS server for new printers, and where discovery looks |
| `PRINTER_IP`, `PRINTER_IPP_URL`, `CUPS_QUEUE` | unset | Optional: add this printer automatically on first start if none exists |
| `IDLE_DAYS` | `7` | **Default** idle days before a nozzle check is due; each printer can override it in the Automation card |
| `POLL_INTERVAL` | `300` | **Default** seconds between counter reads and CUPS job syncs; overridable per printer |
| `HOST_STATE_FILE` | `/host-state/state` | The host timer's `"<count> <epoch>"` file; offered in the wizard (as that printer's timer state file) if it exists and no printer uses it yet |
| `NOZZLE_JOB_PATTERN`, `ATTRIBUTION_WINDOW` | `nozzle-check`, `1800` | **Defaults** for how the timer's check pages are recognised and matched to the counter; overridable per printer |
| `PRINT_COOLDOWN`, `PRINT_MAX_PER_HOUR` | `120`, `5` | Rate limit of the manual-print endpoint (see below) |
| `DATA_DIR`, `CONFIG_DIR` | `./data`, `./config` (image: `/data`, `/config`) | SQLite history, and `links.yml` + `logos/` |
| `SECRET_KEY` | generated | CSRF signing key; if unset a random key is stored in `DATA_DIR/secret.key` (mode 0600) |

## Launcher links

Edit `config/links.yml` (it is commented). Each entry has `name`, `url`, `description`, `logo`, `new_tab` and optionally
`printers`. Changes appear on the page within about a minute, or on reload, with no restart and no rebuild.

**Links follow the selected printer.** They sit under the printer you picked in the header dropdown, and any `name`,
`url` or `description` can use placeholders that are filled in from that printer, so the same file works for HP, Epson,
Canon, Brother or anything else, and for several printers at once:

| Placeholder | Filled with |
| --- | --- |
| `{ip}` | the printer's address (IPv6 is bracketed for you) |
| `{name}`, `{id}` | its name and short id |
| `{queue}` | its CUPS queue (a link using it is hidden for a printer with no queue) |
| `{cups_url}` | its CUPS server; a `127.0.0.1` CUPS is shown as the host you opened the dashboard on, so the link works from your laptop |

```yaml
links:
  - name: Printer Control Panel
    url: http://{ip}/                    # https://{ip}/ if the panel is HTTPS only
  - name: CUPS Dashboard
    url: "{cups_url}/printers/{queue}"   # quote a value that starts with a placeholder
  - name: Router
    url: http://192.168.1.1/             # no placeholder: shown for every printer
  - name: Ink shop
    url: https://example.com/inks
    printers: [Study printer]            # only for this printer (name or id)
```

- `logo:` is a file name in `config/logos/` (svg, png, jpg, webp, gif, ico), a full image URL, or a bundled generic icon (`builtin:printer`, `builtin:server`, `builtin:link`, `builtin:globe`). A missing or broken logo falls back to a coloured letter tile. Logos you drop into `config/logos/` are git-ignored, and no third-party brand logos ship with the repo.
- A mistake never crashes anything. A bad entry (including an unknown placeholder) is skipped with a note under "Quick links"; a file-level problem such as a YAML syntax error shows a message with the line number and keeps showing the last working tiles.

## How the nozzle tracking works

SNMP gives only the printer's current lifetime page count and IPP `Get-Jobs` only lists queued jobs, so the app
keeps its own history in SQLite, separately for each printer:

1. **Counter polling.** Every *check interval* (5 minutes by default) (and when a page is viewed and the last read is older than 30 s) the app reads the counter. A rise is recorded with the time it was noticed and the previous read time, so times are accurate to roughly the poll interval. This is more precise than the host timer's state file, which is stamped when its daily run *noticed* the change.
2. **Telling nozzle checks from other printing.** The app matches counter rises with nozzle checks it knows about: ones it submitted itself, and ones the host timer printed (found in CUPS's completed jobs: job name containing `nozzle-check`, i.e. `NOZZLE_JOB_PATTERN`, not submitted by this app). Anything left over is "someone printed". That is how *last print by someone*, *last automated check* and *last manual run* stay separate.
3. **Prints that bypass CUPS.** CUPS only knows about jobs sent through it. A phone printing straight to the printer, or a copy made on its glass, never appears in CUPS but does move the page counter. When the counter rises and no real CUPS job was printing or finished since the last read, those pages are listed under *Recent prints* as **Printed without CUPS** (a page count and an approximate time, since the printer doesn't say more). If CUPS can't be reached at that moment the pages are not labelled either way.
4. **Idle clock.** Like the host script, a nozzle check counts as a print and restarts the idle clock, so *next automated check due* is `last print of any kind + idle days` (7 by default).
5. **First run.** If the printer has a timer state file set and its count equals the printer's current count, the idle clock starts from its timestamp (it can lag the real print by up to a day). Otherwise it starts from the newest finished non-nozzle job in CUPS, and failing that from the first reading, in which case the card says "at least".

The idle rule lives in `app/tools/maintenance/idle.py` (the old host script's logic, with no I/O) and the scheduler in `schedule.py`. **Run a check now** (inside the Automation card, with a confirm dialog) prints one check on demand through `POST /api/maintenance/<printer id>/nozzle/print`: CSRF-protected and rate-limited. It is recorded as a manual check and restarts the idle clock.

## Automatic nozzle check (Nozzle Check tab)

Two words to keep apart: a **nozzle check** is a test page that is *printed*; **reading** the page total prints nothing and is how the app notices prints (every 5 minutes by default). The tab's cards: *Last page printed* (anything, from any device), *Idle for* (time since then), *Last automatic check* (a check printed by this app or by another timer found in the CUPS history) and *Last manual check* (one you started with Run a check now).

Inkjet nozzles clog when a printer sits unused. panda-print can print one small test page (four colour bars) when a
printer has gone a set number of days without printing anything. It replaces a cron or systemd timer on the host: if
you used one, retire it once you switch this on (otherwise both would print).

How it decides, per printer:

1. The idle clock is the printer's last print of any kind: a document from any device, or a nozzle check (which counts, so one check restarts the clock).
2. When the printer has been idle for the set number of days **and** the time of day is past the *don't print before* time, it sends one check to the printer's CUPS queue (IPP `Print-Job`; no `lp` needed in the container).
3. Guards: it only acts when the page counter can be read (so "idle" is known, not guessed); after a failed attempt it waits an hour; it never sends a second check while an earlier one (sent in the last 6 hours) may still be printing; and an app-sent check whose page never appeared on the counter doesn't restart the idle clock.
4. It is **off by default**. Turn it on with the switch in the **Automation** card, per printer. `NOZZLE_AUTO=1` makes new printers start with it on.

The time is the server's clock, so set `TZ` (for example `TZ=Europe/London`) in the container; the image defaults to UTC.

The card's settings (all per printer, defaults from the environment so a new printer starts exactly as configured there):

| Setting | Default | What it does |
| --- | --- | --- |
| Print the nozzle check automatically | off | The switch (`NOZZLE_AUTO`) |
| Idle days before a check is due | 7 | Drives the automatic check and the "Next check" badge |
| Don't print before | 10:00 | Quiet hours, in the container's time zone: `TZ` if set, else the container's default (UTC in the image) (`NOZZLE_CHECK_TIME`) |
| Read the page counter every | 5 minutes | How often prints are noticed |
| A new page counts as the check if it prints within | 30 minutes | After a check is sent the page total rises by one; a rise within this time is credited to the check, a later one is treated as someone else's print |
| Also recognise checks whose job name has | `nozzle-check` | Spots nozzle-check pages sent to CUPS by another timer, so they aren't mistaken for someone printing |

Only values you change are stored; the rest keep following the defaults. **Save settings** is only enabled when something changed, and **Reset to defaults** puts the settings back (it leaves the on/off switch as it is, because the switch isn't one of the settings). Times on the card, including the "Next check" badge, are shown in the container's time zone.
The logic is in `app/tools/maintenance/schedule.py` (pure rules, with tests) and `Service.run_schedule`.

## Security model

- No login. Put an SSO reverse proxy in front.
- Every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/` needs an `X-CSRF-Token` header that matches an HttpOnly, `SameSite=Strict` cookie and carries a valid HMAC signature (`GET /api/csrf` issues both). If the browser sends `Origin` or `Referer`, its host must match the request host, `X-Forwarded-Host`, or `TRUSTED_ORIGINS`.
- The wizard and the Printers page make the server contact addresses you type (printers, CUPS), so they are POSTs behind the same CSRF protection, and link-local and multicast addresses (where cloud metadata services live) are refused. Anyone allowed through your SSO can add a printer, so treat that as an admin-level permission.
- The manual-print endpoint is limited by `PRINT_COOLDOWN` and `PRINT_MAX_PER_HOUR`, counted from the database so restarts don't reset it (answers `429` with `Retry-After`).
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

**Each utility is a tab.** The dashboard shows a tab bar (the address becomes `#<tool>` or `#<tool>/<tab>`). A new tool
is a new tab with no other change. A tool can also declare several tabs of its own, as Printer Maintenance does with
**Overview** (printer, ink, queue, recent prints) and **Nozzle Check**:

```python
from app.core.tool import Tool, ToolTab
Tool(id="maintenance", ..., tabs=[ToolTab("overview", "Overview", "printer"), ToolTab("nozzle", "Nozzle Check", "droplet")])
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
- A tool about printers keeps its data **per printer** (a `printer_id` column) and puts the id in its routes, like `/api/maintenance/<printer id>/nozzle`. Register `ctx.printers.on_delete(fn)` to remove that data when a printer is removed. See `app/tools/maintenance/` (`store.py`, `runtimes.py`).
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
  tools/maintenance/    idle.py, tracker.py, store.py, runtimes.py, normalize.py, service.py, api.py, ui/ (overview.js, nozzle.js, shared.js)
  tools/_template/      copy me
config/                 links.yml (starter) and logos/
tests/                  fakes.py (fake printer/CUPS/network) and pytest (config, idle logic, attribution, IPP/SNMP/mDNS wire formats, printer registry and API, CSRF, rate limit)
Dockerfile  compose.example.yml  .github/workflows/build.yml
```

## Known limits

- Verified read-only against the real DCP-T730DW from a Mac on the same LAN: mDNS discovery, the IPP attributes (ink levels, state, model) and the SNMP page counter all work. Not yet exercised against a real CUPS server: the queue list, job list, host-job import and printing are covered by in-process fakes only, so on the Pi check that the print queue card loads before pressing "Print nozzle check now".
- An old host timer's state file is optional and per printer; it only helps the very first reading. Nothing else depends on it.
- If CUPS is unreachable while another timer prints a check, that page is counted as "someone printed" (the counter can't tell the difference).
- "Printed without CUPS" needs no real CUPS job at all in the window. If a CUPS print and a direct print land between two reads, both are attributed to CUPS.
- The page counter is shared by all prints, so a check that never prints (for example the printer is offline) can be mistaken for a later print within the matching window (30 minutes by default).
- Ink levels are the printer's own estimates.
