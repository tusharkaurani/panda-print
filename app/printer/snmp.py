"""SNMPv1 GET for a single OID (read-only), stdlib only.

Derived from the host's nozzle-check.py, but with a real BER parse of the reply
instead of scanning for the last TLV, and with the request id checked.
"""
from __future__ import annotations

import random
import socket
import time

INTEGER, OCTET_STRING, NULL, OID, SEQUENCE = 0x02, 0x04, 0x05, 0x06, 0x30
GET_REQUEST, GET_RESPONSE = 0xA0, 0xA2
NUMERIC = {0x02, 0x41, 0x42, 0x43, 0x46}  # Integer, Counter32, Gauge32, TimeTicks, Counter64
EXCEPTIONS = {0x80: "noSuchObject", 0x81: "noSuchInstance", 0x82: "endOfMibView"}
ERROR_STATUS = {1: "tooBig", 2: "noSuchName", 3: "badValue", 4: "readOnly", 5: "genErr"}


class SnmpError(Exception):
    pass


def tlv(t: int, v: bytes) -> bytes:
    n = len(v)
    if n < 128:
        length = bytes([n])
    elif n < 256:
        length = bytes([0x81, n])
    else:
        length = bytes([0x82, n >> 8, n & 0xFF])
    return bytes([t]) + length + v


def enc_int(n: int) -> bytes:
    raw = n.to_bytes((n.bit_length() // 8) + 1, "big", signed=True)
    return tlv(INTEGER, raw)


def enc_oid(s: str) -> bytes:
    p = [int(x) for x in s.strip(".").split(".")]
    b = bytes([p[0] * 40 + p[1]])
    for n in p[2:]:
        c = [n & 0x7F]
        n >>= 7
        while n:
            c.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        b += bytes(c)
    return tlv(OID, b)


def build_get(oid: str, community: str, request_id: int) -> bytes:
    varbind = tlv(SEQUENCE, enc_oid(oid) + tlv(NULL, b""))
    pdu = tlv(GET_REQUEST, enc_int(request_id) + enc_int(0) + enc_int(0) + tlv(SEQUENCE, varbind))
    return tlv(SEQUENCE, enc_int(0) + tlv(OCTET_STRING, community.encode()) + pdu)  # version 0 = SNMPv1


def _read(buf: bytes, pos: int) -> tuple[int, bytes, int]:
    """Read one TLV at pos -> (tag, value, position after it)."""
    if pos + 2 > len(buf):
        raise SnmpError("truncated SNMP reply")
    tag, n = buf[pos], buf[pos + 1]
    pos += 2
    if n & 0x80:
        k = n & 0x7F
        if k == 0 or k > 3 or pos + k > len(buf):
            raise SnmpError("bad length in SNMP reply")
        n = int.from_bytes(buf[pos:pos + k], "big")
        pos += k
    if pos + n > len(buf):
        raise SnmpError("truncated SNMP reply")
    return tag, buf[pos:pos + n], pos + n


def parse_response(buf: bytes) -> tuple[int, int]:
    """Return (request_id, value) from a GetResponse carrying one numeric varbind."""
    tag, msg, _ = _read(buf, 0)
    if tag != SEQUENCE:
        raise SnmpError("not an SNMP message")
    _, _version, p = _read(msg, 0)
    _, _community, p = _read(msg, p)
    tag, pdu, _ = _read(msg, p)
    if tag != GET_RESPONSE:
        raise SnmpError(f"unexpected SNMP PDU type 0x{tag:02x}")
    _, rid, p = _read(pdu, 0)
    _, err, p = _read(pdu, p)
    _, _idx, p = _read(pdu, p)
    status = int.from_bytes(err, "big", signed=True)
    if status:
        raise SnmpError(f"printer reported SNMP error {ERROR_STATUS.get(status, status)}")
    _, varbinds, _ = _read(pdu, p)
    _, varbind, _ = _read(varbinds, 0)
    _, _oid, p = _read(varbind, 0)
    vtag, val, _ = _read(varbind, p)
    if vtag in EXCEPTIONS:
        raise SnmpError(f"printer does not provide this counter ({EXCEPTIONS[vtag]})")
    if vtag not in NUMERIC:
        raise SnmpError(f"unexpected SNMP value type 0x{vtag:02x}")
    return int.from_bytes(rid, "big", signed=True), int.from_bytes(val, "big", signed=(vtag == 0x02))


def get_counter(host: str, oid: str, community: str = "public", port: int = 161, timeout: float = 5.0) -> int:
    rid = random.randint(1, 2**30)
    packet = build_get(oid, community, rid)
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(packet, (host, port))
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise socket.timeout()
            s.settimeout(left)
            data, _ = s.recvfrom(2048)
            got_rid, value = parse_response(data)
            if got_rid == rid:  # ignore stray datagrams
                return value
