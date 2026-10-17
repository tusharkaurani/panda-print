"""Uploading pages, ordering them per printer, and printing them in rotation (all against the fake printer)."""
import io

import pytest

from app.tools.maintenance.runtimes import Runtimes
from conftest import PID, csrf_headers

PNG = b"\x89PNG\r\n\x1a\n" + b"a" * 200
PNG2 = b"\x89PNG\r\n\x1a\n" + b"b" * 200
PDF2 = b"%PDF-1.4\n1 0 obj<</Type/Page>>endobj\n2 0 obj<</Type/Page>>endobj\n"  # two pages
POSTSCRIPT = b"%!PS\n/Times-Roman findfont 24 scalefont setfont showpage\n"


@pytest.fixture
def rig(make_app, clock):
    app, printer = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    client = app.test_client()
    h = csrf_headers(client)
    ctx = app.extensions["panda"]["ctx"]

    def upload(data, name, headers=h, expect=201):
        r = client.post("/api/maintenance/pages", data={"file": (io.BytesIO(data), name)}, headers=headers,
                        content_type="multipart/form-data")
        assert r.status_code == expect, r.get_json()
        return r.get_json()

    def put(path, body):
        return client.put(path, json=body, headers=h)

    def print_now(page=None, pid=PID):
        return client.post(f"/api/maintenance/{pid}/nozzle/print", json={"page": page} if page else {}, headers=h)

    def sent():  # what was printed, as (mime, page name), skipping the seeded history
        return [(d["mime"], d["name"].split(" - ", 1)[1]) for d in printer.submitted]

    return type("Rig", (), dict(app=app, client=client, h=h, ctx=ctx, printer=printer, clock=clock, upload=staticmethod(upload),
                                put=staticmethod(put), print_now=staticmethod(print_now), sent=staticmethod(sent),
                                runtime=lambda self=None: Runtimes(ctx).get(PID)))


def rotate_on(rig, **extra):
    assert rig.put(f"/api/maintenance/{PID}/automation", {"rotate": True, **extra}).status_code == 200


# ------------------------------------------------------------------ the library

def test_upload_list_and_serve(rig):
    page = rig.upload(PNG, "Bunny colouring.png")["data"]
    assert page["name"] == "Bunny colouring" and page["mime"] == "image/png" and page["pages"] == 1
    assert rig.upload(PDF2, "book.pdf")["data"]["pages"] == 2
    listed = rig.client.get("/api/maintenance/pages").get_json()["data"]
    assert [p["name"] for p in listed["pages"]] == ["Bunny colouring", "book"] and listed["limits"]["max_file_mb"] == 10
    f = rig.client.get(f"/api/maintenance/pages/{page['id']}/file")
    assert f.status_code == 200 and f.data == PNG and f.headers["Content-Type"] == "image/png"
    assert f.headers["X-Content-Type-Options"] == "nosniff" and f.headers["Content-Security-Policy"] == "sandbox"


def test_bad_uploads_are_refused_with_a_reason(rig):
    assert "PDF, PNG and JPEG" in rig.upload(POSTSCRIPT, "page.pdf", expect=415)["error"]["message"]  # content wins over name
    assert rig.upload(b"MZ" + b"0" * 100, "evil.png", expect=415)
    assert "empty" in rig.upload(b"", "none.png", expect=400)["error"]["message"]
    rig.upload(PNG, "a.png")
    assert "already in the library" in rig.upload(PNG, "again.png", expect=409)["error"]["message"]
    assert rig.client.post("/api/maintenance/pages", data={}, headers=rig.h).status_code == 400  # no file


def test_oversized_upload_is_refused_and_a_normal_one_is_not_stopped_by_the_global_limit(make_app):
    app, _ = make_app(max_page_mb=1)
    c = app.test_client()
    h = csrf_headers(c)
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (1024 * 1024 + 10)
    r = c.post("/api/maintenance/pages", data={"file": (io.BytesIO(big), "big.png")}, headers=h,
               content_type="multipart/form-data")
    assert r.status_code == 413 and r.get_json()["error"]["message"] == "That file is larger than 1 MB."
    mid = b"\x89PNG\r\n\x1a\n" + b"y" * 300_000  # far above Flask's 64 KB default, below this route's limit
    assert c.post("/api/maintenance/pages", data={"file": (io.BytesIO(mid), "mid.png")}, headers=h,
                  content_type="multipart/form-data").status_code == 201


