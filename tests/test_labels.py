"""Label Sheet Maker: the sheet geometry, the text setting, the spreadsheet reading, the PDF writer, and the API, against fakes."""
import json
import re

import pytest

from app.tools.labels import fonts, pdf as P, sheet as S
from conftest import PID, csrf_headers

A4 = "iso_a4_210x297mm"


def l7160(**kw):
    base = dict(paper_w=210, paper_h=297, cols=3, rows=7, width=63.5, height=38.1, left=7.2, top=15.1, gap_x=2.5, gap_y=0, radius=2)
    base.update(kw)
    return S.make_sheet(**base)


# ------------------------------------------------------------------ fonts

def test_text_is_measured_with_the_real_helvetica_widths():
    assert fonts.text_width("Hello World", 10) == pytest.approx(51.67)  # H e l l o space W o r l d, from the font's own table
    assert fonts.text_width("Hello World", 10, bold=True) > fonts.text_width("Hello World", 10)


def test_characters_the_font_lacks_are_replaced_and_reported():
    text, lost = fonts.encode("Zoë – 日本")
    assert text == "Zoë – ??" and lost == ["日", "本"]  # é and the dash are in code page 1252, the Japanese is not


def test_wrapping_breaks_at_spaces_and_splits_only_a_word_that_is_too_wide():
    assert fonts.wrap("aaa bbb ccc", 10, fonts.text_width("aaa bbb", 10)) == ["aaa bbb", "ccc"]
    pieces = fonts.wrap("w" * 30, 10, 50)
    assert len(pieces) > 1 and all(fonts.text_width(p, 10) <= 50 for p in pieces) and "".join(pieces) == "w" * 30
    assert fonts.wrap("", 10, 50) == [""]


def test_pdf_strings_escape_the_special_characters():
    assert fonts.pdf_escape("a(b)\\c") == b"a\\(b\\)\\\\c"
    assert fonts.pdf_escape("é") == b"\xe9"


# ------------------------------------------------------------------ the sheet

def test_a_sheet_lists_its_cells_row_by_row():
    sh = l7160()
    cells = sh.cells()
    assert sh.per_sheet == 21 and len(cells) == 21
    assert cells[0] == {"x": 7.2, "y": 15.1, "w": 63.5, "h": 38.1} and cells[1]["x"] == pytest.approx(73.2) and cells[3]["y"] == pytest.approx(53.2)


@pytest.mark.parametrize("kw,words", [
    ({"cols": 4}, "wide"), ({"rows": 9}, "tall"), ({"cols": 0}, "columns"), ({"cols": True}, "columns"), ({"width": 2}, "between 5 and 300"),
    ({"left": -1}, "negative"), ({"gap_x": 60}, "negative"), ({"paper_w": 10, "paper_h": 20}, "paper"), ({"top": "3"}, "top margin")])
def test_a_sheet_that_does_not_fit_or_makes_no_sense_is_refused_in_plain_words(kw, words):
    with pytest.raises(S.SheetError, match=words):
        l7160(**kw)


def test_every_shipped_template_fits_its_paper():
    from app.tools.labels import load_templates, HERE
    templates = load_templates(HERE / "templates.yml")
    assert len(templates) >= 15 and len({t["id"] for t in templates}) == len(templates)
    assert {t["paper"] for t in templates} == {"a4", "letter"}
    for t in templates:
        S.make_sheet(t["paper_width"], t["paper_height"], t["cols"], t["rows"], t["width"], t["height"], t["left"], t["top"], t["gap_x"], t["gap_y"], t["radius"])


# ------------------------------------------------------------------ what goes on the labels

def test_typed_text_is_blocks_or_lines():
    text = "Ada\n12 Way\n\n\nGrace  \n1 Court\n"
    assert S.labels_from_text(text, per_line=False) == [["Ada", "12 Way"], ["Grace", "1 Court"]]
    assert S.labels_from_text(text, per_line=True) == [["Ada"], ["12 Way"], ["Grace"], ["1 Court"]]
    assert S.labels_from_text("   \n\n", False) == []


