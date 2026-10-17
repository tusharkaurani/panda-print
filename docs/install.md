# Install and set up

[← Back to the README](../README.md)

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

- The published image is public, so no login is needed. If you fork the project and publish your own image, new GHCR packages are private by default: make yours public (Packages → Package settings), or `docker login ghcr.io` on the Pi with a token that has `read:packages`.
- Without host networking, publish the port instead (`ports: ["8095:8095"]`) and set `CUPS_URL=http://192.168.1.10:631`. CUPS must then allow requests from the container's network.
- Behind a reverse proxy the app only needs `X-Forwarded-Host`/`Host` to match the browser's `Origin` (the usual case). If your proxy rewrites `Host`, set `TRUSTED_ORIGINS=https://print.example.lan`.
- If the proxy runs on the same Pi with host networking, set `HOST=127.0.0.1` so the dashboard isn't also exposed on the LAN without SSO.

## Setting up printers

Open the dashboard. With no printer added, you get the wizard, and nothing else is shown until one exists.

1. **Find your printer.** panda-print searches in two places at once: the queues CUPS already has (the printer's address is read from each queue's device URI) and the network itself (mDNS/Bonjour `_ipp._tcp`). Pick one, or type an IP address or host name (pasting `http://192.168.1.50/` works too).
2. **Connect and name.** It checks three things in parallel and shows each result: the printer over IPP (model, state), the page counter over SNMP, and the CUPS queue (it picks the queue whose device points at the same address). Name and queue are filled in for you; **Advanced settings** has the IPP address, SNMP community/port/OID, the CUPS server, and an optional nozzle-check timer state file. If something fails you can still save ("Add anyway") and fix it later.

The **printer dropdown** in the header lists your printers (the first is preselected) and ends with **Manage printers…**,
which opens the Printers page: every printer with a live check, and edit, re-test and remove. Removing a printer
deletes its nozzle-check history (not the printer or its CUPS queue). With zero printers the wizard comes back.

Discovery notes: mDNS needs multicast, which works with host networking (the compose example) but usually not in a
bridged Docker network; the wizard says so and CUPS plus manual entry still work. `PRINTER_IP` (optional) adds a first
printer automatically at start-up for unattended installs.