def test_changes_need_csrf(rig):
    assert rig.client.post("/api/maintenance/pages", data={"file": (io.BytesIO(PNG), "a.png")},
                           content_type="multipart/form-data").status_code == 403
    page = rig.upload(PNG, "a.png")["data"]
    assert rig.client.delete(f"/api/maintenance/pages/{page['id']}").status_code == 403
    assert rig.client.put(f"/api/maintenance/{PID}/rotation", json={"items": []}).status_code == 403


def test_rename_and_delete(rig):
    page = rig.upload(PNG, "a.png")["data"]
    assert rig.put(f"/api/maintenance/pages/{page['id']}", {"name": "  Dino  "}).get_json()["data"]["name"] == "Dino"
    assert rig.client.delete(f"/api/maintenance/pages/{page['id']}", headers=rig.h).status_code == 200
    assert rig.client.delete(f"/api/maintenance/pages/{page['id']}", headers=rig.h).status_code == 404
    assert rig.client.get(f"/api/maintenance/pages/{page['id']}/file").status_code == 404


# ------------------------------------------------------------------ each printer's order

def test_rotation_lists_the_standard_page_first_then_uploads(rig):
    a = rig.upload(PNG, "A.png")["data"]["id"]
    b = rig.upload(PDF2, "B.pdf")["data"]["id"]
    d = rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]
    assert [(i["name"], i["enabled"]) for i in d["items"]] == [("Standard test page", True), ("A", True), ("B", True)]
    assert d["rotate"] is False and d["next"]["id"] == "standard"


def test_order_and_switches_are_saved_per_printer(rig):
    a = rig.upload(PNG, "A.png")["data"]["id"]
    b = rig.upload(PDF2, "B.pdf")["data"]["id"]
    rig.client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=rig.h)
    r = rig.put(f"/api/maintenance/{PID}/rotation", {"items": [{"id": b, "enabled": True}, {"id": "standard", "enabled": False},
                                                              {"id": a, "enabled": True}]})
    assert [(i["name"], i["enabled"]) for i in r.get_json()["data"]["items"]] == [("B", True), ("Standard test page", False), ("A", True)]
    hall = rig.client.get("/api/maintenance/hall/rotation").get_json()["data"]["items"]  # Hall keeps the default order
    assert [i["name"] for i in hall] == ["Standard test page", "A", "B"]


def test_a_bad_list_is_refused_and_deleting_a_page_removes_it_everywhere(rig):
    a = rig.upload(PNG, "A.png")["data"]["id"]
    assert rig.put(f"/api/maintenance/{PID}/rotation", {"items": [{"id": "nope"}]}).status_code == 400
    assert rig.put(f"/api/maintenance/{PID}/rotation", {"items": [{"id": a}, {"id": a}]}).status_code == 400
    assert rig.put(f"/api/maintenance/{PID}/rotation", {"items": "x"}).status_code == 400
    rig.put(f"/api/maintenance/{PID}/rotation", {"items": [{"id": a, "enabled": True}, {"id": "standard", "enabled": True}]})
    rig.client.delete(f"/api/maintenance/pages/{a}", headers=rig.h)
    assert [i["name"] for i in rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]["items"]] == ["Standard test page"]


# ------------------------------------------------------------------ printing

def test_without_rotation_the_standard_page_always_prints(rig):
    rig.upload(PNG, "A.png")
    rig.print_now(); rig.print_now()
    assert rig.sent() == [("application/postscript", "Standard test page")] * 2


