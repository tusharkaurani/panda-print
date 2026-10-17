# panda-print

A small, self-hosted dashboard that is the front door for everything printer-related at home.
It ships with one tool, **Printer Maintenance** (status, ink gauges, lifetime page counter, CUPS queue,
recent prints, and a nozzle-check tracker that can also print the nozzle check for you when a printer has sat idle), plus a **launcher** grid for
external tools. More tools plug in as self-contained modules.

Built for a Brother DCP-T730DW behind CUPS on a Raspberry Pi (the only printer verified on real hardware; others should work through standard IPP and SNMP but are untested), and no printer is hard-wired: the first visit opens
a **setup wizard** that finds your printer (or asks for its IP), and a **Printers** page lets you add, edit, test and
remove printers later. Everything else (ports, paths, timeouts) is an environment variable.

- Python 3.12 + Flask on waitress, no build step, no CDN, no runtime internet. About 30-40 MB RAM.
- Light and dark themes (follows the OS, with a toggle), mobile friendly.
- One port (default 8095). No login: it expects an SSO reverse proxy in front. State-changing requests are CSRF-protected and printing is rate-limited.

## CUPS is optional

panda-print talks to the printer directly (IPP for status and ink, SNMP for the page count). **CUPS**, the print service on
a Pi, Mac or Linux box, is only needed for printing. CUPS can run on the same machine or on another one on your network.

| Without CUPS you get | With CUPS you also get |
|---|---|
| Printer status and ink levels | The print queue and recent print jobs |
| Lifetime page count, and any print noticed from it | Telling a print from a computer apart from one sent straight to the printer |
| Last print, idle time | **Print now** (with a preview) |
| Several printers, quick links, the Print Utilities tab | Automatic printing when the printer sits unused, and your own pages in rotation |

A printer is "without CUPS" when it has no print queue. Cards that need CUPS show a faded sketch of what they would hold, what
CUPS adds and how to set it up (install CUPS, add the printer to it, then choose the queue under **Printers → Edit**). Nothing is
shown as an error for a choice you haven't made. A queue that is set but unreachable still shows an error with a retry.

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
| `MAX_PAGE_MB`, `MAX_PAGES`, `MAX_LIBRARY_MB` | `10`, `50`, `100` | Limits of the library of pages to print |
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