def test_a_spreadsheet_becomes_one_label_per_row_and_skips_empty_lines():
    labels, cols = S.labels_from_csv("Name,Street,Line2,City\nAda,1 X,Flat 2,London\nBob,2 Y,,Leeds\n", "{Name}\n{Street}\n{Line2}\n{City}")
    assert cols == ["Name", "Street", "Line2", "City"]
    assert labels == [["Ada", "1 X", "Flat 2", "London"], ["Bob", "2 Y", "Leeds"]]  # no gap where Bob has no second line


def test_a_spreadsheet_template_mixes_columns_and_matches_names_loosely():
    labels, _ = S.labels_from_csv("First,Last,Town\nAda,Lovelace,London\n", "{first} {LAST}\n{Town}")
    assert labels == [["Ada Lovelace", "London"]]


def test_a_spreadsheet_with_no_template_prints_every_column():
    labels, _ = S.labels_from_csv("A,B\n1,2\n3,\n", "")
    assert labels == [["1", "2"], ["3"]]


@pytest.mark.parametrize("text", ["A;B\n1;2\n", "A\tB\n1\t2\n", "﻿A,B\r\n1,2\r\n"])
def test_semicolon_tab_and_excel_style_files_are_read(text):
    assert S.labels_from_csv(text, "{A}-{B}")[0] == [["1-2"]]


@pytest.mark.parametrize("text,words", [("", "empty"), ("A,B\n", "at least one row"), ("A,A\n1,2\n", "different"), ("A\n" + "1\n" * 2100, "more than")])
def test_bad_spreadsheets_are_explained(text, words):
    with pytest.raises(S.SheetError, match=words):
        S.labels_from_csv(text, "")


def test_a_template_naming_a_missing_column_lists_the_real_ones():
    with pytest.raises(S.SheetError, match=r"\{Zip\}.*Name, City"):
        S.labels_from_csv("Name,City\nA,B\n", "{Name}\n{Zip}")


# ------------------------------------------------------------------ setting the text

def lines(result, page=0, cell=0):
    return [ln["t"] for ln in result.pages[page][cell]["lines"]]


def test_text_lands_inside_its_label():
    sh = l7160()
    r = S.compute(sh, [["Ada Lovelace", "12 Analytical Way", "London"]], S.Style(size=11, pad=3))
    cell = r.pages[0][0]
    assert lines(r) == ["Ada Lovelace", "12 Analytical Way", "London"]
    for ln in cell["lines"]:
        assert cell["x"] + 3 - 0.01 <= ln["x"] and cell["y"] < ln["y"] < cell["y"] + cell["h"]
    assert all(not c["lines"] for c in r.pages[0][1:])  # the other places stay empty


def test_alignment_moves_the_text_across_the_label():
    sh = l7160()
    x = lambda a: S.compute(sh, [["Hi"]], S.Style(size=12, align=a, pad=3)).pages[0][0]["lines"][0]["x"]
    assert x("left") < x("center") < x("right")
    c = sh.cells()[0]
    assert x("left") == pytest.approx(c["x"] + 3, abs=0.01)
    width = fonts.text_width("Hi", 12) * S.MM_PER_PT
    assert x("right") == pytest.approx(c["x"] + c["w"] - 3 - width, abs=0.01)


def test_top_and_middle_vertical_alignment():
    sh = l7160()
    y = lambda v: S.compute(sh, [["Hi"]], S.Style(size=10, valign=v, pad=2)).pages[0][0]["lines"][0]["y"]
    assert y("top") < y("middle")


def test_long_lines_wrap_inside_the_label():
    r = S.compute(l7160(), [["A very long name that cannot possibly fit on one line of this label"]], S.Style(size=12, pad=3))
    assert len(lines(r)) > 1 and r.warnings == []
    inner = (63.5 - 6) / S.MM_PER_PT
    assert all(fonts.text_width(t, 12) <= inner + 0.01 for t in lines(r))


def test_automatic_size_is_the_largest_that_fits_every_label_and_stays_small_enough_for_the_longest():
    sh = l7160()
    short = S.compute(sh, [["Hi"]], S.Style(size=None)).size
    mixed = S.compute(sh, [["Hi"], ["Line one", "Line two", "Line three", "Line four", "Line five", "Line six"]], S.Style(size=None)).size
    assert short > mixed >= S.MIN_SIZE  # one long label makes all of them smaller, so the sheet looks even