def test_print_now_walks_the_rotation_and_loops(rig):
    rig.upload(PNG, "A.png"); rig.upload(PDF2, "B.pdf")
    rotate_on(rig)
    for _ in range(4):
        assert rig.print_now().status_code == 202
    assert rig.sent() == [("application/postscript", "Standard test page"), ("image/png", "A"),
                          ("application/pdf", "B"), ("application/postscript", "Standard test page")]
    rows = rig.ctx.db.query("SELECT page_name, pages, pages_left FROM mt_checks WHERE printer_id=? AND source='manual' "
                            "AND page_name IS NOT NULL ORDER BY id", (PID,))
    assert [(r["page_name"], r["pages"]) for r in rows] == [("Standard test page", 1), ("A", 1), ("B", 2), ("Standard test page", 1)]
    d = rig.client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["history"][0]["page_name"] == "Standard test page" and d["history"][1]["pages"] == 2


def test_the_next_page_is_shown_before_it_prints(rig):
    rig.upload(PNG, "A.png")
    rotate_on(rig)
    assert rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]["next"]["name"] == "Standard test page"
    rig.print_now()
    assert rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]["next"]["name"] == "A"


def test_printing_a_chosen_page_leaves_the_rotation_where_it_was(rig):
    a = rig.upload(PNG, "A.png")["data"]["id"]; rig.upload(PDF2, "B.pdf")
    rotate_on(rig)
    rig.print_now()  # standard
    rig.print_now(page=a)  # a test print of A: not part of the rotation
    rig.print_now()
    assert [n for _, n in rig.sent()] == ["Standard test page", "A", "A"]  # the rotation's own next was A, unaffected
    assert rig.print_now(page="ghost").status_code == 404


def test_disabled_and_missing_pages_are_skipped_and_nothing_enabled_falls_back(rig):
    a = rig.upload(PNG, "A.png")["data"]["id"]; b = rig.upload(PNG2, "B.png")["data"]["id"]
    rotate_on(rig)
    rig.put(f"/api/maintenance/{PID}/rotation", {"items": [{"id": "standard", "enabled": False}, {"id": a, "enabled": False},
                                                          {"id": b, "enabled": True}]})
    rig.print_now(); rig.print_now()
    assert [n for _, n in rig.sent()] == ["B", "B"]  # only B is on
    rig.put(f"/api/maintenance/{PID}/rotation", {"items": [{"id": "standard", "enabled": False}, {"id": a, "enabled": False},
                                                          {"id": b, "enabled": False}]})
    rig.print_now()
    assert rig.sent()[-1][1] == "Standard test page"  # nothing on: the plain page
    # a file removed behind the app's back is skipped rather than failing the print
    rig.put(f"/api/maintenance/{PID}/rotation", {"items": [{"id": a, "enabled": True}, {"id": b, "enabled": True}]})
    import pathlib
    for f in (rig.ctx.settings.data_dir / "pages").glob(f"{a}*"):
        pathlib.Path(f).unlink()
    rig.print_now()
    assert rig.sent()[-1][1] == "B"


def test_every_nth_print_is_the_plain_page(rig):
    rig.upload(PNG, "A.png"); rig.upload(PNG2, "B.png")
    rotate_on(rig, plain_every=2)
    items = rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]["items"]
    rig.put(f"/api/maintenance/{PID}/rotation", {"items": [{"id": i["id"], "enabled": not i["standard"]} for i in items]})
    for _ in range(4):
        rig.print_now()
    assert [n for _, n in rig.sent()] == ["A", "Standard test page", "B", "Standard test page"]


def test_a_failed_print_is_recorded_with_its_page_and_does_not_move_the_rotation(make_app):
    app, printer = make_app(fail=("cups",), print_cooldown=0, print_max_per_hour=100)
    c = app.test_client(); h = csrf_headers(c)
    c.put(f"/api/maintenance/{PID}/automation", json={"rotate": True}, headers=h)
    assert c.post(f"/api/maintenance/{PID}/nozzle/print", json={}, headers=h).status_code == 502
    ctx = app.extensions["panda"]["ctx"]
    row = ctx.db.one("SELECT * FROM mt_checks WHERE printer_id=? AND state='failed'", (PID,))
    assert row["page_name"] == "Standard test page"
    assert ctx.db.one("SELECT value FROM kv WHERE key=?", (f"maintenance.{PID}.rotation_last",)) is None