The idle rule lives in `app/tools/maintenance/idle.py` (the old host script's logic, with no I/O) and the scheduler in `schedule.py`. **Print now** (top right of the Nozzle care card; it first shows a preview of the page that will print, then asks you to confirm) prints one page on demand through `POST /api/maintenance/<printer id>/nozzle/print`: CSRF-protected and rate-limited. It is recorded as a manual check and restarts the idle clock.

## Automatic nozzle check (Nozzle Care tab)

Two words to keep apart: a **print** is something that came out of the printer, and a **nozzle check** is the test page; **reading** the page total prints nothing and is how the app notices prints (every 5 minutes by default). The tab's cards: *Last print* (anything, from any device), *Idle for* (time since then), *Last auto print* (a nozzle check printed automatically, by this app or by another timer found in the CUPS history) and *Last manual print* (one you started with **Print now**). *Recent prints* lists them with where each came from.

Inkjet nozzles clog when a printer sits unused. panda-print can print one small test page (four colour bars) when a
printer has gone a set number of days without printing anything. It replaces a cron or systemd timer on the host: if
you used one, retire it once you switch this on (otherwise both would print).

How it decides, per printer:

1. The idle clock is the printer's last print of any kind: a document from any device, or a nozzle check (which counts, so one check restarts the clock).
2. When the printer has been idle for the set number of days **and** the time of day is past the *don't print before* time, it sends one check to the printer's CUPS queue (IPP `Print-Job`; no `lp` needed in the container).
3. Guards: it only acts when the page counter can be read (so "idle" is known, not guessed). When a check comes due, the app reads the counter once more right before sending, so a page printed a minute ago cancels it, and an unreadable printer holds it back; after a failed attempt it waits an hour; it never sends a second check while an earlier one (sent in the last 6 hours) may still be printing; and an app-sent check whose page never appeared on the counter doesn't restart the idle clock.
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

### Useful pages instead of a test pattern

An idle print that only shows colour bars wastes a sheet. Instead the app can rotate through pages you choose, for
example colouring pages, so the print is worth having.

- **Page to print**, on the Automation card: *Standard test page* (the colour bars) or *Rotate my pages*. **Manage pages…** opens the library.
- **The library** is shared by every printer. Add PDF, PNG or JPEG files (a picker or drag and drop; up to `MAX_PAGE_MB` each, `MAX_PAGES` files and `MAX_LIBRARY_MB` in total). Files are checked by their contents, not their names, and PostScript is refused. They are stored in `/data/pages`, so they live in the same volume as the history; include it in backups.
- **Preview and download:** every row shows a real picture of the page (the standard page is drawn as an exact replica of the colour bars). Click it, or the eye icon, to see the page the way it will be sent; PDFs show their pages (the first 12). Uploaded pages also have a **Download** button for the original file. The standard page is built in, so its Download and Delete are greyed out, and nothing can be uploaded over it. Thumbnails are small PNGs your browser draws when you upload (kept in `/data/pages` next to the file); a page without one falls back to the picture itself or a PDF icon.
- **Per printer:** the order, and a switch for each page (the standard test page is one of the items, so you can drop it). Each automatic print, and **Print now**, takes the next enabled page and then loops. Deleted or switched-off pages are skipped; with nothing switched on, the standard page is used.
- **Print a chosen page** (the Print button on a page, with a confirm) sends exactly that page without moving the rotation, which is also how to test a page you just added.
- **Both sides:** for a printer that can flip the paper **by itself**, the Pages dialog offers *Multi-page files print on both sides* (flip like a book, or like a notepad), which saves paper. It applies to files with more than one page, for automatic prints, **Print now** and each page's Print button; one-page files and pictures print as they are. Printing here is unattended, so the option only appears when both the printer and its CUPS queue report two-sided printing (a printer without an automatic duplexer doesn't list it) and is never sent otherwise. If the printer is off when asked, its last confirmed ability is used, and a printer never confirmed counts as not capable. Reset to defaults keeps the choice.
- **Ink:** a page only moves the inks it uses; a black-and-white page does not exercise cyan, magenta or yellow. The dialog says so, and you decide what to include. To be safe, set *Also print the standard test page every N prints* (0 = never) and every Nth print is the colour-bar page on top of the rotation.
- **Printing:** the file goes to the printer's CUPS queue as it is (`print-scaling=fit`, so a picture isn't cropped) and the host's CUPS converts it for the printer. If a page fails to print, the History row shows CUPS's reason; try that page with the Print button, and check that CUPS can print a PDF or picture from the command line (`lp -d <queue> file.pdf`).
- **Page counts:** a print records how many sheets it should make (PDFs are counted; pictures are one). The page counter is credited to the print page by page, so a 2-page PDF isn't mistaken for someone else's print. If a PDF's page count can't be read it is assumed to be one page.

