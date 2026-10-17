"""The registry of printers the dashboard manages.

Printers live in the database (added through the setup wizard or the Printers
page); tools never see a hard-coded address. Every field is validated here, so
the rest of the app can trust what it gets.
"""
from __future__ import annotations

import ipaddress
import re
import threading
import time
from dataclasses import asdict, dataclass, fields, replace
from typing import Callable, Iterable
from urllib.parse import urlsplit

from app.core.db import Database

DEFAULT_COUNTER_OID = "1.3.6.1.2.1.43.10.2.1.4.1.1"  # prtMarkerLifeCount

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS printers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ip TEXT NOT NULL,
        ipp_url TEXT NOT NULL DEFAULT '',
        snmp_community TEXT NOT NULL DEFAULT 'public',
        snmp_port INTEGER NOT NULL DEFAULT 161,
        counter_oid TEXT NOT NULL DEFAULT '1.3.6.1.2.1.43.10.2.1.4.1.1',
        cups_url TEXT NOT NULL DEFAULT 'http://127.0.0.1:631',
        cups_queue TEXT NOT NULL DEFAULT '',
        host_state_file TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    """,
]


@dataclass(frozen=True)
class Printer:
    id: str
    name: str
    ip: str  # address or host name of the printer itself
    ipp_url: str = ""  # blank means ipp://<ip>/ipp/print
    snmp_community: str = "public"
    snmp_port: int = 161
    counter_oid: str = DEFAULT_COUNTER_OID
    cups_url: str = "http://127.0.0.1:631"
    cups_queue: str = ""  # blank means "no CUPS queue": status works, jobs and printing don't
    host_state_file: str = ""  # optional: the host nozzle-check timer's state file for this printer
    created_at: float = 0.0

    @property
    def ipp_endpoint(self) -> str:
        return self.ipp_url or f"ipp://{self.ip}/ipp/print"

    def to_dict(self) -> dict:
        return asdict(self)


EDITABLE = [f.name for f in fields(Printer) if f.name not in ("id", "created_at")]


class ValidationError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_QUEUE_RE = re.compile(r"^[A-Za-z0-9_.@+-]{1,127}$")
_OID_RE = re.compile(r"^\d+(\.\d+){5,}$")


def clean_host(value: str) -> str:
    """Accept '192.168.1.18', 'printer.lan', or a pasted 'http://192.168.1.18/'. Returns '' if invalid."""
    value = (value or "").strip()
    if "://" in value:
        value = urlsplit(value).hostname or ""
    value = value.strip("[]")
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return value if _HOST_RE.match(value) else ""
    # nothing a printer could be, and link-local is where cloud metadata services live
    if addr.is_unspecified or addr.is_multicast or addr.is_link_local:
        return ""
    return str(addr)


def clean_url(value: str, schemes: tuple[str, ...]) -> str:
    """The URL without a trailing slash if its scheme and host are acceptable, else ''."""
    parts = urlsplit(value)
    if parts.scheme not in schemes or not parts.hostname:
        return ""
    host = clean_host(parts.hostname)
    return value.rstrip("/") if host else ""


def validate(draft: dict, taken_names: Iterable[str] = ()) -> tuple[dict, dict[str, str]]:
    """Return (clean values for every editable field, {field: message})."""
    errors: dict[str, str] = {}
    d = {k: (v.strip() if isinstance(v, str) else v) for k, v in draft.items()}

    name = d.get("name") or ""
    if not name:
        errors["name"] = "Give the printer a name."
    elif len(name) > 60:
        errors["name"] = "Use 60 characters or fewer."
    elif name.lower() in {n.lower() for n in taken_names}:
        errors["name"] = "Another printer already has this name."

    ip = clean_host(d.get("ip") or "")
    if not ip:
        errors["ip"] = "Enter the printer's IP address or host name, such as 192.168.1.50."

    ipp_url = d.get("ipp_url") or ""
    if ipp_url:
        ipp_url = clean_url(ipp_url, ("ipp", "http", "https"))
        if not ipp_url:
            errors["ipp_url"] = "Use a full address like ipp://192.168.1.50/ipp/print."

    community = d.get("snmp_community")
    community = "public" if community in (None, "") else str(community)
    if len(community) > 64:
        errors["snmp_community"] = "Too long."

    port = d.get("snmp_port")
    try:
        port = 161 if port in (None, "") else int(port)
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        errors["snmp_port"] = "Enter a port between 1 and 65535."
        port = 161

    oid = d.get("counter_oid") or DEFAULT_COUNTER_OID
    if not _OID_RE.match(oid):
        errors["counter_oid"] = "Enter an OID like 1.3.6.1.2.1.43.10.2.1.4.1.1."

    cups_url = d.get("cups_url") or "http://127.0.0.1:631"
    cups_url = clean_url(cups_url, ("http", "https"))
    if not cups_url:
        errors["cups_url"] = "Use an address like http://127.0.0.1:631."

    queue = d.get("cups_queue") or ""
    if queue and not _QUEUE_RE.match(queue):
        errors["cups_queue"] = "Queue names use letters, digits and - _ . only."

    state = d.get("host_state_file") or ""
    if state and (not state.startswith("/") or len(state) > 300):
        errors["host_state_file"] = "Use an absolute path, such as /host-state/state."

    clean = {"name": name, "ip": ip, "ipp_url": ipp_url, "snmp_community": community, "snmp_port": port,
             "counter_oid": oid, "cups_url": cups_url, "cups_queue": queue, "host_state_file": state}
    return clean, errors


def default_name(draft: dict, taken: Iterable[str]) -> str:
    """The name a printer gets when none is given: its model (the UI passes it as `model`), else its address.
    Two printers of the same model are told apart as 'Model', 'Model (2)', ..."""
    base = str(draft.get("model") or "").strip() or clean_host(str(draft.get("ip") or "")) or "Printer"
    base = base[:50]
    lowered = {n.lower() for n in taken}
    name, n = base, 1
    while name.lower() in lowered:
        n += 1
        name = f"{base} ({n})"
    return name


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "printer"


class PrinterRegistry:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time):
        self.db, self.clock = db, clock
        db.migrate("core", MIGRATIONS)
        self._lock = threading.Lock()
        self._delete_hooks: list[Callable[[str], None]] = []

    # --- reads
    def list(self) -> list[Printer]:
        rows = self.db.query("SELECT * FROM printers ORDER BY created_at, id")
        return [Printer(**dict(r)) for r in rows]

    def get(self, printer_id: str) -> Printer | None:
        row = self.db.one("SELECT * FROM printers WHERE id=?", (printer_id,))
        return Printer(**dict(row)) if row else None

    def __len__(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM printers")["n"]

    # --- writes
    def add(self, draft: dict) -> Printer:
        with self._lock:
            taken = [p.name for p in self.list()]
            if not str(draft.get("name") or "").strip():  # the name is optional
                draft = {**draft, "name": default_name(draft, taken)}
            clean, errors = validate(draft, taken)
            if errors:
                raise ValidationError(errors)
            ids = {p.id for p in self.list()}
            base, n = slugify(clean["name"]), 1
            pid = base
            while pid in ids:
                n += 1
                pid = f"{base}-{n}"
            printer = Printer(id=pid, created_at=self.clock(), **clean)
            self.db.execute(
                "INSERT INTO printers(id,name,ip,ipp_url,snmp_community,snmp_port,counter_oid,cups_url,"
                "cups_queue,host_state_file,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (printer.id, printer.name, printer.ip, printer.ipp_url, printer.snmp_community,
                 printer.snmp_port, printer.counter_oid, printer.cups_url, printer.cups_queue,
                 printer.host_state_file, printer.created_at))
            return printer

    def update(self, printer_id: str, draft: dict) -> Printer:
        with self._lock:
            current = self.get(printer_id)
            if current is None:
                raise KeyError(printer_id)
            others = [p.name for p in self.list() if p.id != printer_id]
            merged = {**{k: getattr(current, k) for k in EDITABLE}, **draft}
            if "name" in draft and not str(draft["name"] or "").strip():  # blanked: back to the default
                merged["name"] = default_name({**merged, "model": draft.get("model")}, others)
            clean, errors = validate(merged, others)
            if errors:
                raise ValidationError(errors)
            self.db.execute(
                "UPDATE printers SET name=?,ip=?,ipp_url=?,snmp_community=?,snmp_port=?,counter_oid=?,"
                "cups_url=?,cups_queue=?,host_state_file=? WHERE id=?",
                (*(clean[k] for k in EDITABLE), printer_id))
            return replace(current, **clean)

    def on_delete(self, hook: Callable[[str], None]) -> None:
        """Tools register a callback to remove the data they keep per printer."""
        self._delete_hooks.append(hook)

    def delete(self, printer_id: str) -> bool:
        with self._lock:
            if self.get(printer_id) is None:
                return False
            for hook in self._delete_hooks:
                hook(printer_id)
            self.db.execute("DELETE FROM printers WHERE id=?", (printer_id,))
            return True
