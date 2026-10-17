"""Setup-wizard helpers: find printers, and check that a printer's address works."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import urlsplit

from app.core.errors import SourceError
from app.core.printers import Printer, clean_host, validate
from app.printer import mdns
from app.printer.live import LivePrinter
from app.settings import Settings

PROBE_TIMEOUT = 4.0
IPP_PATHS = ["/ipp/print", "/ipp", "/ipp/printer", "/"]
PRINTER_STATES = {3: "Idle", 4: "Printing", 5: "Stopped"}


def _first(attrs, name, default=None):
    v = attrs.get(name)
    return v[0] if v else default


def device_host(device_uri: str) -> str:
    """The IP/host in a CUPS device URI (ipp://, socket://, lpd://...), or '' (usb:, dnssd names...)."""
    parts = urlsplit(device_uri)
    if parts.scheme not in ("ipp", "ipps", "http", "https", "socket", "lpd"):
        return ""
    return clean_host(parts.hostname or "")


def stub(settings: Settings, cups_url: str) -> Printer:
    return Printer(id="-", name="-", ip="0.0.0.0", cups_url=cups_url)


def cups_candidates(settings: Settings, cups_url: str) -> tuple[list[dict], str | None]:
    """Queues CUPS already has, with the printer address read from their device URI."""
    try:
        queues = LivePrinter(settings, stub(settings, cups_url), PROBE_TIMEOUT).cups_queues()
    except SourceError as e:
        return [], f"Couldn't read the CUPS queue list: {e.message}"
    out = []
    for q in queues:
        device = str(_first(q, "device-uri", ""))
        ip = device_host(device)
        model = _first(q, "printer-make-and-model") or _first(q, "printer-info") or ""
        if not ip:  # usb:, dnssd://... : nothing we can talk to over the network
            continue
        out.append({"source": "cups", "name": _first(q, "printer-info") or _first(q, "printer-name"),
                    "model": model, "ip": ip, "queue": _first(q, "printer-name"), "cups_url": cups_url,
                    "ipp_url": device if device.startswith("ipp://") else "", "device_uri": device})
    return out, None


def discover(settings: Settings, cups_url: str | None = None) -> dict:
    """Look in CUPS and on the network at the same time, merging what is the same printer."""
    cups_url = cups_url or settings.cups_url
    with ThreadPoolExecutor(2) as pool:
        cups_f = pool.submit(cups_candidates, settings, cups_url)
        mdns_f = pool.submit(mdns.browse)
        (from_cups, cups_note), (from_net, net_note) = cups_f.result(), mdns_f.result()
    merged: dict[str, dict] = {c["ip"]: {**c, "sources": ["cups"]} for c in from_cups}
    for c in from_net:
        if c["ip"] in merged:
            m = merged[c["ip"]]
            m["sources"].append("mdns")
            m["ipp_url"] = m["ipp_url"] or c["ipp_url"]
            m["model"] = m["model"] or c["model"]
        else:
            merged[c["ip"]] = {**c, "sources": ["mdns"]}
    notes = [n for n in (cups_note, net_note) if n]
    return {"candidates": list(merged.values()), "notes": notes}


# ------------------------------------------------------------------ probing a draft

def _probe_ipp(settings: Settings, printer: Printer) -> dict:
    urls = [printer.ipp_url] if printer.ipp_url else [f"ipp://{printer.ip}{p}" for p in IPP_PATHS]
    last: SourceError | None = None
    for url in urls:
        try:
            attrs = LivePrinter(settings, replace(printer, ipp_url=url), PROBE_TIMEOUT).printer_info()
        except SourceError as e:
            last = e
            if e.kind in ("timeout", "dns"):
                break  # the host itself isn't answering; other paths won't help
            continue
        model = _first(attrs, "printer-make-and-model") or _first(attrs, "printer-info") or ""
        return {"ok": True, "ipp_url": url, "model": model,
                "name": _first(attrs, "printer-info") or model,
                "state": PRINTER_STATES.get(_first(attrs, "printer-state"), "Unknown"),
                "inks": len(attrs.get("marker-levels", []))}
    return {"ok": False, "error": (last or SourceError("printer", "No answer.")).to_dict()}


def _probe_snmp(settings: Settings, printer: Printer) -> dict:
    try:
        return {"ok": True, "value": LivePrinter(settings, printer, PROBE_TIMEOUT).read_counter()}
    except SourceError as e:
        return {"ok": False, "error": e.to_dict()}


def _probe_cups(settings: Settings, printer: Printer) -> dict:
    try:
        queues = LivePrinter(settings, printer, PROBE_TIMEOUT).cups_queues()
    except SourceError as e:
        return {"ok": False, "error": e.to_dict(), "queues": [], "suggested": ""}
    listed = [{"name": _first(q, "printer-name"), "info": _first(q, "printer-info", ""),
               "device_uri": _first(q, "device-uri", "")} for q in queues]
    same_ip = [q["name"] for q in listed if device_host(q["device_uri"]) == printer.ip]
    names = [q["name"] for q in listed]
    suggested = (printer.cups_queue if printer.cups_queue in names
                 else same_ip[0] if same_ip else names[0] if len(names) == 1 else "")
    if printer.cups_queue and printer.cups_queue not in names:
        return {"ok": False, "queues": listed, "suggested": suggested,
                "error": {"source": "cups", "message": f"CUPS has no queue named '{printer.cups_queue}'.",
                          "hint": "Pick one of the queues below."}}
    if not names:
        return {"ok": False, "queues": [], "suggested": "",
                "error": {"source": "cups", "message": "CUPS has no print queues yet.",
                          "hint": "Add the printer to CUPS first, or continue without a queue."}}
    return {"ok": True, "queues": listed, "suggested": suggested}


def probe(settings: Settings, draft: dict) -> dict:
    """Try IPP, SNMP and CUPS for a draft config, all at once. Never raises."""
    clean, errors = validate({**draft, "name": draft.get("name") or "probe"})
    errors.pop("name", None)
    if errors:
        return {"ok": False, "errors": errors}
    printer = Printer(id="-", created_at=0.0, **clean)
    with ThreadPoolExecutor(3) as pool:
        ipp_f = pool.submit(_probe_ipp, settings, printer)
        snmp_f = pool.submit(_probe_snmp, settings, printer)
        cups_f = pool.submit(_probe_cups, settings, printer)
        ipp_r, snmp_r, cups_r = ipp_f.result(), snmp_f.result(), cups_f.result()
    return {"ok": True, "ipp": ipp_r, "snmp": snmp_r, "cups": cups_r,
            "suggested_name": ipp_r.get("name") or "", "ipp_url": ipp_r.get("ipp_url", "")}
