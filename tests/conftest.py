import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.printer import ipp

from app.factory import create_app
from app.tools.maintenance.store import Store
from fakes import FakeAccess, seed_history
from app.settings import Settings

T0 = 1_800_000_000.0
PID = "brother-dcp-t730dw"  # id of the default test printer
DEFAULT_PRINTER = {"name": "Brother DCP-T730DW", "ip": "192.168.1.18", "cups_queue": "T730",
                   "ipp_url": "ipp://192.168.1.18/ipp/print"}


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "config").mkdir()
    return Settings(data_dir=tmp_path / "data", config_dir=tmp_path / "config",
                    host_state_file=tmp_path / "host-state" / "state")


def add_default_printer(app, clock, seed=True):
    """Register the default fake printer and (optionally) give it a believable past."""
    ctx = app.extensions["panda"]["ctx"]
    printer = ctx.printers.add(DEFAULT_PRINTER)
    if seed:
        seed_history(Store(ctx.db, printer.id), clock())
    return ctx.backend_for(printer)


@pytest.fixture
def make_app(settings, clock):
    """make_app(fail=(), printer=True, **setting_overrides) -> (flask app, fake backend of the default printer).
    `fail` lists sources ("printer", "snmp", "cups") that should raise; printer=False starts with no printers."""
    import dataclasses

    def _make(fail=(), printer=True, **overrides):
        s = dataclasses.replace(settings, **overrides) if overrides else settings
        app = create_app(s, access=FakeAccess(s, clock, fail=fail), start_background=False, clock=clock)
        return app, (add_default_printer(app, clock) if printer else None)

    return _make


@pytest.fixture
def client(make_app):
    return make_app()[0].test_client()


def csrf_headers(client):
    token = client.get("/api/csrf").get_json()["token"]
    return {"X-CSRF-Token": token}


class FakeCups(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        req = self.rfile.read(int(self.headers["Content-Length"]))
        op = struct.unpack(">H", req[2:4])[0]
        parsed = ipp.decode_response(req)  # same wire layout as a request
        self.seen.append((self.path, op, parsed.of(ipp.OPERATION_ATTRS)[0], req))
        op_group = (ipp.OPERATION_ATTRS, [(ipp.CHARSET, "attributes-charset", "utf-8")])
        groups, status = [op_group], 0
        if op == ipp.GET_PRINTER_ATTRIBUTES:
            groups.append((ipp.PRINTER_ATTRS, [(ipp.NAME, "printer-name", "Brother"),
                                               (ipp.TEXT, "printer-make-and-model", "Brother DCP-T730DW"),
                                               (ipp.NAME, "printer-info", "Brother DCP-T730DW"),
                                               (ipp.ENUM, "printer-state", 3),
                                               (ipp.INTEGER, "marker-levels", [94, 95, 94, 95]),
                                               (ipp.KEYWORD, "marker-names", ["M", "C", "Y", "BK"])]))
        elif op == ipp.CUPS_GET_PRINTERS:
            for name in ("Other", "T730"):
                groups.append((ipp.PRINTER_ATTRS, [(ipp.NAME, "printer-name", name), (ipp.ENUM, "printer-state", 3),
                                                   (ipp.URI, "device-uri", "ipp://127.0.0.1/ipp/print" if name == "T730" else "usb://Foo/Bar")]))
        elif op == ipp.GET_JOBS:
            which = parsed.of(ipp.OPERATION_ATTRS)[0]["which-jobs"][0]
            jid = 10 if which == "completed" else 11
            groups.append((ipp.JOB_ATTRS, [(ipp.INTEGER, "job-id", jid), (ipp.NAME, "job-name", which)]))
        elif op == ipp.PRINT_JOB:
            groups.append((ipp.JOB_ATTRS, [(ipp.INTEGER, "job-id", 77)]))
        if self.path.endswith("/printers/MISSING"):
            status = 0x0406
        body = ipp.encode_request(status, groups, parsed.request_id)
        self.send_response(200)
        self.send_header("Content-Type", "application/ipp")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def cups():
    FakeCups.seen = []
    server = HTTPServer(("127.0.0.1", 0), FakeCups)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()