def test_the_automatic_print_uses_the_rotation_too(rig):
    rig.upload(PNG, "A.png")
    rotate_on(rig)
    rig.put(f"/api/maintenance/{PID}/automation", {"enabled": True, "idle_days": 2, "run_at": "00:00"})
    svc = rig.runtime().service
    svc.tracker.poll()
    assert svc.run_schedule().action == "print"
    assert rig.sent() == [("application/postscript", "Standard test page")]
    assert rig.ctx.db.one("SELECT source FROM mt_checks WHERE printer_id=? AND page_name IS NOT NULL ORDER BY id DESC", (PID,))["source"] == "auto"


# ------------------------------------------------------------------ the counter and multi-page prints

def test_a_two_page_pdf_is_credited_in_full_not_called_someone_elses_print(rig):
    pdf = rig.upload(PDF2, "B.pdf")["data"]["id"]
    svc = rig.runtime().service
    svc.tracker.poll()
    before = rig.client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_external_print"]
    assert rig.print_now(page=pdf).status_code == 202
    rig.clock.advance(60)  # the fake printer finishes both sheets: the counter goes up by 2
    svc.tracker.poll()
    d = rig.client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["last_print"]["kind"] == "nozzle" and d["last_external_print"] == before
    last = rig.ctx.db.one("SELECT delta, kind FROM mt_activity WHERE printer_id=? ORDER BY id DESC", (PID,))
    assert (last["kind"], last["delta"]) == ("nozzle", 2)


# ------------------------------------------------------------------ thumbnails and downloads

THUMB = b"\x89PNG\r\n\x1a\n" + b"thumbnail" * 20


def upload_with_thumb(rig, data, name, thumb=THUMB):
    form = {"file": (io.BytesIO(data), name)}
    if thumb is not None:
        form["thumb"] = (io.BytesIO(thumb), "thumb.png")
    r = rig.client.post("/api/maintenance/pages", data=form, headers=rig.h, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]


def test_a_thumbnail_uploaded_with_the_page_is_served(rig):
    page = upload_with_thumb(rig, PNG, "Bunny.png")
    assert page["thumb"] is True
    t = rig.client.get(f"/api/maintenance/pages/{page['id']}/thumb")
    assert t.status_code == 200 and t.data == THUMB and t.headers["Content-Type"] == "image/png"
    assert rig.client.get("/api/maintenance/pages").get_json()["data"]["pages"][0]["thumb"] is True
    items = {i["id"]: i for i in rig.client.get(f"/api/maintenance/{PID}/rotation").get_json()["data"]["items"]}
    assert items[page["id"]]["thumb"] is True and items["standard"]["thumb"] is False  # the dialog lists these


def test_a_page_without_a_thumbnail_says_so(rig):
    page = rig.upload(PNG, "Plain.png")["data"]
    assert page["thumb"] is False
    assert rig.client.get(f"/api/maintenance/pages/{page['id']}/thumb").status_code == 404


def test_a_fake_thumbnail_is_ignored_not_an_error(rig):
    page = upload_with_thumb(rig, PNG, "A.png", thumb=b"<script>alert(1)</script>")
    assert page["thumb"] is False and rig.client.get(f"/api/maintenance/pages/{page['id']}/thumb").status_code == 404


def test_download_gives_the_file_under_a_clean_name_and_the_default_is_inline(rig):
    page = rig.upload(PNG, "Bunny colouring.png")["data"]
    inline = rig.client.get(f"/api/maintenance/pages/{page['id']}/file")
    assert "attachment" not in inline.headers.get("Content-Disposition", "")
    d = rig.client.get(f"/api/maintenance/pages/{page['id']}/file?download=1")
    assert d.status_code == 200 and d.data == PNG
    assert "attachment" in d.headers["Content-Disposition"] and "Bunny colouring.png" in d.headers["Content-Disposition"]
    assert d.headers["X-Content-Type-Options"] == "nosniff" and d.headers["Content-Security-Policy"] == "sandbox"
    pdf = rig.upload(PDF2, "Book.pdf")["data"]
    assert "Book.pdf" in rig.client.get(f"/api/maintenance/pages/{pdf['id']}/file?download=1").headers["Content-Disposition"]
