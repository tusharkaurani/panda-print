import struct
import pytest

from app.core.errors import SourceError
from conftest import FakeCups
from app.printer import ipp
from app.core.printers import Printer
from app.printer.live import LivePrinter
from app.settings import Settings
from app.tools.maintenance.service import NOZZLE_PS


def test_request_roundtrips_through_the_decoder():
    body = ipp.encode_request(
        ipp.GET_JOBS,
        [(ipp.OPERATION_ATTRS, [(ipp.CHARSET, "attributes-charset", "utf-8"),
                                (ipp.KEYWORD, "requested-attributes", ["job-id", "job-name"]),
                                (ipp.INTEGER, "limit", 5), (ipp.BOOLEAN, "my-jobs", False)])],
        request_id=7)
    r = ipp.decode_response(body)
    attrs = r.of(ipp.OPERATION_ATTRS)[0]
    assert r.status == ipp.GET_JOBS and r.request_id == 7
    assert attrs["requested-attributes"] == ["job-id", "job-name"]
    assert attrs["limit"] == [5] and attrs["my-jobs"] == [False]


def test_decodes_collections_and_additional_values():
    def attr(tag, name, value):
        return struct.pack(">BH", tag, len(name)) + name.encode() + struct.pack(">H", len(value)) + value

    col = (attr(ipp.BEG_COLLECTION, "media-col", b"")
           + attr(ipp.MEMBER_NAME, "", b"media-size")
           + attr(ipp.BEG_COLLECTION, "", b"")
           + attr(ipp.MEMBER_NAME, "", b"x-dimension") + attr(ipp.INTEGER, "", struct.pack(">i", 21000))
           + attr(ipp.END_COLLECTION, "", b"")
           + attr(ipp.END_COLLECTION, "", b""))
    data = (struct.pack(">BBHI", 1, 1, 0, 1) + bytes([ipp.PRINTER_ATTRS]) + col
            + attr(ipp.KEYWORD, "printer-state-reasons", b"none") + attr(ipp.KEYWORD, "", b"toner-low-warning")
            + attr(0x13, "no-value-attr", b"") + bytes([ipp.END_OF_ATTRS]))
    printer = ipp.decode_response(data).of(ipp.PRINTER_ATTRS)[0]
    assert printer["media-col"] == [{"media-size": [{"x-dimension": [21000]}]}]
    assert printer["printer-state-reasons"] == ["none", "toner-low-warning"]
    assert printer["no-value-attr"] == [None]


def test_truncated_response_is_an_ipp_error():
    with pytest.raises(ipp.IppError):
        ipp.decode_response(b"\x01\x01")


def live(port, cups_queue="T730"):
    p = Printer(id="t", name="T", ip="127.0.0.1", ipp_url=f"ipp://127.0.0.1:{port}/ipp/print",
                cups_url=f"http://127.0.0.1:{port}", cups_queue=cups_queue)
    return LivePrinter(Settings(net_timeout=2), p)


def test_live_printer_reads_attributes_queue_and_jobs(cups):
    p = live(cups)
    info = p.printer_info()
    assert info["marker-levels"] == [94, 95, 94, 95] and info["marker-names"] == ["M", "C", "Y", "BK"]
    assert p.queue_info()["printer-name"] == ["T730"]  # picked our queue out of the list
    assert sorted(j["job-id"][0] for j in p.list_jobs()) == [10, 11]  # completed + not-completed
    # the printer was asked at its own IPP path, CUPS at the root
    paths = [s[0] for s in FakeCups.seen]
    assert paths[0] == "/ipp/print" and paths[1] == "/"


def test_live_printer_submits_postscript_via_print_job(cups):
    p = live(cups)
    assert p.submit_document("panda-print nozzle check (manual)", NOZZLE_PS, "panda-print") == 77
    path, op, attrs, raw = FakeCups.seen[-1]
    assert path == "/printers/T730" and op == ipp.PRINT_JOB
    assert attrs["document-format"] == ["application/postscript"]
    assert attrs["job-name"] == ["panda-print nozzle check (manual)"]
    assert attrs["printer-uri"] == [f"ipp://127.0.0.1:{cups}/printers/T730"]
    assert raw.endswith(NOZZLE_PS)  # the document follows the IPP header


def test_missing_queue_is_a_friendly_error(cups):
    with pytest.raises(SourceError, match="no queue named 'Nope'.*Other, T730"):
        live(cups, cups_queue="Nope").queue_info()


def test_ipp_error_status_is_explained(cups):
    with pytest.raises(SourceError, match="not found"):
        live(cups, cups_queue="MISSING").submit_document("x", NOZZLE_PS, "u")


def test_unreachable_printer_is_a_friendly_error():
    p = live(1)  # nothing listens on port 1
    with pytest.raises(SourceError) as e:
        p.printer_info()
    assert e.value.source == "printer" and "refused" in e.value.message


def test_a_pdf_is_sent_with_its_own_type_and_scaled_to_fit(cups):
    p = live(cups)
    pdf = b"%PDF-1.4\n%fake\n"
    assert p.submit_document("panda-print nozzle check (auto) - Bunny", pdf, "panda-print", "application/pdf") == 77
    path, op, attrs, raw = FakeCups.seen[-1]
    assert op == ipp.PRINT_JOB and attrs["document-format"] == ["application/pdf"]
    job = ipp.decode_response(raw).of(ipp.JOB_ATTRS)
    assert job and job[0]["print-scaling"] == ["fit"]  # a picture must not be cropped by the paper size
    assert raw.endswith(pdf)


def test_postscript_is_sent_without_scaling_attributes(cups):
    live(cups).submit_document("x", NOZZLE_PS, "u")
    assert ipp.decode_response(FakeCups.seen[-1][3]).of(ipp.JOB_ATTRS) == []
