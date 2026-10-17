"""Find network printers with mDNS / DNS-SD (Bonjour), stdlib only.

We ask the LAN for `_ipp._tcp.local` services and read the answers. Needs
multicast to reach the network: fine with host networking, usually not inside a
bridged Docker network (the wizard then falls back to CUPS and manual entry).
"""
from __future__ import annotations

import socket
import struct
import time

MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353
SERVICE = "_ipp._tcp.local"
PTR, TXT, A, SRV = 12, 16, 1, 33


def build_query(service: str = SERVICE) -> bytes:
    name = b"".join(bytes([len(p)]) + p.encode() for p in service.split(".")) + b"\x00"
    # QU bit (0x8000) asks responders to answer us directly instead of by multicast
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + name + struct.pack(">HH", PTR, 0x8001)


def _name(data: bytes, pos: int) -> tuple[str, int]:
    labels, end, jumps = [], None, 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated name")
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:  # compression pointer
            if end is None:
                end = pos + 2
            pos = ((n & 0x3F) << 8) | data[pos + 1]
            jumps += 1
            if jumps > 20:
                raise ValueError("name loop")
            continue
        labels.append(data[pos + 1: pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
    return ".".join(labels), (end if end is not None else pos)


def parse_response(data: bytes) -> dict:
    """-> {'ptr': [instance...], 'srv': {instance: (port, target)}, 'txt': {instance: {k: v}}, 'a': {host: ip}}"""
    out: dict = {"ptr": [], "srv": {}, "txt": {}, "a": {}}
    try:
        _id, _flags, qd, an, ns, ar = struct.unpack_from(">HHHHHH", data, 0)
        pos = 12
        for _ in range(qd):
            _, pos = _name(data, pos)
            pos += 4
        for _ in range(an + ns + ar):
            name, pos = _name(data, pos)
            rtype, _cls, _ttl, rdlen = struct.unpack_from(">HHIH", data, pos)
            pos += 10
            rdata = data[pos: pos + rdlen]
            if rtype == PTR:
                out["ptr"].append(_name(data, pos)[0])
            elif rtype == SRV and rdlen >= 6:
                port = struct.unpack_from(">H", rdata, 4)[0]
                out["srv"][name] = (port, _name(data, pos + 6)[0])
            elif rtype == TXT:
                kv, i = {}, 0
                while i < len(rdata):
                    n = rdata[i]
                    item = rdata[i + 1: i + 1 + n].decode("utf-8", "replace")
                    k, _, v = item.partition("=")
                    if k:
                        kv[k.lower()] = v
                    i += 1 + n
                out["txt"][name] = kv
            elif rtype == A and rdlen == 4:
                out["a"][name] = socket.inet_ntoa(rdata)
            pos += rdlen
    except (struct.error, ValueError, IndexError):
        pass  # a malformed packet from some device on the LAN: keep what we parsed
    return out


def candidates(records: list[tuple[dict, str]]) -> list[dict]:
    """Merge parsed responses ([(parsed, sender ip)]) into printer candidates."""
    ptr: set[str] = set()
    srv, txt, addr, sender = {}, {}, {}, {}
    for parsed, ip in records:
        ptr.update(parsed["ptr"])
        srv.update(parsed["srv"])
        txt.update(parsed["txt"])
        addr.update(parsed["a"])
        for inst in list(parsed["srv"]) + list(parsed["ptr"]):
            sender.setdefault(inst, ip)
    found: dict[str, dict] = {}
    for inst in sorted(ptr | {i for i in srv if i.endswith(SERVICE)}):
        if not inst.endswith("." + SERVICE):
            continue
        port, target = srv.get(inst, (631, ""))
        ip = addr.get(target) or sender.get(inst)
        if not ip:
            continue
        info = txt.get(inst, {})
        label = inst[: -len(SERVICE) - 1]
        model = info.get("ty") or info.get("product", "").strip("()") or label
        rp = (info.get("rp") or "ipp/print").lstrip("/")
        found.setdefault(ip, {
            "source": "mdns", "name": label, "model": model, "ip": ip,
            "ipp_url": f"ipp://{ip}{'' if port == 631 else f':{port}'}/{rp}",
        })
    return list(found.values())


def browse(timeout: float = 2.5) -> tuple[list[dict], str | None]:
    """Ask the LAN for printers. Returns (candidates, note about why there may be none)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.bind(("", 0))
        query = build_query()
        got: list[tuple[dict, str]] = []
        deadline = time.monotonic() + timeout
        next_send = 0.0
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if now >= next_send:
                sock.sendto(query, (MDNS_GROUP, MDNS_PORT))
                next_send = now + 1.0
            sock.settimeout(max(0.05, min(next_send, deadline) - time.monotonic()))
            try:
                data, (ip, _port) = sock.recvfrom(9000)
            except socket.timeout:
                continue
            got.append((parse_response(data), ip))
        return candidates(got), None
    except OSError as e:
        return [], f"Network discovery isn't available here ({e.strerror or e})."
    finally:
        sock.close()
