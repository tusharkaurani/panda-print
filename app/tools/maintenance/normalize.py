"""Turn raw IPP attributes into the shapes the UI wants. Pure functions."""
from __future__ import annotations

from urllib.parse import urlsplit

from app.printer import Attrs

PRINTER_STATES = {3: ("idle", "Idle"), 4: ("processing", "Printing"), 5: ("stopped", "Stopped")}
JOB_STATES = {3: "pending", 4: "held", 5: "printing", 6: "stopped", 7: "canceled", 8: "aborted", 9: "completed"}
INK_ORDER = ["C", "M", "Y", "K"]
INK_NAMES = {"C": "Cyan", "M": "Magenta", "Y": "Yellow", "K": "Black"}
SEVERITY_RANK = {"none": 0, "info": 1, "warning": 2, "error": 3}
DEFAULT_LOW_PERCENT = 10


def first(attrs: Attrs, name: str, default=None):
    values = attrs.get(name)
    return values[0] if values else default


UNNAMED = "(unnamed)"


def job_name(attrs: Attrs) -> str:
    """A CUPS job's name. Jobs sent with `lp` may lack job-name; then the document name is all there is."""
    return str(first(attrs, "job-name") or first(attrs, "document-name-supplied") or "")


def ink_key(name: str, color: str | None = None) -> str | None:
    n = (name or "").strip().lower()
    if "black" in n or n in ("k", "bk", "pgbk"):
        return "K"
    if "cyan" in n or n == "c":
        return "C"
    if "magenta" in n or n == "m":
        return "M"
    if "yellow" in n or n == "y":
        return "Y"
    return {"#000000": "K", "#00ffff": "C", "#ff00ff": "M", "#ffff00": "Y"}.get((color or "").lower())


def inks(attrs: Attrs) -> list[dict]:
    """Ink gauges in C, M, Y, K order (the printer sends them in its own order)."""
    names = [str(x) for x in attrs.get("marker-names", [])]
    levels = attrs.get("marker-levels", [])
    colors = attrs.get("marker-colors", [])
    lows = attrs.get("marker-low-levels", [])
    out = []
    for i, name in enumerate(names):
        level = levels[i] if i < len(levels) else -1
        color = colors[i] if i < len(colors) else None
        key = ink_key(name, color) or name
        percent = int(level) if isinstance(level, int) and 0 <= level <= 100 else None
        low_at = lows[i] if i < len(lows) and isinstance(lows[i], int) and lows[i] > 0 else DEFAULT_LOW_PERCENT
        out.append({"key": key, "name": INK_NAMES.get(key, name), "percent": percent,
                    "low": percent is not None and percent <= low_at,
                    "empty": percent == 0})
    out.sort(key=lambda g: INK_ORDER.index(g["key"]) if g["key"] in INK_ORDER else 99)
    return out


def reasons(attrs: Attrs) -> tuple[list[dict], str]:
    """printer-state-reasons -> chips [{text, severity}] and the worst severity."""
    chips, worst = [], "none"
    for r in attrs.get("printer-state-reasons", []):
        r = str(r)
        if r == "none":
            continue
        sev = "info"
        for suffix, s in (("-error", "error"), ("-warning", "warning"), ("-report", "info")):
            if r.endswith(suffix):
                r, sev = r[: -len(suffix)], s
                break
        text = r.replace("-", " ").strip()
        chips.append({"text": text[:1].upper() + text[1:], "severity": sev})
        if SEVERITY_RANK[sev] > SEVERITY_RANK[worst]:
            worst = sev
    return chips, worst


def queue_state(attrs: Attrs) -> dict:
    code = int(first(attrs, "printer-state", 0) or 0)
    key, label = PRINTER_STATES.get(code, ("unknown", "Unknown"))
    chips, worst = reasons(attrs)
    accepting = first(attrs, "printer-is-accepting-jobs")
    if key == "stopped" or worst == "error" or accepting is False:
        health = "error"
    elif worst == "warning":
        health = "warn"
    elif key == "unknown":
        health = "unknown"
    else:
        health = "ok"
    return {"code": key, "label": label, "health": health, "reasons": chips,
            "message": first(attrs, "printer-state-message", "") or "",
            "accepting": accepting if isinstance(accepting, bool) else None}


def _uptime(seconds) -> str | None:
    if not isinstance(seconds, int) or seconds <= 0:
        return None
    d, rem = divmod(seconds, 86400)
    h, m = divmod(rem // 60, 60)
    return f"{d}d {h}h" if d else f"{h}h {m}m"


def _media(v: str) -> str:
    return v.replace("iso_", "").replace("na_", "").split("_")[0].upper() if v else v


def landing_page(more_info, ip: str | None) -> str | None:
    """The printer's own front page. printer-more-info often points deeper (Brother gives its AirPrint
    help page), so keep only the address part; with none, fall back to http://<ip>/."""
    parts = urlsplit(str(more_info or ""))
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}/"
    if ip:
        return f"http://[{ip}]/" if ":" in ip else f"http://{ip}/"
    return None


def printer_details(attrs: Attrs, ip: str | None = None) -> list[dict]:
    """Extra facts, only those the printer provided. `key` is stable for the UI; `label` is for people."""
    rows = [
        ("model", "Model", first(attrs, "printer-make-and-model")),
        ("firmware", "Firmware", first(attrs, "printer-firmware-string-version")),
        ("uptime", "Uptime", _uptime(first(attrs, "printer-up-time"))),
        ("web", "Web interface", landing_page(first(attrs, "printer-more-info"), ip)),
        ("location", "Location", first(attrs, "printer-location")),
        ("paper", "Paper loaded", ", ".join(_media(str(m)) for m in attrs.get("media-ready", [])) or None),
        ("sides", "Default sides", first(attrs, "sides-default")),
        ("colour", "Default colour mode", first(attrs, "print-color-mode-default")),
    ]
    return [{"key": k, "label": label, "value": str(v)} for k, label, v in rows if v not in (None, "")]


def printer(attrs: Attrs, ip: str | None = None) -> dict:
    state = queue_state(attrs)
    return {
        "name": first(attrs, "printer-info") or first(attrs, "printer-name") or "Printer",
        "model": first(attrs, "printer-make-and-model"),
        "state": state,
        "inks": inks(attrs),
        "details": printer_details(attrs, ip),
    }


def job(attrs: Attrs, nozzle_pattern: str) -> dict:
    name = job_name(attrs) or UNNAMED
    code = first(attrs, "job-state")
    created = first(attrs, "time-at-creation")
    completed = first(attrs, "time-at-completed")
    pages = first(attrs, "job-media-sheets-completed")
    return {
        "id": first(attrs, "job-id"),
        "name": name,
        "user": first(attrs, "job-originating-user-name", ""),
        "state": JOB_STATES.get(code, "unknown"),
        "created": created,
        "completed": completed,
        "pages": pages if isinstance(pages, int) and pages > 0 else None,
        "nozzle_check": nozzle_pattern.lower() in name.lower() or "nozzle check" in name.lower(),
    }


def queue(attrs: Attrs) -> dict:
    state = queue_state(attrs)
    return {
        "name": first(attrs, "printer-name"),
        "info": first(attrs, "printer-info"),
        "state": state,
        "queued": first(attrs, "queued-job-count", 0),
        "device": first(attrs, "device-uri"),
        "sides_supported": list(attrs.get("sides-supported") or []),
    }