def test_text_that_cannot_fit_is_reported_with_how_many_labels_are_affected():
    r = S.compute(l7160(), [["x"] * 40, ["ok"]], S.Style(size=12))
    assert any("1 label has more text than fits at 12 pt" in w for w in r.warnings)


def test_characters_that_cannot_be_printed_are_warned_about():
    r = S.compute(l7160(), [["日本"]], S.Style(size=12))
    assert any("日" in w and "?" in w for w in r.warnings) and lines(r) == ["??"]


def test_first_line_bold_and_all_bold():
    r = S.compute(l7160(), [["Name", "Street"]], S.Style(size=11, first_bold=True))
    assert [ln["bold"] for ln in r.pages[0][0]["lines"]] == [True, False]
    r = S.compute(l7160(), [["Name", "Street"]], S.Style(size=11, bold=True))
    assert all(ln["bold"] for ln in r.pages[0][0]["lines"])


# ------------------------------------------------------------------ how many, and where they start

def test_copies_repeat_each_label_one_after_another():
    r = S.compute(l7160(), [["A"], ["B"]], S.Style(size=11), copies=3)
    assert [lines(r, cell=i) for i in range(7)] == [["A"], ["A"], ["A"], ["B"], ["B"], ["B"], []] and r.labels == 6


def test_a_part_used_sheet_starts_at_the_chosen_place():
    r = S.compute(l7160(), [["A"], ["B"]], S.Style(size=11), start=5)
    assert [bool(c["lines"]) for c in r.pages[0][:7]] == [False, False, False, False, True, True, False]
    with pytest.raises(S.SheetError, match="1 to 21"):
        S.compute(l7160(), [["A"]], S.Style(size=11), start=22)


def test_more_labels_than_fit_run_on_to_more_sheets_and_the_first_sheet_honours_start():
    labels = [[f"L{i}"] for i in range(25)]
    r = S.compute(l7160(), labels, S.Style(size=11), start=3)
    assert len(r.pages) == 2 and lines(r, 0, 2) == ["L0"] and lines(r, 0, 20) == ["L18"] and lines(r, 1, 0) == ["L19"] and lines(r, 1, 5) == ["L24"]
    assert not r.pages[1][6]["lines"]


def test_fill_repeats_the_list_until_the_last_sheet_is_full():
    r = S.compute(l7160(), [["A"], ["B"]], S.Style(size=11), fill=True)
    assert r.labels == 21 and all(c["lines"] for c in r.pages[0]) and lines(r, cell=20) == ["A"]
    r = S.compute(l7160(), [["A"]], S.Style(size=11), fill=True, start=20)
    assert r.labels == 2  # only places 20 and 21 are free


def test_limits_on_copies_sheets_and_labels():
    with pytest.raises(S.SheetError, match="Copies"):
        S.compute(l7160(), [["A"]], S.Style(size=11), copies=0)
    with pytest.raises(S.SheetError, match="sheets"):
        S.compute(l7160(), [["A"]] * 2200, S.Style(size=11), copies=1)
    with pytest.raises(S.SheetError, match="the most is"):
        S.compute(l7160(), [["A"]] * 60, S.Style(size=11), copies=100)


def test_style_values_are_checked():
    for bad in (S.Style(size=2), S.Style(size=11, align="justify"), S.Style(size=11, pad=40), S.Style(size=11, valign="bottom")):
        with pytest.raises(S.SheetError):
            S.compute(l7160(), [["A"]], bad)


# ------------------------------------------------------------------ the PDF

def mediabox(pdf_bytes):
    m = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf_bytes)
    return float(m[1]), float(m[2])


