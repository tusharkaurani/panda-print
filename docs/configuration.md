# Configuration

[← Back to the README](../README.md)

## Configuration

All settings are environment variables; see [.env.example](../.env.example) for the full list with defaults.

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
| `PRINT_COOLDOWN`, `PRINT_MAX_PER_HOUR` | `120`, `5` | Rate limit of the manual-print endpoint (see [How it works](how-it-works.md#security-model)) |
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

## Print Utilities list

The Print Utilities tab shows the tools built into panda-print first (*Built in*), then cards that open free online tools in a new tab. Only the online ones are in this list.

The built-in list is `app/tools/utilities/catalog.yml`. To use your own, copy it to `print-utilities.yml` next to `links.yml` (in the folder you mount at `/config`), then edit: categories, tools, and for each tool `name`, `url` (https), `description`, `art` (which drawing) and optionally `image` (a picture URL). The page picks up changes within a minute; a mistake is reported on the page and the built-in list keeps showing. Every site in the built-in list was read on 17 Oct 2026 and says on its own pages that it is free; that is the site's claim, not a guarantee, and a few ask for a free account for some things (the card says so). Sites that charge for the result or put the main content behind a membership are deliberately left out. They are third-party sites and change, so check a link if it stops working or starts asking for money.
