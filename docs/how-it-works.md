# How it works

[← Back to the README](../README.md)

## How the nozzle tracking works

SNMP gives only the printer's current lifetime page count and IPP `Get-Jobs` only lists queued jobs, so the app
keeps its own history in SQLite, separately for each printer:

1. **Counter polling.** Every *check interval* (5 minutes by default) (and when a page is viewed and the last read is older than 30 s) the app reads the counter. A rise is recorded with the time it was noticed and the previous read time, so times are accurate to roughly the poll interval. This is more precise than the host timer's state file, which is stamped when its daily run *noticed* the change.
2. **Telling nozzle checks from other printing.** The app matches counter rises with nozzle checks it knows about: ones it submitted itself, and ones the host timer printed (found in CUPS's completed jobs: job name containing `nozzle-check`, i.e. `NOZZLE_JOB_PATTERN`, not submitted by this app). Anything left over is "someone printed". That is how *last print by someone*, *last automated check* and *last manual run* stay separate.
3. **Prints that bypass CUPS.** CUPS only knows about jobs sent through it. A phone printing straight to the printer, or a copy made on its glass, never appears in CUPS but does move the page counter. When the counter rises and no real CUPS job was printing or finished since the last read, those pages are listed under *Recent prints* as **Printed without CUPS** (a page count and an approximate time, since the printer doesn't say more). If CUPS can't be reached at that moment the pages are not labelled either way.
4. **Idle clock.** Like the host script, a nozzle check counts as a print and restarts the idle clock, so *next automated check due* is `last print of any kind + idle days` (7 by default).
5. **First run.** If the printer has a timer state file set and its count equals the printer's current count, the idle clock starts from its timestamp (it can lag the real print by up to a day). Otherwise it starts from the newest finished non-nozzle job in CUPS, and failing that from the first reading, in which case the card says "at least".

The idle rule lives in `app/tools/maintenance/idle.py` (the old host script's logic, with no I/O) and the scheduler in `schedule.py`. **Print now** (top right of the Nozzle care card; it first shows a preview of the page that will print, then asks you to confirm) prints one page on demand through `POST /api/maintenance/<printer id>/nozzle/print`: CSRF-protected and rate-limited. It is recorded as a manual check and restarts the idle clock.

## Security model

- No login. Put an SSO reverse proxy in front.
- Every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/` needs an `X-CSRF-Token` header that matches an HttpOnly, `SameSite=Strict` cookie and carries a valid HMAC signature (`GET /api/csrf` issues both). If the browser sends `Origin` or `Referer`, its host must match the request host, `X-Forwarded-Host`, or `TRUSTED_ORIGINS`.
- The wizard and the Printers page make the server contact addresses you type (printers, CUPS), so they are POSTs behind the same CSRF protection, and link-local and multicast addresses (where cloud metadata services live) are refused. Anyone allowed through your SSO can add a printer, so treat that as an admin-level permission.
- The manual-print endpoint is limited by `PRINT_COOLDOWN` and `PRINT_MAX_PER_HOUR`, counted from the database so restarts don't reset it (answers `429` with `Retry-After`).
- Strict Content-Security-Policy (no inline script or style, no external hosts except images you point links at), and logo files are served sandboxed.
- The SNMP community and addresses are configuration, not secrets, but nothing sensitive is committed: `.env`, `data/`, `secret.key` and your logos are git-ignored.

## Known limits

- Verified read-only against the real DCP-T730DW (from a Mac on the same LAN) and the real CUPS on the Pi: mDNS discovery, the IPP attributes (ink levels, state, model), the SNMP page counter, and the CUPS queue and job list. Not yet exercised on real hardware: printing a PDF, PNG or JPEG through CUPS (covered by in-process fakes), so on the Pi check that the print queue card loads and try **Print now** once.
- An old host timer's state file is optional and per printer; it only helps the very first reading. Nothing else depends on it.
- If CUPS is unreachable while another timer prints a check, that page is counted as "someone printed" (the counter can't tell the difference).
- "Printed without CUPS" needs no real CUPS job at all in the window. If a CUPS print and a direct print land between two reads, both are attributed to CUPS.
- The page counter is shared by all prints, so a check that never prints (for example the printer is offline) can be mistaken for a later print within the matching window (30 minutes by default).
- Ink levels are the printer's own estimates.