def test_the_pdf_is_the_paper_size_with_one_page_per_sheet_and_the_text_in_it():
    r = S.compute(l7160(), [[f"Name {i}", "Street"] for i in range(25)], S.Style(size=11, first_bold=True))
    body = P.build(r)
    assert body.startswith(b"%PDF") and body.rstrip().endswith(b"%%EOF")
    assert abs(mediabox(body)[0] - 210 * 72 / 25.4) < 0.01 and abs(mediabox(body)[1] - 297 * 72 / 25.4) < 0.01  # exactly A4, nothing scaled
    assert b"/Count 2" in body and body.count(b"/Type /Page ") == 2
    assert b"(Name 0) Tj" in body and b"(Name 24) Tj" in body and b"/F2 11 Tf" in body and b"/F1 11 Tf" in body
    assert b"/BaseFont /Helvetica " in body and b"/BaseFont /Helvetica-Bold " in body and b"WinAnsiEncoding" in body


def test_the_pdf_places_text_where_the_layout_says():
    r = S.compute(l7160(), [["Hi"]], S.Style(size=12, pad=3, valign="top"))
    ln = r.pages[0][0]["lines"][0]
    body = P.build(r)
    pt = 72 / 25.4
    expect = f"{ln['x'] * pt:.3f}".rstrip("0").rstrip(".") + " " + f"{(297 - ln['y']) * pt:.3f}".rstrip("0").rstrip(".") + " Td (Hi) Tj"
    assert expect.encode() in body  # the PDF's y runs upwards from the bottom of the page


def test_special_characters_cannot_break_the_pdf_text():
    r = S.compute(l7160(), [["a (b) \\ c", "café"]], S.Style(size=11))
    body = P.build(r)
    assert b"(a \\(b\\) \\\\ c) Tj" in body and b"(caf\xe9) Tj" in body


def test_borders_are_drawn_only_when_asked_and_with_rounded_corners_when_the_sheet_has_them():
    r = S.compute(l7160(), [["A"]], S.Style(size=11))
    assert b" S\n" not in P.build(r)
    with_border = P.build(r, outline=True)
    assert with_border.count(b" S\n") == 21 and b" c h S" in with_border
    square = S.compute(l7160(radius=0), [["A"]], S.Style(size=11))
    assert b" re S" in P.build(square, outline=True)


def test_an_empty_sheet_still_makes_a_valid_pdf():
    body = P.build(S.compute(l7160(), [], S.Style(size=11)), outline=True)
    assert body.startswith(b"%PDF") and b"/Count 1" in body


# ------------------------------------------------------------------ the API

@pytest.fixture
def rig(make_app, clock):
    app, backend = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    client = app.test_client()
    h = csrf_headers(client)

    def body(**kw):
        b = {"sheet": {"paper": {"width": 210, "height": 297}, "cols": 3, "rows": 7, "width": 63.5, "height": 38.1, "left": 7.2, "top": 15.1,
                       "gap_x": 2.5, "gap_y": 0, "radius": 2},
             "content": {"mode": "text", "text": "Ada Lovelace\n12 Analytical Way\n\nGrace Hopper\n1 Court"},
             "style": {"size": 11, "align": "left", "valign": "middle", "pad": 3}, "copies": 1, "start": 1}
        b.update(kw)
        return b

    def post(path, data=None, **kw):
        return client.post(f"/api/labels/{path}", json=data if data is not None else body(), headers=h, **kw)

    return type("Rig", (), dict(app=app, client=client, h=h, backend=backend, body=staticmethod(body), post=staticmethod(post)))


GOOD = {"media": A4, "media_type": "stationery", "quality": 4, "copies": 1}


def test_config_lists_the_sheets_and_the_printers_choices(rig):
    c = rig.client.get(f"/api/labels/config?printer={PID}").get_json()["data"]
    assert len(c["templates"]) >= 15 and c["templates"][0]["id"] == "l7159" and c["printer"]["id"] == PID and c["printer"]["media"]
    assert rig.client.get("/api/labels/config").get_json()["data"]["printer"] is None
    assert rig.client.get("/api/labels/config?printer=ghost").status_code == 404


def test_layout_returns_the_placed_text_for_the_preview(rig):
    d = rig.post("layout").get_json()["data"]
    assert (d["sheets"], d["labels"], d["per_sheet"], d["page"]) == (1, 2, 21, 1) and d["size"] == 11
    assert [ln["t"] for ln in d["cells"][0]["lines"]] == ["Ada Lovelace", "12 Analytical Way"] and d["cells"][2]["lines"] == []


