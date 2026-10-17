"""A minimal IPP/1.1 client (RFC 8010) over stdlib http.client.

Only what panda-print needs: encode a request, decode a response (including
collections), and POST it. No CUPS client tools are required in the container.
"""
from __future__ import annotations

import http.client
import itertools
import ssl
import struct
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

# --- operations
PRINT_JOB = 0x0002
GET_JOBS = 0x000A
GET_PRINTER_ATTRIBUTES = 0x000B
CUPS_GET_PRINTERS = 0x4002

# --- delimiter tags
OPERATION_ATTRS, JOB_ATTRS, END_OF_ATTRS, PRINTER_ATTRS = 0x01, 0x02, 0x03, 0x04

# --- value tags
INTEGER, BOOLEAN, ENUM = 0x21, 0x22, 0x23
OCTET_STRING, DATETIME, RESOLUTION, RANGE = 0x30, 0x31, 0x32, 0x33
BEG_COLLECTION, END_COLLECTION, MEMBER_NAME = 0x34, 0x37, 0x4A
TEXT, NAME, KEYWORD, URI, URI_SCHEME, CHARSET, LANGUAGE, MIME_TYPE = 0x41, 0x42, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49
OUT_OF_BAND = {0x10, 0x12, 0x13, 0x14, 0x15}  # unsupported, unknown, no-value, ...

STATUS_NAMES = {
    0x0400: "bad request", 0x0401: "forbidden", 0x0402: "not authenticated", 0x0403: "not authorized",
    0x0404: "not possible", 0x0405: "timeout", 0x0406: "not found", 0x0407: "gone",
    0x0408: "request entity too large", 0x040A: "document format not supported",
    0x0500: "internal error", 0x0501: "operation not supported", 0x0502: "service unavailable",
    0x0504: "printer is deactivated", 0x0507: "busy", 0x0508: "job canceled",
}

_request_ids = itertools.count(1)
_request_lock = threading.Lock()


class IppError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class IppResponse:
    status: int
    request_id: int
    # groups in order: (delimiter tag, {attribute name: [values]})
    groups: list[tuple[int, dict[str, list[Any]]]] = field(default_factory=list)

    def of(self, tag: int) -> list[dict[str, list[Any]]]:
        return [attrs for t, attrs in self.groups if t == tag]


# ----------------------------------------------------------------- encoding

def _attr(tag: int, name: str, value: Any, first: bool) -> bytes:
    if tag in (INTEGER, ENUM):
        raw = struct.pack(">i", value)
    elif tag == BOOLEAN:
        raw = b"\x01" if value else b"\x00"
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("utf-8")
    n = name.encode() if first else b""
    return struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(raw)) + raw


def encode_request(op: int, groups: list[tuple[int, list[tuple[int, str, Any]]]],
                   request_id: int, data: bytes = b"") -> bytes:
    """groups: [(delimiter tag, [(value tag, name, value | [values])])]"""
    out = bytearray(struct.pack(">BBHI", 1, 1, op, request_id))
    for delim, attrs in groups:
        out.append(delim)
        for tag, name, value in attrs:
            values = value if isinstance(value, (list, tuple)) else [value]
            for i, v in enumerate(values):
                out += _attr(tag, name, v, first=(i == 0))
    out.append(END_OF_ATTRS)
    return bytes(out) + data


# ----------------------------------------------------------------- decoding

def _datetime(raw: bytes) -> str:
    try:
        y, mo, d, h, mi, s, ds, sign, oh, om = struct.unpack(">HBBBBBBcBB", raw[:11])
        off = timedelta(hours=oh, minutes=om) * (-1 if sign == b"-" else 1)
        return datetime(y, mo, d, h, mi, s, ds * 100000, tzinfo=timezone(off)).isoformat()
    except (struct.error, ValueError):
        return raw.hex()


def _value(tag: int, raw: bytes) -> Any:
    if tag in (INTEGER, ENUM) and len(raw) == 4:
        return struct.unpack(">i", raw)[0]
    if tag == BOOLEAN:
        return raw != b"\x00"
    if tag == DATETIME:
        return _datetime(raw)
    if tag == RESOLUTION and len(raw) == 9:
        x, y, u = struct.unpack(">iiB", raw)
        return (x, y, "dpi" if u == 3 else "dpcm")
    if tag == RANGE and len(raw) == 8:
        return struct.unpack(">ii", raw)
    if 0x40 <= tag <= 0x4F or tag in (0x35, 0x36):  # text, name, keyword, uri, charset, ...
        return raw.decode("utf-8", "replace")
    if tag in OUT_OF_BAND:
        return None
    return raw


