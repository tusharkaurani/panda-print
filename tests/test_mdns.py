import socket
import struct

from app.printer import mdns

INST = "Brother DCP-T730DW._ipp._tcp.local"


def nm(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"


def rr(name: bytes, rtype: int, rdata: bytes) -> bytes:
    return name + struct.pack(">HHIH", rtype, 0x8001, 120, len(rdata)) + rdata


def packet(answers: list[bytes], extra: list[bytes] = ()) -> bytes:
    return struct.pack(">HHHHHH", 0, 0x8400, 0, len(answers), 0, len(extra)) + b"".join(answers) + b"".join(extra)


def txt(**kv) -> bytes:
    return b"".join(bytes([len(f"{k}={v}")]) + f"{k}={v}".encode() for k, v in kv.items())


def printer_packet(ip="192.168.1.18", port=631, **t) -> bytes:
    return packet(
        [rr(nm(mdns.SERVICE), mdns.PTR, nm(INST))],
        [rr(nm(INST), mdns.SRV, struct.pack(">HHH", 0, 0, port) + nm("BRW1234.local")),
         rr(nm(INST), mdns.TXT, txt(rp="ipp/print", ty="Brother DCP-T730DW", **t)),
         rr(nm("BRW1234.local"), mdns.A, socket.inet_aton(ip))])


def test_query_asks_for_ipp_services():
    q = mdns.build_query()
    assert q[4:6] == b"\x00\x01" and b"\x04_ipp\x04_tcp\x05local\x00" in q


def test_finds_a_printer_from_a_full_response():
    found = mdns.candidates([(mdns.parse_response(printer_packet()), "192.168.1.18")])
    assert found == [{"source": "mdns", "name": "Brother DCP-T730DW", "model": "Brother DCP-T730DW",
                      "ip": "192.168.1.18", "ipp_url": "ipp://192.168.1.18/ipp/print"}]


def test_non_default_port_is_kept_in_the_ipp_url():
    found = mdns.candidates([(mdns.parse_response(printer_packet(port=8631)), "192.168.1.18")])
    assert found[0]["ipp_url"] == "ipp://192.168.1.18:8631/ipp/print"


def test_falls_back_to_the_sender_address_when_no_a_record():
    pkt = packet([rr(nm(mdns.SERVICE), mdns.PTR, nm(INST))],
                 [rr(nm(INST), mdns.SRV, struct.pack(">HHH", 0, 0, 631) + nm("x.local"))])
    found = mdns.candidates([(mdns.parse_response(pkt), "192.168.1.77")])
    assert found[0]["ip"] == "192.168.1.77"


def test_compressed_names_are_followed():
    pkt = packet([rr(nm("printer.local"), mdns.A, socket.inet_aton("192.168.1.50")),
                  rr(b"\xc0\x0c", mdns.A, socket.inet_aton("192.168.1.51"))])  # 0xC00C points at the first name
    assert mdns.parse_response(pkt)["a"] == {"printer.local": "192.168.1.51"}


def test_garbage_and_loops_are_survived():
    assert mdns.parse_response(b"\x00\x01") == {"ptr": [], "srv": {}, "txt": {}, "a": {}}
    loop = struct.pack(">HHHHHH", 0, 0, 0, 1, 0, 0) + b"\xc0\x0c" + b"\x00" * 10
    mdns.parse_response(loop)  # must return, not spin


def test_same_printer_answering_twice_is_listed_once():
    parsed = mdns.parse_response(printer_packet())
    assert len(mdns.candidates([(parsed, "192.168.1.18"), (parsed, "192.168.1.18")])) == 1