def test_layout_pages_through_a_long_list_and_reports_the_spreadsheet_columns(rig):
    csv_text = "Name,City\n" + "\n".join(f"P{i},C{i}" for i in range(30))
    d = rig.post("layout", rig.body(content={"mode": "csv", "csv": csv_text, "template": "{Name}, {City}"}, page=2)).get_json()["data"]
    assert d["sheets"] == 2 and d["page"] == 2 and d["columns"] == ["Name", "City"] and d["cells"][0]["lines"][0]["t"] == "P21, C21"
    assert rig.post("layout", rig.body(content={"mode": "csv", "csv": csv_text}, page=99)).get_json()["data"]["page"] == 2  # past the end shows the last


@pytest.mark.parametrize("change,words", [
    ({"copies": 0}, "Copies"), ({"copies": "2"}, "whole number"), ({"start": 99}, "1 to 21"), ({"content": {"mode": "pdf"}}, "typed text or a spreadsheet"),
    ({"content": {"mode": "text", "text": 5}}, "text wasn"), ({"content": {"mode": "csv", "csv": "A,B\n1,2\n", "template": "{Z}"}}, "no column"),
    ({"style": {"size": "big"}}, "text size"), ({"sheet": {"paper": {"width": 210, "height": 297}, "cols": 4, "rows": 7, "width": 63.5, "height": 38.1,
                                                          "left": 7.2, "top": 15.1}}, "wide"),
    ({"sheet": {"paper": {"width": "a", "height": 297}}}, "paper width")])
def test_layout_refuses_bad_requests_with_a_reason(rig, change, words):
    r = rig.post("layout", rig.body(**change))
    assert r.status_code == 400 and words in r.get_json()["error"]["message"]


def test_layout_without_a_body_is_a_400_not_a_crash(rig):
    assert rig.client.post("/api/labels/layout", data="nope", headers=rig.h, content_type="application/json").status_code == 400
    assert rig.client.post("/api/labels/layout", json=[1], headers=rig.h).status_code == 400


def test_text_closer_to_the_edge_than_the_printer_reaches_is_warned_about_unless_borderless(rig):
    tight = rig.body(sheet={**rig.body()["sheet"], "left": 0, "cols": 3}, style={"size": 11, "pad": 0.5, "align": "left", "valign": "top"}, min_margin=3)
    assert any("edge" in w for w in rig.post("layout", tight).get_json()["data"]["warnings"])
    assert not any("edge" in w for w in rig.post("layout", {**tight, "borderless": True}).get_json()["data"]["warnings"])
    assert not any("edge" in w for w in rig.post("layout", rig.body(min_margin=3)).get_json()["data"]["warnings"])