PDFs are drawn in the browser with [pdf.js](https://github.com/mozilla/pdf.js) (Apache-2.0), bundled in `app/static/vendor/pdfjs`, so nothing is loaded from a CDN. It is fetched only when a PDF needs drawing, and runs with script evaluation off.

## Print Utilities tab

A tab of cards that open free online tools for things worth printing (passport photos, student worksheets, colouring pages, calendars and ruled paper, a PDF toolbox, posters, labels and crafts), grouped by category with at most four cards in a row. Each card is a drawing of the kind of page, the tool's name, a one-line description and its site; it opens in a new tab. The cards are drawings made by the app, so opening the tab loads nothing from those sites.

The built-in list is `app/tools/utilities/catalog.yml`. To use your own, copy it to `print-utilities.yml` next to `links.yml` (in the folder you mount at `/config`), then edit: categories, tools, and for each tool `name`, `url` (https), `description`, `art` (which drawing) and optionally `image` (a picture URL). The page picks up changes within a minute; a mistake is reported on the page and the built-in list keeps showing. Every site in the built-in list was read on 17 Oct 2026 and says on its own pages that it is free; that is the site's claim, not a guarantee, and a few ask for a free account for some things (the card says so). Sites that charge for the result or put the main content behind a membership are deliberately left out. They are third-party sites and change, so check a link if it stops working or starts asking for money.

Every button and tab in the app shows what it does when you hover over it (a test fails if a new button has no hover text).

## Security model

- No login. Put an SSO reverse proxy in front.
- Every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/` needs an `X-CSRF-Token` header that matches an HttpOnly, `SameSite=Strict` cookie and carries a valid HMAC signature (`GET /api/csrf` issues both). If the browser sends `Origin` or `Referer`, its host must match the request host, `X-Forwarded-Host`, or `TRUSTED_ORIGINS`.
- The wizard and the Printers page make the server contact addresses you type (printers, CUPS), so they are POSTs behind the same CSRF protection, and link-local and multicast addresses (where cloud metadata services live) are refused. Anyone allowed through your SSO can add a printer, so treat that as an admin-level permission.
- The manual-print endpoint is limited by `PRINT_COOLDOWN` and `PRINT_MAX_PER_HOUR`, counted from the database so restarts don't reset it (answers `429` with `Retry-After`).
- Strict Content-Security-Policy (no inline script or style, no external hosts except images you point links at), and logo files are served sandboxed.
- The SNMP community and addresses are configuration, not secrets, but nothing sensitive is committed: `.env`, `data/`, `secret.key` and your logos are git-ignored.

## When a printer is off

A printer that is switched off or off the network is a normal case, not a crash. The Overview keeps the last known ink levels, model and page count on screen, dimmed, with a note ("The printer isn't answering… from 2 hours ago") and a Try again button; the badge says Offline. The last good answer is stored in the database, so it survives a restart. Nozzle Care says it can't read the page count. The automatic print waits (it only prints when it can tell the printer is idle) and goes ahead by itself when the printer returns. **Print now** warns first: the page would wait in the CUPS queue and print when the printer is back. A printer that has never answered shows just the reason. Everything recovers on its own, and one printer being off never affects another.

## Several printers

Add as many printers as you like (**Manage printers…** in the header menu). The rules the code and `tests/test_multi_printer.py` hold to:

- **Everything per printer stays per printer:** status, history, automatic-print settings, rotation order and switches, rate limits, locks. Changing one printer never changes another.
- **Shared on purpose:** the library of pages (a new page appears in every printer's list; deleting one removes it from every list) and the launcher links file (a link can be limited to some printers with `printers:`).
- **One printer's trouble stays its own:** an unreachable address, a slow CUPS or a failing queue shows as that printer's error card. The background poller gives each printer its own worker, never runs one printer twice at once, and a hung printer doesn't delay the others.
- **Removing a printer** deletes its history and settings (not the shared pages) and, if it was selected, the header falls back to the first one. Removing the last one returns to the welcome wizard. A new printer that reuses a removed printer's name starts clean.
- **Editing** keeps the printer's id, so renaming keeps its history; changing its address or queue rebuilds its connection, and the print lock survives the rebuild so two copies can't print at once.
- **Names and ids:** any name works (symbols, other alphabets, near-identical names). Ids are made from the name, made unique, and never clash with the app's own words (`pages`, `probe`, `discover`, ...).
- Two CUPS queues on one physical printer are fine; each keeps its own books.

The tests use fakes only. `test_a_removed_printer_leaves_nothing_in_any_table` fails if a new tool adds a `printer_id` table and forgets `on_delete`, and `test_every_printer_route_answers_404_for_an_unknown_printer_and_never_500` walks the URL map, so a new per-printer route is covered automatically.

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
  tools/_template/      copy me
config/                 links.yml (starter) and logos/
tests/                  fakes.py (fake printer/CUPS/network) and pytest (config, idle logic, attribution, IPP/SNMP/mDNS wire formats, printer registry and API, CSRF, rate limit)
Dockerfile  compose.example.yml  .github/workflows/build.yml
```

## Known limits

- Verified read-only against the real DCP-T730DW (from a Mac on the same LAN) and the real CUPS on the Pi: mDNS discovery, the IPP attributes (ink levels, state, model), the SNMP page counter, and the CUPS queue and job list. Not yet exercised on real hardware: printing a PDF, PNG or JPEG through CUPS (covered by in-process fakes), so on the Pi check that the print queue card loads and try **Print now** once.
- An old host timer's state file is optional and per printer; it only helps the very first reading. Nothing else depends on it.
- If CUPS is unreachable while another timer prints a check, that page is counted as "someone printed" (the counter can't tell the difference).
- "Printed without CUPS" needs no real CUPS job at all in the window. If a CUPS print and a direct print land between two reads, both are attributed to CUPS.
- The page counter is shared by all prints, so a check that never prints (for example the printer is offline) can be mistaken for a later print within the matching window (30 minutes by default).
- Ink levels are the printer's own estimates.

## License

MIT, see [LICENSE](LICENSE). Third-party software and trademarks are listed in [THIRD-PARTY.md](THIRD-PARTY.md). Security notes are in
[SECURITY.md](SECURITY.md) and how to contribute is in [CONTRIBUTING.md](CONTRIBUTING.md). panda-print is not affiliated with Brother, the CUPS project or any
site it links to.