def _parse_collection(data: bytes, pos: int) -> tuple[dict[str, list[Any]], int]:
    """Parse members after a begCollection until the matching endCollection."""
    members: dict[str, list[Any]] = {}
    name: str | None = None
    while pos < len(data):
        tag = data[pos]
        nlen = struct.unpack_from(">H", data, pos + 1)[0]
        pos += 3 + nlen
        vlen = struct.unpack_from(">H", data, pos)[0]
        raw = data[pos + 2: pos + 2 + vlen]
        pos += 2 + vlen
        if tag == END_COLLECTION:
            return members, pos
        if tag == MEMBER_NAME:
            name = raw.decode("utf-8", "replace")
            members.setdefault(name, [])
        elif name is not None:
            if tag == BEG_COLLECTION:
                val, pos = _parse_collection(data, pos)
            else:
                val = _value(tag, raw)
            members[name].append(val)
    raise IppError("truncated IPP collection")


def decode_response(data: bytes) -> IppResponse:
    if len(data) < 9:
        raise IppError("IPP response too short")
    _major, _minor, status, request_id = struct.unpack_from(">BBHI", data, 0)
    resp = IppResponse(status, request_id)
    pos = 8
    current: dict[str, list[Any]] | None = None
    last_name: str | None = None
    try:
        while pos < len(data):
            tag = data[pos]
            pos += 1
            if tag == END_OF_ATTRS:
                break
            if tag <= 0x0F:  # begin-attribute-group
                current = {}
                resp.groups.append((tag, current))
                last_name = None
                continue
            nlen = struct.unpack_from(">H", data, pos)[0]
            name = data[pos + 2: pos + 2 + nlen].decode("utf-8", "replace")
            pos += 2 + nlen
            vlen = struct.unpack_from(">H", data, pos)[0]
            raw = data[pos + 2: pos + 2 + vlen]
            pos += 2 + vlen
            if current is None:
                raise IppError("IPP attribute outside of a group")
            if tag == BEG_COLLECTION:
                val, pos = _parse_collection(data, pos)
            else:
                val = _value(tag, raw)
            if name:
                last_name = name
                current.setdefault(name, []).append(val)
            elif last_name:  # additional value of a 1setOf attribute
                current[last_name].append(val)
    except struct.error:
        raise IppError("malformed IPP response") from None
    return resp


# ----------------------------------------------------------------- transport

class IppClient:
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    @staticmethod
    def http_url(url: str) -> str:
        """ipp://host[:port]/path -> http://host:port/path (631 is the IPP default)."""
        parts = urlsplit(url)
        if parts.scheme == "ipp":
            return f"http://{parts.hostname}:{parts.port or 631}{parts.path or '/'}"
        if parts.scheme in ("http", "https"):
            return url
        raise IppError(f"Unsupported URL scheme {parts.scheme!r}; use ipp://, http:// or https://")

    def post(self, url: str, body: bytes) -> bytes:
        parts = urlsplit(self.http_url(url))
        if parts.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                parts.hostname, parts.port or 443, timeout=self.timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=self.timeout)
        try:
            conn.request("POST", parts.path or "/", body=body,
                         headers={"Content-Type": "application/ipp", "Accept": "application/ipp"})
            resp = conn.getresponse()
            payload = resp.read()
            if resp.status == 426:
                raise IppError("The server requires an encrypted connection (use an https:// CUPS_URL).")
            if resp.status in (401, 403):
                raise IppError(f"The server refused access (HTTP {resp.status}). Check its access rules.")
            if resp.status != 200:
                raise IppError(f"Unexpected HTTP {resp.status} {resp.reason} from the server.")
            return payload
        finally:
            conn.close()

    def request(self, url: str, op: int, printer_uri: str | None, op_attrs: list[tuple[int, str, Any]],
                data: bytes = b"") -> IppResponse:
        with _request_lock:
            rid = next(_request_ids)
        base = [(CHARSET, "attributes-charset", "utf-8"), (LANGUAGE, "attributes-natural-language", "en")]
        if printer_uri:
            base.append((URI, "printer-uri", printer_uri))
        body = encode_request(op, [(OPERATION_ATTRS, base + op_attrs)], rid, data)
        resp = decode_response(self.post(url, body))
        if resp.status >= 0x0100:
            label = STATUS_NAMES.get(resp.status, f"status 0x{resp.status:04x}")
            raise IppError(f"IPP error: {label}.", resp.status)
        return resp