def test_the_pdf_is_served_inline_or_as_a_download(rig):
    r = rig.post("pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and "inline" in r.headers["Content-Disposition"] and r.data.startswith(b"%PDF")
    assert r.headers["Content-Security-Policy"] == "sandbox" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in rig.client.post("/api/labels/pdf?download=1", json=rig.body(), headers=rig.h).headers["Content-Disposition"]
    assert rig.post("pdf", rig.body(copies=0)).status_code == 400


def test_post_requires_the_csrf_token(rig):
    assert rig.client.post("/api/labels/layout", json=rig.body()).status_code == 403


def test_print_sends_the_pdf_with_the_chosen_paper_at_actual_size(rig):
    r = rig.post("print", {**rig.body(), "printer": PID, "options": GOOD})
    d = r.get_json()
    assert r.status_code == 202 and d["data"]["labels"] == 2 and d["data"]["sheets"] == 1
    job = rig.backend.submitted[-1]
    assert job["mime"] == "application/pdf" and job["name"] == "panda-print labels"
    assert job["options"]["media"] == A4 and job["options"]["print-scaling"] == "none" and job["options"]["media-type"] == "stationery"
    assert job["document"].startswith(b"%PDF") and abs(mediabox(job["document"])[0] - 210 * 72 / 25.4) < 0.1


def test_print_refuses_paper_that_is_not_the_sheets_paper(rig):
    letter = next(m for m in rig.client.get(f"/api/labels/config?printer={PID}").get_json()["data"]["printer"]["media"] if m["keyword"].startswith("na_letter"))
    r = rig.post("print", {**rig.body(), "printer": PID, "options": {**GOOD, "media": letter["keyword"]}})
    assert r.status_code == 400 and "laid out for 210 × 297" in r.get_json()["error"]["message"]


@pytest.mark.parametrize("change,words", [({"media": "iso_a9_37x52mm"}, "paper size"), ({"media_type": "nope"}, "paper types"),
                                          ({"quality": 9}, "qualities"), ({"copies": 99}, "Copies")])
def test_print_options_the_queue_does_not_offer_are_refused(rig, change, words):
    r = rig.post("print", {**rig.body(), "printer": PID, "options": {**GOOD, **change}})
    assert r.status_code == 400 and words in r.get_json()["error"]["message"]


def test_print_for_a_ghost_or_a_printer_without_cups(rig):
    assert rig.post("print", {**rig.body(), "printer": "ghost", "options": GOOD}).status_code == 404
    rig.client.put(f"/api/printers/{PID}", json={"cups_queue": ""}, headers=rig.h)
    r = rig.post("print", {**rig.body(), "printer": PID, "options": GOOD})
    assert r.status_code == 400 and "no CUPS print queue" in r.get_json()["error"]["message"]
    assert rig.client.get(f"/api/labels/config?printer={PID}").get_json()["data"]["printer"]["configured"] is False


def test_print_while_cups_is_down_is_a_502(make_app):
    app, _ = make_app(fail=("cups",), timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    c = app.test_client()
    r = c.post("/api/labels/print", json={"sheet": {"paper": {"width": 210, "height": 297}, "cols": 3, "rows": 7, "width": 63.5, "height": 38.1},
                                          "content": {"mode": "text", "text": "A"}, "style": {"size": 11}, "printer": PID, "options": GOOD}, headers=csrf_headers(c))
    assert r.status_code == 502


def test_printing_is_rate_limited_per_printer_and_a_removed_printers_log_goes_with_it(make_app):
    app, _ = make_app(print_cooldown=45, print_max_per_hour=100)
    c = app.test_client()
    h = csrf_headers(c)
    other = c.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h).get_json()["data"]["id"]
    sheet = {"paper": {"width": 210, "height": 297}, "cols": 3, "rows": 7, "width": 63.5, "height": 38.1, "left": 7.2, "top": 15.1}

    def go(pid):
        return c.post("/api/labels/print", json={"sheet": sheet, "content": {"mode": "text", "text": "A"}, "style": {"size": 11}, "printer": pid, "options": {"media": A4}}, headers=h)
    assert go(PID).status_code == 202
    again = go(PID)
    assert again.status_code == 429 and again.headers["Retry-After"] and again.get_json()["error"]["source"] == "rate-limit"
    assert go(other).status_code == 202  # the other printer is not held up
    ctx = app.extensions["panda"]["ctx"]
    assert ctx.db.one("SELECT COUNT(*) AS n FROM lb_prints WHERE printer_id=?", (PID,))["n"] == 1
    c.delete(f"/api/printers/{PID}", headers=h)
    assert ctx.db.one("SELECT COUNT(*) AS n FROM lb_prints WHERE printer_id=?", (PID,))["n"] == 0


def test_a_sheet_goes_to_the_chosen_printer_only(make_app):
    app, first = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    c = app.test_client()
    h = csrf_headers(c)
    ctx = app.extensions["panda"]["ctx"]
    other = c.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h).get_json()["data"]["id"]
    second = ctx.backend_for(ctx.printers.get(other))
    sheet = {"paper": {"width": 210, "height": 297}, "cols": 3, "rows": 7, "width": 63.5, "height": 38.1, "left": 7.2, "top": 15.1}
    c.post("/api/labels/print", json={"sheet": sheet, "content": {"mode": "text", "text": "A"}, "style": {"size": 11}, "printer": other, "options": {"media": A4}}, headers=h)
    assert len(second.submitted) == 1 and len(first.submitted) == 0
    assert c.get(f"/api/labels/config?printer={other}").get_json()["data"]["printer"]["name"] == "Hall"
