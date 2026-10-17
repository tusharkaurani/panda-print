import socket
import threading

import pytest

from app.printer import snmp

OID = "1.3.6.1.2.1.43.10.2.1.4.1.1"


def request_id(packet: bytes) -> int:
    _, msg, _ = snmp._read(packet, 0)
    _, _, p = snmp._read(msg, 0)
    _, _, p = snmp._read(msg, p)
    _, pdu, _ = snmp._read(msg, p)
    _, rid, _ = snmp._read(pdu, 0)
    return int.from_bytes(rid, "big")


def response(rid: int, value_tag: int, value: bytes, error_status: int = 0) -> bytes:
    varbind = snmp.tlv(snmp.SEQUENCE, snmp.enc_oid(OID) + snmp.tlv(value_tag, value))
    pdu = snmp.tlv(snmp.GET_RESPONSE, snmp.enc_int(rid) + snmp.enc_int(error_status) + snmp.enc_int(0)
                   + snmp.tlv(snmp.SEQUENCE, varbind))
    return snmp.tlv(snmp.SEQUENCE, snmp.enc_int(0) + snmp.tlv(snmp.OCTET_STRING, b"public") + pdu)


def serve(reply):
    """UDP responder on localhost; reply(rid) -> bytes | None. Returns (port, received packets)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)
    received = []

    def run():
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            return
        received.append(data)
        out = reply(request_id(data))
        if out:
            sock.sendto(out, addr)
        sock.close()

    threading.Thread(target=run, daemon=True).start()
    return sock.getsockname()[1], received


def test_request_encoding():
    packet = snmp.build_get(OID, "public", 1234)
    assert packet[0] == 0x30 and b"public" in packet
    assert request_id(packet) == 1234


def test_oid_encoding_multibyte_arcs():
    assert snmp.enc_oid("1.3.6.1.4.1.11.2.3.9.4.2") == bytes.fromhex("06 0b 2b 06 01 04 01 0b 02 03 09 04 02".replace(" ", ""))
    assert snmp.enc_oid("1.3.6.1.2.1.200.300") == bytes.fromhex("06 09 2b 06 01 02 01 81 48 82 2c".replace(" ", ""))


@pytest.mark.parametrize("tag,raw,expected", [
    (0x41, b"\x00\xae", 174),            # Counter32 (high bit set, so BER adds a zero byte)
    (0x41, b"\x01\x11\x70", 70000),
    (0x42, b"\x05", 5),                  # Gauge32
    (0x02, b"\xff", -1),                 # Integer is signed
])
def test_reads_counter_value_types(tag, raw, expected):
    port, _ = serve(lambda rid: response(rid, tag, raw))
    assert snmp.get_counter("127.0.0.1", OID, "public", port, timeout=2) == expected


def test_no_such_object_is_an_error():
    port, _ = serve(lambda rid: response(rid, 0x80, b""))
    with pytest.raises(snmp.SnmpError, match="does not provide"):
        snmp.get_counter("127.0.0.1", OID, port=port, timeout=2)


def test_error_status_is_an_error():
    port, _ = serve(lambda rid: response(rid, 0x05, b"", error_status=2))
    with pytest.raises(snmp.SnmpError, match="noSuchName"):
        snmp.get_counter("127.0.0.1", OID, port=port, timeout=2)


def test_stray_reply_with_wrong_request_id_is_ignored_until_timeout():
    port, _ = serve(lambda rid: response(rid + 1, 0x41, b"\x01"))
    with pytest.raises(socket.timeout):
        snmp.get_counter("127.0.0.1", OID, port=port, timeout=0.5)


def test_silence_times_out():
    port, _ = serve(lambda rid: None)
    with pytest.raises(socket.timeout):
        snmp.get_counter("127.0.0.1", OID, port=port, timeout=0.5)
