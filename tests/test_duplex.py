"""Printing multi-page files on both sides: a per-printer choice, applied only where it makes sense."""
import io

import pytest

from app.tools.maintenance.runtimes import Runtimes
from conftest import PID, csrf_headers

PNG = b"\x89PNG\r\n\x1a\n" + b"a" * 200
PDF1 = b"%PDF-1.4\n1 0 obj<</Type/Page>>endobj\n"
PDF3 = b"%PDF-1.4\n1 0 obj<</Type/Page>>endobj\n2 0 obj<</Type/Page>>endobj\n3 0 obj<</Type/Page>>endobj\n"
LONG, SHORT = "two-sided-long-edge", "two-sided-short-edge"


@pytest.fixture
def rig(make_app, clock):
    app, backend = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    client = app.test_client()
    h = csrf_headers(client)

    def upload(data, name):
        r = client.post("/api/maintenance/pages", data={"file": (io.BytesIO(data), name)}, headers=h, content_type="multipart/form-data")
        assert r.status_code == 201
        return r.get_json()["data"]["id"]

    def sides(value):
        r = client.put(f"/api/maintenance/{PID}/automation", json={"sides": value}, headers=h)
        return r

    def print_page(page=None):
        r = client.post(f"/api/maintenance/{PID}/nozzle/print", json={"page": page} if page else {}, headers=h)
        assert r.status_code == 202, r.get_json()
        return backend.submitted[-1]

    return type("Rig", (), dict(client=client, h=h, backend=backend, upload=staticmethod(upload), sides=staticmethod(sides),
                                print_page=staticmethod(print_page), app=app, clock=clock))


def test_the_default_is_one_side_and_nothing_extra_is_sent(rig):
    book = rig.upload(PDF3, "Book")
    assert rig.print_page(book)["sides"] is None


def test_a_multi_page_pdf_prints_on_both_sides_when_chosen(rig):
    book = rig.upload(PDF3, "Book")
    assert rig.sides(LONG).status_code == 200
    assert rig.print_page(book)["sides"] == LONG
    rig.sides(SHORT)
    assert rig.print_page(book)["sides"] == SHORT


def test_single_pages_and_pictures_are_left_alone(rig):
    one, pic = rig.upload(PDF1, "One"), rig.upload(PNG, "Picture")
    rig.sides(LONG)
    assert rig.print_page(one)["sides"] is None and rig.print_page(pic)["sides"] is None
    assert rig.print_page()["sides"] is None  # the standard test page is one sheet


def test_the_rotation_and_the_automatic_print_use_it_too(rig):
    book = rig.upload(PDF3, "Book")
    rig.client.put(f"/api/maintenance/{PID}/automation", json={"rotate": True, "enabled": True, "idle_days": 0.5, "run_at": "00:00"}, headers=rig.h)
    rig.client.put(f"/api/maintenance/{PID}/rotation", json={"items": [{"id": book, "enabled": True}, {"id": "standard", "enabled": False}]}, headers=rig.h)
    rig.sides(LONG)
    rig.clock.advance(3 * 86400)
    svc = Runtimes(rig.app.extensions["panda"]["ctx"]).get(PID).service
    svc.tracker.poll()
    assert svc.run_schedule().action == "print"
    assert rig.backend.submitted[-1]["sides"] == LONG and "(auto)" in rig.backend.submitted[-1]["name"]


def test_a_queue_without_two_sided_printing_is_never_asked_for_it(rig):
    book = rig.upload(PDF3, "Book")
    rig.backend.sides_supported = ("one-sided",)
    rig.client.application.extensions["panda"]["ctx"]  # (same fake backend the API uses)
    rig.sides(LONG)
    rot = rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]
    assert rot["duplex_supported"] is False and rot["sides"] == LONG
    assert rig.print_page(book)["sides"] is None  # the setting stays, but we don't send what the queue can't do


def test_the_page_says_two_sided_is_available_when_the_queue_offers_it(rig):
    rot = rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]
    assert rot["duplex_supported"] is True and rot["sides"] == "one-sided"


def test_only_the_standard_choices_are_accepted(rig):
    r = rig.sides("two-sided-sideways")
    assert r.status_code == 400 and "sides" in r.get_json()["error"]["fields"]
    assert rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]["sides"] == "one-sided"


def test_reset_to_defaults_keeps_the_choice_like_the_other_choices(rig):
    rig.sides(SHORT)
    d = rig.client.delete(f"/api/maintenance/{PID}/automation", headers=rig.h).get_json()["data"]
    assert d["values"]["sides"] == SHORT and d["customised"] == []  # not counted as a "changed setting"


def test_each_printer_has_its_own_choice(rig):
    other = rig.client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=rig.h).get_json()["data"]["id"]
    rig.sides(LONG)
    assert rig.client.get(f"/api/maintenance/{other}/rotation").get_json()["data"]["sides"] == "one-sided"


def test_without_cups_nothing_is_offered(make_app):
    app, _ = make_app()
    c = app.test_client()
    c.put(f"/api/printers/{PID}", json={"cups_queue": ""}, headers=csrf_headers(c))
    rot = c.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]
    assert rot["cups_configured"] is False and rot["duplex_supported"] is False


# ---- unattended printing: only a printer that flips the paper BY ITSELF may be asked for both sides

def test_a_printer_without_an_automatic_duplexer_is_never_asked_even_if_the_queue_claims_it(rig):
    book = rig.upload(PDF3, "Book")
    rig.backend.printer_sides = ("one-sided",)  # no duplexer in the printer; a driver in CUPS may still list two-sided
    rig.sides(LONG)
    rot = rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]
    assert rot["duplex_supported"] is False
    assert rig.print_page(book)["sides"] is None


def test_both_the_printer_and_the_queue_must_offer_it(rig):
    book = rig.upload(PDF3, "Book")
    rig.sides(LONG)
    assert rig.print_page(book)["sides"] == LONG  # both do
    rig.backend.sides_supported = ("one-sided",)
    rig.clock.advance(60)
    assert rig.print_page(book)["sides"] is None  # only the printer does: not enough


def test_a_switched_off_printer_keeps_its_last_known_ability(rig):
    ctx = rig.app.extensions["panda"]["ctx"]
    assert Runtimes(ctx).get(PID).service.automatic_duplex() is True  # asked while it answers: remembered
    rig.backend.fail = ("printer",)  # now it is off
    fresh = Runtimes(ctx).get(PID).service  # new process: no cached answers, only the remembered one
    assert fresh.automatic_duplex() is True


def test_a_printer_never_confirmed_and_off_is_treated_as_not_capable(make_app):
    app, _ = make_app(fail=("printer",))
    svc = Runtimes(app.extensions["panda"]["ctx"]).get(PID).service
    assert svc.automatic_duplex() is False  # unknown means no: it would be printing unattended
