"""Label Sheet Maker: the sheet geometry (packs and plain sheets), the text setting and styling, the spreadsheet reading,
the PDF writer, the presets, and the API, against fakes."""
import json
import re

import pytest

from app.tools.labels import fonts, pdf as P, presets as pre, sheet as S
from conftest import PID, csrf_headers
from test_photosheet import make_jpeg
from app.tools.photosheet import pdfsheet

A4 = "iso_a4_210x297mm"
PT = 72 / 25.4


def l7160(**kw):
    base = dict(paper_w=210, paper_h=297, cols=3, rows=7, width=63.5, height=38.1, left=7.2, top=15.1, gap_x=2.5, gap_y=0)
    base.update(kw)
    return S.pack_sheet(**base)


def plain(**kw):
    base = dict(paper_w=210, paper_h=297, width=40, height=40, margin=8, gap=3)
    base.update(kw)
    return S.size_sheet(**base)


def style(**kw):
    base = dict(size=11, pad=3)
    base.update(kw)
    return S.Style(**base)


def lines(result, page=0, cell=0):
    return [ln["t"] for ln in result.pages[page][cell]["lines"]]


# ------------------------------------------------------------------ fonts

def test_text_is_measured_with_the_real_font_widths():
    assert fonts.text_width("Hello World", 10) == pytest.approx(51.67)  # H e l l o space W o r l d, from Helvetica's own table
    assert fonts.text_width("Hello World", 10, "Helvetica-Bold") > fonts.text_width("Hello World", 10)
    assert fonts.text_width("Hello", 10, "Courier") == 30  # every typewriter character is 0.6 wide
    assert fonts.text_width("Hello World", 10, "Times-Roman") == pytest.approx(50.27)
    assert fonts.text_width("Hello", 10, "Helvetica-Oblique") == fonts.text_width("Hello", 10)  # slanted, same widths


def test_every_family_has_all_four_faces():
    names = {fonts.font_name(f, b, i) for f in fonts.FAMILIES for b in (False, True) for i in (False, True)}
    assert len(names) == 12 and fonts.font_name("serif", True, True) == "Times-BoldItalic" and fonts.font_name() == "Helvetica"
    for n in names:
        assert fonts.text_width("Ag", 10, n) > 0


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


# ------------------------------------------------------------------ a label pack

def test_a_pack_lists_its_cells_row_by_row():
    sh = l7160()
    assert sh.per_sheet == 21 and (sh.cols, sh.rows) == (3, 7)
    assert sh.cells[0] == {"x": 7.2, "y": 15.1, "w": 63.5, "h": 38.1} and sh.cells[1]["x"] == pytest.approx(73.2) and sh.cells[3]["y"] == pytest.approx(53.2)


@pytest.mark.parametrize("kw,words", [
    ({"cols": 4}, "wide"), ({"rows": 9}, "tall"), ({"cols": 0}, "columns"), ({"cols": True}, "columns"), ({"width": 2}, "between 5 and 300"),
    ({"left": -1}, "negative"), ({"gap_x": 60}, "negative"), ({"paper_w": 10, "paper_h": 20}, "paper"), ({"top": "3"}, "top margin")])
def test_a_pack_that_does_not_fit_or_makes_no_sense_is_refused_in_plain_words(kw, words):
    with pytest.raises(S.SheetError, match=words):
        l7160(**kw)


def test_every_shipped_pack_fits_its_paper():
    from app.tools.labels import load_templates, HERE
    templates = load_templates(HERE / "templates.yml")
    assert len(templates) >= 15 and len({t["id"] for t in templates}) == len(templates)
    assert {t["paper"] for t in templates} == {"a4", "letter"}
    for t in templates:
        S.pack_sheet(t["paper_width"], t["paper_height"], t["cols"], t["rows"], t["width"], t["height"], t["left"], t["top"], t["gap_x"], t["gap_y"])


def test_a_pack_can_use_only_its_top_or_bottom_half_to_save_the_rest_for_later():
    top, bottom = l7160(area="top"), l7160(area="bottom")
    assert top.per_sheet == 12 and bottom.per_sheet == 9 and top.per_sheet + bottom.per_sheet == 21  # together, the whole sheet
    assert all(c["y"] + c["h"] / 2 < 148.5 for c in top.cells) and all(c["y"] + c["h"] / 2 > 148.5 for c in bottom.cells)
    assert top.area == "top" and top.region["h"] == pytest.approx(148.5) and bottom.region["y"] == pytest.approx(148.5)
    assert any("12 labels in the top half" in w for w in top.warnings) and not l7160().warnings
    with pytest.raises(S.SheetError, match="half"):
        l7160(area="left")
    with pytest.raises(S.SheetError, match="No label"):
        S.pack_sheet(210, 297, 1, 1, 60, 200, 5, 90, area="top")  # the only label starts in the lower half


def test_labels_flow_into_the_chosen_half_only():
    r = S.compute(l7160(area="bottom"), [[f"L{i}"] for i in range(12)], style())
    assert len(r.pages) == 2 and r.pages[0][0]["y"] > 148.5 and sum(1 for c in r.pages[0] if c["used"]) == 9 and r.pages[0][0]["lines"][0]["t"] == "L0"


def test_blank_labels_print_their_background_with_no_text():
    r = S.compute(plain(), [], style(fill="#ffeecc", shape="ellipse", border="#aa7700"), blanks_fill=True)
    assert r.labels == 24 and r.blanks == 24 and all(c["used"] and c["lines"] == [] for c in r.pages[0])
    body = P.build(r)
    assert b"1 0.933 0.8 rg" in body and b" Tj" not in body and b"BT" not in body  # colour and border, but no text objects
    assert body.count(b" f Q") == 24
    few = S.compute(plain(), [], style(), blanks=5)
    assert few.labels == 5 and sum(1 for c in few.pages[0] if c["used"]) == 5


def test_blank_labels_share_the_sheet_with_the_printed_ones():
    r = S.compute(l7160(), [["Ada"], ["Grace"]], style(fill="#ffeecc"), blanks_fill=True)
    assert r.labels == 21 and r.blanks == 19 and lines(r, cell=0) == ["Ada"] and lines(r, cell=1) == ["Grace"] and lines(r, cell=2) == []
    assert all(c["used"] for c in r.pages[0])  # printed and blank labels alike are drawn
    some = S.compute(l7160(), [["Ada"], ["Grace"]], style(), blanks=3)
    assert some.labels == 5 and some.blanks == 3 and [c["used"] for c in some.pages[0][:6]] == [True] * 5 + [False]
    assert S.compute(l7160(), [["Ada"]], style()).blanks == 0


def test_blanks_follow_copies_start_and_runs_onto_more_sheets():
    r = S.compute(l7160(), [["A"]], style(), copies=3, start=5, blanks_fill=True)
    assert r.labels == 17 and r.blanks == 14 and [bool(c["lines"]) for c in r.pages[0][:9]] == [False] * 4 + [True] * 3 + [False] * 2  # 4 skipped, 3 copies, then blanks
    more = S.compute(l7160(), [[f"L{i}"] for i in range(25)], style(), blanks_fill=True)
    assert len(more.pages) == 2 and more.labels == 42 and more.blanks == 17 and all(c["used"] for c in more.pages[1])
    assert S.compute(l7160(), [[f"L{i}"] for i in range(21)], style(), blanks_fill=True).blanks == 0  # nothing to fill
    with pytest.raises(S.SheetError, match="blank labels"):
        S.compute(l7160(), [["A"]], style(), blanks=-1)
    with pytest.raises(S.SheetError, match="the most is"):
        S.compute(l7160(), [["A"]], style(), blanks=S.MAX_LABELS)


# ------------------------------------------------------------------ a plain sheet laid out by label size

def test_a_plain_sheet_is_filled_by_label_size_like_the_passport_tool():
    sh = plain()
    assert (sh.cols, sh.rows, sh.per_sheet) == (4, 6, 24)  # 40 mm labels, 8 mm margins and 3 mm gaps on A4
    assert sh.cells[0] == {"x": pytest.approx(sh.cells[0]["x"]), "y": pytest.approx(sh.cells[0]["y"]), "w": 40, "h": 40}
    assert plain(mode="grid", cols=2, rows=3).per_sheet == 6 and plain(mode="count", count=5).per_sheet == 5


def test_a_plain_sheet_can_be_turned_or_used_by_half_and_reports_it():
    land = plain(orientation="landscape")
    assert land.orientation == "landscape" and land.paper_w > land.paper_h
    top = plain(area="top")
    assert top.area == "top" and all(c["y"] + c["h"] <= 297 / 2 + 0.01 for c in top.cells) and top.region["h"] == pytest.approx(148.5)
    assert plain(area="bottom").cells[0]["y"] > 148.5
    with pytest.raises(S.SheetError, match="half page"):
        plain(area="top", orientation="landscape")


def test_a_plain_sheet_speaks_of_labels_not_photos():
    with pytest.raises(S.SheetError) as e:
        plain(width=290, height=290)
    assert "label" in str(e.value).lower() and "photo" not in str(e.value).lower()
    sh = plain(mode="grid", cols=9, rows=9)
    assert sh.warnings and all("photo" not in w.lower() for w in sh.warnings) and any("labels fit" in w for w in sh.warnings)
    close = plain(margin=1, min_margin=3)
    assert any("margin" in w for w in close.warnings)
    assert not plain(margin=1, min_margin=0).warnings


def test_the_common_sizes_all_fit_on_a4():
    for name, w, h, shape in S.SIZES:
        assert shape in S.SHAPES
        assert S.size_sheet(210, 297, w, h, margin=5, gap=2).per_sheet >= 1, name


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
    assert S.labels_from_csv("First,Last,Town\nAda,Lovelace,London\n", "{first} {LAST}\n{Town}")[0] == [["Ada Lovelace", "London"]]


def test_a_spreadsheet_with_no_template_prints_every_column():
    assert S.labels_from_csv("A,B\n1,2\n3,\n", "")[0] == [["1", "2"], ["3"]]


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

def test_text_lands_inside_its_label():
    r = S.compute(l7160(), [["Ada Lovelace", "12 Analytical Way", "London"]], style())
    cell = r.pages[0][0]
    assert lines(r) == ["Ada Lovelace", "12 Analytical Way", "London"]
    for ln in cell["lines"]:
        assert cell["x"] + 3 - 0.01 <= ln["x"] and cell["y"] < ln["y"] < cell["y"] + cell["h"]
    assert all(not c["lines"] for c in r.pages[0][1:])  # the other places stay empty


def test_alignment_moves_the_text_across_the_label():
    sh = l7160()
    x = lambda a: S.compute(sh, [["Hi"]], style(size=12, align=a)).pages[0][0]["lines"][0]["x"]
    assert x("left") < x("center") < x("right")
    c = sh.cells[0]
    assert x("left") == pytest.approx(c["x"] + 3, abs=0.01)
    assert x("right") == pytest.approx(c["x"] + c["w"] - 3 - fonts.text_width("Hi", 12) * S.MM_PER_PT, abs=0.01)


def test_top_and_middle_vertical_alignment():
    y = lambda v: S.compute(l7160(), [["Hi"]], style(size=10, valign=v, pad=2)).pages[0][0]["lines"][0]["y"]
    assert y("top") < y("middle")


def test_long_lines_wrap_inside_the_label():
    r = S.compute(l7160(), [["A very long name that cannot possibly fit on one line of this label"]], style(size=12))
    assert len(lines(r)) > 1 and r.warnings == []
    assert all(fonts.text_width(t, 12) <= (63.5 - 6) / S.MM_PER_PT + 0.01 for t in lines(r))


def test_automatic_size_is_the_largest_that_fits_every_label():
    sh = l7160()
    short = S.compute(sh, [["Hi"]], style(size=None)).size
    mixed = S.compute(sh, [["Hi"], ["Line one", "Line two", "Line three", "Line four", "Line five", "Line six"]], style(size=None)).size
    assert short > mixed >= S.MIN_SIZE  # one long label makes all of them smaller, so the sheet looks even


def test_text_that_cannot_fit_is_reported_with_how_many_labels_are_affected():
    r = S.compute(l7160(), [["x"] * 40, ["ok"]], style(size=12))
    assert any("1 label has more text than fits at 12 pt" in w for w in r.warnings)


def test_characters_that_cannot_be_printed_are_warned_about():
    r = S.compute(l7160(), [["日本"]], style(size=12))
    assert any("日" in w and "?" in w for w in r.warnings) and lines(r) == ["??"]


def test_bold_italic_and_first_line_choices_reach_each_line():
    r = S.compute(l7160(), [["Name", "Street"]], style(first_bold=True))
    assert [(ln["bold"], ln["font"]) for ln in r.pages[0][0]["lines"]] == [(True, "Helvetica-Bold"), (False, "Helvetica")]
    r = S.compute(l7160(), [["Name", "Street"]], style(bold=True, italic=True, family="serif"))
    assert [ln["font"] for ln in r.pages[0][0]["lines"]] == ["Times-BoldItalic"] * 2


def test_a_larger_first_line_and_its_own_colour():
    r = S.compute(l7160(), [["Name", "Street"]], style(size=10, first_scale=1.5, first_color="#cc0000", color="#222222"))
    first, second = r.pages[0][0]["lines"]
    assert first["size"] == 15 and second["size"] == 10 and first["color"] == "#cc0000" and second["color"] == "#222222"
    assert second["y"] - first["y"] > 10 * S.MM_PER_PT * 1.2  # the gap grows with the larger line above it


def test_line_spacing_spreads_the_lines():
    gap = lambda ls: (lambda ls_: ls_[1]["y"] - ls_[0]["y"])(S.compute(l7160(), [["A", "B"]], style(size=10, line_spacing=ls)).pages[0][0]["lines"])
    assert gap(1.0) < gap(1.2) < gap(2.0) and gap(1.5) == pytest.approx(10 * 1.5 * S.MM_PER_PT, abs=0.01)


# ------------------------------------------------------------------ the look: shapes, colours, picture

def test_only_places_that_hold_a_label_are_marked_used():
    r = S.compute(l7160(), [["A"], ["B"]], style(), start=2)
    assert [c["used"] for c in r.pages[0][:4]] == [False, True, True, False]


def test_text_stays_inside_an_oval_and_a_rounded_label():
    box = lambda **kw: S.text_box(l7160().cells[0], style(**kw))[0]
    rect, rounded, oval = box(shape="rect"), box(shape="rounded", radius=10), box(shape="ellipse")
    assert rect["w"] > rounded["w"] > oval["w"] and rect["h"] > rounded["h"] > oval["h"]
    assert oval["w"] == pytest.approx(63.5 * S.INSET_ELLIPSE - 6, abs=0.01)  # the rectangle that fits inside an ellipse, less the padding
    r = S.compute(l7160(), [["x"]], style(shape="ellipse", fill="#ffeecc"))
    d = r.to_dict()["look"]
    assert d["shape"] == "ellipse" and d["fill"] == "#ffeecc"


def test_a_picture_takes_space_from_the_text():
    plain_box = S.text_box(l7160().cells[0], style())[0]
    logo = S.Logo(200, 100, "top", 30)
    top_box, top_logo = S.text_box(l7160().cells[0], style(logo=logo))
    assert top_box["h"] < plain_box["h"] and top_box["w"] == plain_box["w"] and top_logo["y"] < top_box["y"]
    left_box, left_logo = S.text_box(l7160().cells[0], style(logo=S.Logo(200, 100, "left", 30)))
    assert left_box["w"] < plain_box["w"] and left_logo["x"] < left_box["x"]
    back_box, back_logo = S.text_box(l7160().cells[0], style(logo=S.Logo(200, 100, "background", 30)))
    assert back_box == plain_box and back_logo["cover"] is True


def test_a_picture_keeps_its_shape_and_a_background_covers_the_label():
    contain = S.fit_logo({"x": 0, "y": 0, "w": 40, "h": 40}, S.Logo(200, 100))
    assert contain["w"] == 40 and contain["h"] == 20 and contain["y"] == 10  # all of it, centred
    cover = S.fit_logo({"x": 0, "y": 0, "w": 40, "h": 40, "cover": True}, S.Logo(200, 100))
    assert cover["h"] == 40 and cover["w"] == 80 and cover["x"] == -20  # crops the sides


def test_only_used_places_get_a_picture():
    r = S.compute(l7160(), [["A"]], style(logo=S.Logo(100, 100)), start=2)
    assert "logo" not in r.pages[0][0] and "logo" in r.pages[0][1]


@pytest.mark.parametrize("bad", [dict(size=2), dict(align="justify"), dict(pad=40), dict(valign="bottom"), dict(family="comic"), dict(first_scale=5),
                                 dict(line_spacing=0.5), dict(shape="star"), dict(radius=-1), dict(border_width=9),
                                 dict(logo=S.Logo(10, 10, "middle", 30)), dict(logo=S.Logo(10, 10, "top", 90))])
def test_style_values_are_checked(bad):
    with pytest.raises(S.SheetError):
        S.compute(l7160(), [["A"]], style(**bad))


@pytest.mark.parametrize("value,ok", [("#a1B2c3", True), (None, True), ("", True), ("red", False), ("#123", False), (5, False)])
def test_colours_must_be_hex(value, ok):
    if ok:
        S.clean_color(value, "c")
    else:
        with pytest.raises(S.SheetError, match="colour"):
            S.clean_color(value, "c")


# ------------------------------------------------------------------ how many, and where they start

def test_copies_repeat_each_label_one_after_another():
    r = S.compute(l7160(), [["A"], ["B"]], style(), copies=3)
    assert [lines(r, cell=i) for i in range(7)] == [["A"], ["A"], ["A"], ["B"], ["B"], ["B"], []] and r.labels == 6


def test_a_part_used_sheet_starts_at_the_chosen_place():
    r = S.compute(l7160(), [["A"], ["B"]], style(), start=5)
    assert [bool(c["lines"]) for c in r.pages[0][:7]] == [False, False, False, False, True, True, False]
    with pytest.raises(S.SheetError, match="1 to 21"):
        S.compute(l7160(), [["A"]], style(), start=22)


def test_more_labels_than_fit_run_on_to_more_sheets():
    r = S.compute(l7160(), [[f"L{i}"] for i in range(25)], style(), start=3)
    assert len(r.pages) == 2 and lines(r, 0, 2) == ["L0"] and lines(r, 0, 20) == ["L18"] and lines(r, 1, 0) == ["L19"] and lines(r, 1, 5) == ["L24"]
    assert not r.pages[1][6]["lines"]


def test_repeat_fills_the_last_sheet_and_works_on_a_plain_sheet_too():
    r = S.compute(l7160(), [["A"], ["B"]], style(), fill=True)
    assert r.labels == 21 and all(c["lines"] for c in r.pages[0]) and lines(r, cell=20) == ["A"]
    assert S.compute(l7160(), [["A"]], style(), fill=True, start=20).labels == 2
    assert S.compute(plain(), [["A"]], style(), fill=True).labels == 24
    assert S.compute(plain(mode="count", count=5), [["A"]], style(), fill=True).labels == 5


def test_limits_on_copies_sheets_and_labels():
    with pytest.raises(S.SheetError, match="Copies"):
        S.compute(l7160(), [["A"]], style(), copies=0)
    with pytest.raises(S.SheetError, match="sheets"):
        S.compute(l7160(), [["A"]] * 2200, style())
    with pytest.raises(S.SheetError, match="the most is"):
        S.compute(l7160(), [["A"]] * 60, style(), copies=100)


# ------------------------------------------------------------------ the PDF

def mediabox(pdf_bytes):
    m = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf_bytes)
    return float(m[1]), float(m[2])


def test_the_pdf_is_the_paper_size_with_one_page_per_sheet_and_the_text_in_it():
    r = S.compute(l7160(), [[f"Name {i}", "Street"] for i in range(25)], style(first_bold=True))
    body = P.build(r)
    assert body.startswith(b"%PDF") and body.rstrip().endswith(b"%%EOF")
    assert abs(mediabox(body)[0] - 210 * PT) < 0.01 and abs(mediabox(body)[1] - 297 * PT) < 0.01  # exactly A4, nothing scaled
    assert b"/Count 2" in body and body.count(b"/Type /Page ") == 2
    assert b"(Name 0) Tj" in body and b"(Name 24) Tj" in body and b" 11 Tf" in body
    assert b"/BaseFont /Helvetica " in body and b"/BaseFont /Helvetica-Bold " in body and b"WinAnsiEncoding" in body


def test_only_the_fonts_in_use_are_included():
    body = P.build(S.compute(l7160(), [["Hi"]], style(family="serif", italic=True)))
    assert b"/BaseFont /Times-Italic " in body and b"/BaseFont /Courier" not in body


def test_the_pdf_places_text_where_the_layout_says():
    r = S.compute(l7160(), [["Hi"]], style(size=12, valign="top"))
    ln = r.pages[0][0]["lines"][0]
    n = lambda v: f"{v:.3f}".rstrip("0").rstrip(".")
    assert f"{n(ln['x'] * PT)} {n((297 - ln['y']) * PT)} Td (Hi) Tj".encode() in P.build(r)  # the PDF's y runs upwards from the bottom


def test_special_characters_cannot_break_the_pdf_text():
    body = P.build(S.compute(l7160(), [["a (b) \\ c", "café"]], style()))
    assert b"(a \\(b\\) \\\\ c) Tj" in body and b"(caf\xe9) Tj" in body


def test_colours_shapes_and_borders_are_drawn_only_when_chosen():
    plain_body = P.build(S.compute(l7160(), [["A"]], style()))
    assert b" rg" in plain_body and b" RG" not in plain_body and b" f Q" not in plain_body  # black text, nothing else
    r = S.compute(l7160(), [["A"], ["B"]], style(shape="ellipse", fill="#ff8000", border="#0000ff", border_width=0.5, color="#ff0000"))
    body = P.build(r)
    assert b"1 0.502 0 rg" in body and b"0 0 1 RG" in body and b"1 0 0 rg" in body  # fill, border and text colours
    assert body.count(b" f Q") == 2 and body.count(b" S Q") == 2  # only the two used places
    assert b" c h" in body and b" re" not in body  # an ellipse is curves
    rounded = P.build(S.compute(l7160(), [["A"]], style(shape="rounded", radius=3, border="#000000")))
    assert b" c h" in rounded
    square = P.build(S.compute(l7160(), [["A"]], style(shape="rect", border="#000000")))
    assert b" re" in square and b" c h" not in square


def segments(body):
    return [tuple(float(v) / PT for v in m) for m in re.findall(rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", body)]


def test_a_cut_grid_is_straight_dashed_lines_between_the_places():
    r = S.compute(plain(width=40, height=40, gap=0, margin=5), [[]], style(inset=2), blanks_fill=True)
    body = P.build(r, cut="grid")
    assert b"[1.6 1.2] 0 d" in body  # dashed
    lines = segments(body)
    cols, rows = r.sheet.cols, r.sheet.rows
    assert len(lines) == (cols + 1) + (rows + 1)  # one line per column edge and row edge: shared edges are drawn once, and they run the whole way
    vertical = [l for l in lines if abs(l[0] - l[2]) < 0.01]
    assert len(vertical) == cols + 1 and all(abs(abs(l[3] - l[1]) - rows * 40) < 0.1 for l in vertical)
    assert P.build(r).count(b" l S") == 0 and b"[1.6 1.2] 0 d" not in P.build(r)  # nothing unless asked


def test_cut_lines_sit_on_the_edge_of_the_place_with_the_label_inset_inside_them():
    r = S.compute(plain(width=40, height=40, gap=0, margin=5), [["Sugar"]], style(inset=2, shape="rounded", radius=4, border="#cc0000", fill="#fde8e6"))
    cell = r.pages[0][0]
    assert cell["art"] == {"x": cell["x"] + 2, "y": cell["y"] + 2, "w": 36, "h": 36}  # the label is smaller than the place by the inset, all round
    xs = {round(l[0], 1) for l in segments(P.build(r, cut="grid")) if abs(l[0] - l[2]) < 0.01}
    assert round(cell["x"], 1) in xs and round(cell["art"]["x"], 1) not in xs  # the line is at the place's edge, not the label's
    assert all(ln["x"] >= cell["art"]["x"] for ln in cell["lines"])  # and the text stays inside the label


def test_a_cut_line_around_each_label_follows_its_shape_inside_the_margin():
    r = S.compute(l7160(), [["A"], ["B"]], style(shape="ellipse", inset=1), start=2)
    cut = P.build(r, cut="shape")
    assert cut.count(b" c h S") == 2 and b" re S" not in cut  # a circle is cut round, only for the two printed labels
    square = P.build(S.compute(l7160(), [["A"], ["B"]], style(inset=1), start=2), cut="shape")
    assert square.count(b" re S") == 2
    assert P.build(S.compute(l7160(), [], style()), cut="shape").count(b" S\n") == 0  # nothing printed, nothing to cut


def test_cut_lines_cover_only_the_places_that_hold_labels():
    r = S.compute(plain(width=40, height=40, gap=0, margin=5), [[]] * 5, style())  # five of 24 places: one full row and one place
    lines = segments(P.build(r, cut="grid"))
    assert 0 < len(lines) < 10 and all(l[3] - l[1] <= 80.1 or l[2] - l[0] <= 160.1 for l in lines)


def test_the_space_around_a_label_is_checked():
    for bad in (dict(inset=-1), dict(inset=40), dict(inset=15, pad=5)):
        with pytest.raises(S.SheetError):
            S.compute(l7160(), [["A"]], style(**bad))
    big = S.text_box(S.art_rect(l7160().cells[0], style(inset=0)), style())[0]
    small = S.text_box(S.art_rect(l7160().cells[0], style(inset=3)), style(inset=3))[0]
    assert small["w"] == pytest.approx(big["w"] - 6) and small["h"] == pytest.approx(big["h"] - 6)


def test_cut_marks_sit_in_the_margins_beside_the_labels_and_only_when_asked():
    r = S.compute(plain(), [["A"]], style(), fill=True)
    marks = P.build(r, marks=True)
    assert marks.count(b" l S") > 20 and P.build(r).count(b" l S") == 0  # ticks for every column and row edge, top and bottom, left and right
    ticks = re.findall(rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", marks)
    first = r.pages[0][0]
    for x1, y1, x2, y2 in ticks:  # a tick never lands on a label
        x1, y1, x2, y2 = (float(v) / PT for v in (x1, y1, x2, y2))
        y1, y2 = 297 - y1, 297 - y2
        assert not any(c["x"] + 0.01 < x1 < c["x"] + c["w"] - 0.01 and c["y"] + 0.01 < y1 < c["y"] + c["h"] - 0.01 for c in r.pages[0])
    assert P.build(S.compute(l7160(), [], style()), marks=True).count(b" l S") == 0


def test_cut_marks_are_left_out_where_the_margin_has_no_room():
    r = S.compute(S.size_sheet(210, 297, 40, 40, margin=1, gap=0, mode="grid", cols=2, rows=2), [["A"]], style(), fill=True)
    top_ticks = [t for t in re.findall(rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", P.build(r, marks=True)) if float(t[1]) / PT > 297 - 4]
    assert all(float(t[3]) / PT <= 297 for t in top_ticks)  # nothing is drawn off the page


def test_a_logo_is_embedded_once_and_drawn_clipped_on_every_used_label():
    jpeg = make_jpeg(300, 200)
    info = pdfsheet.jpeg_info(jpeg)
    r = S.compute(l7160(), [["A"], ["B"], ["C"]], style(logo=S.Logo(300, 200, "top", 30)))
    body = P.build(r, logo=(jpeg, info))
    assert body.count(b"/Subtype /Image") == 1 and body.count(b"/Im0 Do") == 3 and body.count(jpeg) == 1 and b"/Filter /DCTDecode" in body
    assert body.count(b" W n ") == 3
    assert b"/Im0 Do" not in P.build(S.compute(l7160(), [["A"]], style()))


def test_an_empty_sheet_still_makes_a_valid_pdf():
    body = P.build(S.compute(l7160(), [], style()), cut="grid", marks=True)
    assert body.startswith(b"%PDF") and b"/Count 1" in body


def test_a_landscape_sheet_is_turned_onto_a_portrait_page_for_printing_only():
    r = S.compute(plain(orientation="landscape", width=70, height=30), [["Hi"]], style(), fill=True)
    assert r.sheet.paper_w > r.sheet.paper_h
    view, printed = P.build(r), P.build(r, for_media=True)
    assert mediabox(view)[0] > mediabox(view)[1] and mediabox(printed)[0] < mediabox(printed)[1]
    assert b"0 1 -1 0" in printed and b"0 1 -1 0" not in view
    assert abs(mediabox(printed)[0] - 210 * PT) < 0.01 and abs(mediabox(printed)[1] - 297 * PT) < 0.01


# ------------------------------------------------------------------ presets

PACK_PRESET = {"unit": "mm", "kind": "pack", "pack": "l7160", "paper": {"width": 210, "height": 297, "media": A4, "label": "A4 (210 × 297 mm)"}}
SIZE_PRESET = {"unit": "in", "kind": "size", "paper": {"width": 215.9, "height": 279.4}, "size": {"width": 50.8, "height": 50.8}, "mode": "grid", "cols": 3, "rows": 4,
               "look": {"shape": "ellipse", "fill": "#ffeecc", "border": "#aa7700", "family": "serif", "italic": True},
               "text": {"size": None, "align": "center", "first_scale": 1.5, "first_color": "#cc0000"}, "copies": 2, "repeat": True}


def test_the_shipped_presets_are_all_valid():
    from app.tools.labels import HERE
    shipped = pre.load_builtin(HERE / "presets.yml", clean=pre.clean_settings)
    assert len(shipped) == 3 and all(p["id"].startswith("builtin-") for p in shipped)
    assert {p["settings"]["kind"] for p in shipped} == {"pack", "size"}


def test_a_preset_is_checked_and_filled_in_with_defaults():
    x = pre.clean_settings(PACK_PRESET)
    assert x["look"]["shape"] == "rect" and x["text"]["size"] == 11.0 and x["copies"] == 1 and x["repeat"] is False and x["pack"] == "l7160"
    y = pre.clean_settings(SIZE_PRESET)
    assert y["mode"] == "grid" and (y["cols"], y["rows"]) == (3, 4) and y["text"]["size"] is None and y["look"]["fill"] == "#ffeecc" and y["margin"] == 5.0


@pytest.mark.parametrize("change,words", [
    ({"unit": "yd"}, "unit"), ({"kind": "round"}, "label pack"), ({"paper": None}, "paper"), ({"extra": 1}, "Unknown setting"),
    ({"look": {"shape": "star"}}, "shape"), ({"look": {"fill": "red"}}, "colour"), ({"look": {"family": "comic"}}, "font"), ({"look": {"zzz": 1}}, "look"),
    ({"text": {"size": 2}}, "text size"), ({"text": {"align": "full"}}, "left"), ({"copies": 0}, "Copies"), ({"repeat": "yes"}, "true or false"),
    ({"quality": 9}, "quality"), ({"resolution": [1]}, "resolution")])
def test_bad_presets_are_refused_in_plain_words(change, words):
    with pytest.raises(pre.PresetError, match=words):
        pre.clean_settings({**PACK_PRESET, **change})


def test_a_preset_can_use_a_half_page_for_a_pack_and_keeps_cut_lines():
    x = pre.clean_settings({**PACK_PRESET, "area": "bottom", "cut": "grid", "marks": True, "look": {"inset": 1.5}})
    assert x["area"] == "bottom" and x["cut"] == "grid" and x["marks"] is True and x["look"]["inset"] == 1.5
    assert pre.clean_settings(PACK_PRESET)["area"] == "full" and pre.clean_settings(PACK_PRESET)["marks"] is False
    assert pre.clean_settings(PACK_PRESET)["cut"] == "none" and pre.clean_settings(PACK_PRESET)["look"]["inset"] == 0.0
    assert pre.clean_settings({**PACK_PRESET, "outline": True})["cut"] == "shape"  # a preset saved before the grid existed keeps its line around each label
    with pytest.raises(pre.PresetError, match="Cut lines"):
        pre.clean_settings({**PACK_PRESET, "cut": "zigzag"})
    with pytest.raises(pre.PresetError, match="top or bottom"):
        pre.clean_settings({**PACK_PRESET, "area": "left"})
    with pytest.raises(pre.PresetError, match="half page"):
        pre.clean_settings({**SIZE_PRESET, "area": "top", "orientation": "landscape"})
    with pytest.raises(pre.PresetError, match="true or false"):
        pre.clean_settings({**PACK_PRESET, "marks": 1})


def test_a_preset_can_bring_the_label_text_with_it():
    x = pre.clean_settings({**PACK_PRESET, "content": {"text": "Oats\nRice", "per_line": True}})
    assert x["content"] == {"text": "Oats\nRice", "per_line": True}
    assert "content" not in pre.clean_settings(PACK_PRESET)
    for bad, words in (({"text": "x" * 20001}, "at most"), ({"text": 5}, "at most"), ({"zzz": 1}, "label text"), ("oats", "label text"), ({"text": "a", "per_line": "yes"}, "true or false")):
        with pytest.raises(pre.PresetError, match=words):
            pre.clean_settings({**PACK_PRESET, "content": bad})


def test_the_shipped_presets_are_the_three_out_of_the_box_and_kitchen_brings_its_list():
    from app.tools.labels import HERE
    shipped = pre.load_builtin(HERE / "presets.yml", clean=pre.clean_settings)
    assert [p["name"] for p in shipped] == ["Return address ×30, US Letter", "Round stickers, 40 mm on plain A4", "Kitchen Labels - A4 Sheet"]
    kitchen = shipped[2]["settings"]
    items = kitchen["content"]["text"].split("\n")
    assert kitchen["content"]["per_line"] is True and len(items) == 36 and items[0] == "Oats" and items[-1] == "Quinoa" and "Biscuits & Cookies" in items and not {"Spaghetti", "Turkish Coffee", "Monk Fruit Sweetener"} & set(items)  # 36 = 4 across × 9 down: one sheet
    assert (kitchen["size"], kitchen["look"]["inset"], kitchen["cut"]) == ({"width": 50.0, "height": 31.0}, 3.0, "grid")
    round_ = shipped[1]["settings"]
    assert (round_["size"], round_["look"]["shape"], round_["margin"], round_["cut"], round_["look"]["inset"]) == ({"width": 40.0, "height": 40.0}, "ellipse", 0.5, "grid", 3.0)


def test_a_saved_preset_keeps_the_label_text_only_when_asked_through_the_api(rig):
    keep = rig.client.post("/api/labels/presets", json={"name": "With list", "settings": {**PACK_PRESET, "content": {"text": "A\nB", "per_line": True}}}, headers=rig.h)
    assert keep.status_code == 201 and keep.get_json()["data"]["settings"]["content"]["text"] == "A\nB"
    plain_ = rig.client.post("/api/labels/presets", json={"name": "No list", "settings": PACK_PRESET}, headers=rig.h)
    assert "content" not in plain_.get_json()["data"]["settings"]


def test_a_preset_for_your_own_pack_needs_its_measurements_and_a_size_preset_its_size():
    with pytest.raises(pre.PresetError, match="measurements"):
        pre.clean_settings({**PACK_PRESET, "pack": ""})
    ok = pre.clean_settings({**PACK_PRESET, "pack": "", "size": {"width": 60, "height": 30}, "grid": {"cols": 3, "rows": 8, "left": 5, "top": 10, "gap_x": 2, "gap_y": 0}})
    assert ok["grid"]["rows"] == 8 and ok["size"]["width"] == 60
    with pytest.raises(pre.PresetError, match="size"):
        pre.clean_settings({k: v for k, v in SIZE_PRESET.items() if k != "size"})
    with pytest.raises(pre.PresetError, match="half page"):
        pre.clean_settings({**SIZE_PRESET, "area": "top", "orientation": "landscape"})


# ------------------------------------------------------------------ the API

@pytest.fixture
def rig(make_app, clock):
    app, backend = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    client = app.test_client()
    h = csrf_headers(client)

    def body(**kw):
        b = {"sheet": {"kind": "pack", "paper": {"width": 210, "height": 297}, "cols": 3, "rows": 7, "width": 63.5, "height": 38.1, "left": 7.2, "top": 15.1,
                       "gap_x": 2.5, "gap_y": 0},
             "content": {"mode": "text", "text": "Ada Lovelace\n12 Analytical Way\n\nGrace Hopper\n1 Court"},
             "style": {"size": 11, "align": "left", "valign": "middle", "pad": 3}, "copies": 1, "start": 1}
        b.update(kw)
        return b

    def post(path, data=None, **kw):
        return client.post(f"/api/labels/{path}", json=data if data is not None else body(), headers=h, **kw)

    return type("Rig", (), dict(app=app, client=client, h=h, backend=backend, body=staticmethod(body), post=staticmethod(post)))


GOOD = {"media": A4, "media_type": "stationery", "quality": 4, "copies": 1}


def plain_sheet(**kw):
    s = {"kind": "size", "paper": {"width": 210, "height": 297}, "width": 40, "height": 40, "margin": 8, "gap": 3, "mode": "fill"}
    s.update(kw)
    return s


def test_config_lists_the_packs_sizes_and_the_printers_choices(rig):
    c = rig.client.get(f"/api/labels/config?printer={PID}").get_json()["data"]
    assert len(c["templates"]) >= 15 and c["templates"][0]["id"] == "l7159" and c["printer"]["id"] == PID and c["printer"]["media"]
    assert len(c["sizes"]) >= 15 and {"label", "width", "height", "shape"} <= set(c["sizes"][0]) and len(c["papers"]) >= 5
    assert rig.client.get("/api/labels/config").get_json()["data"]["printer"] is None
    assert rig.client.get("/api/labels/config?printer=ghost").status_code == 404


def test_layout_returns_the_placed_text_and_the_look_for_the_preview(rig):
    d = rig.post("layout").get_json()["data"]
    assert (d["sheets"], d["labels"], d["per_sheet"], d["page"]) == (1, 2, 21, 1) and d["size"] == 11
    assert [ln["t"] for ln in d["cells"][0]["lines"]] == ["Ada Lovelace", "12 Analytical Way"] and d["cells"][0]["used"] and not d["cells"][2]["used"]
    assert d["look"]["shape"] == "rect" and d["look"]["family"] == "sans" and d["area"] == "full" and d["orientation"] == "portrait"


def test_a_pack_half_page_through_the_api(rig):
    d = rig.post("layout", rig.body(sheet={**rig.body()["sheet"], "area": "top"})).get_json()["data"]
    assert d["per_sheet"] == 12 and d["area"] == "top" and d["region"]["h"] == pytest.approx(148.5) and any("top half" in w for w in d["warnings"])
    assert rig.post("layout", rig.body(sheet={**rig.body()["sheet"], "area": "side"})).status_code == 400


def test_blank_labels_through_the_api(rig):
    mixed = rig.post("layout", rig.body(content={"mode": "text", "text": "Ada\n\nGrace", "blanks": {"mode": "fill"}})).get_json()["data"]
    assert mixed["labels"] == 21 and mixed["blanks"] == 19 and mixed["cells"][0]["lines"] and not mixed["cells"][2]["lines"] and mixed["cells"][2]["used"]
    some = rig.post("layout", rig.body(content={"mode": "text", "text": "Ada", "blanks": {"mode": "count", "count": 4}})).get_json()["data"]
    assert some["labels"] == 5 and some["blanks"] == 4 and not some["cells"][5]["used"]
    only = rig.post("layout", rig.body(content={"mode": "text", "text": "", "blanks": {"mode": "fill"}})).get_json()["data"]
    assert only["labels"] == 21 and only["blanks"] == 21  # no list at all: just blank labels
    none = rig.post("layout", rig.body(content={"mode": "text", "text": "Ada", "blanks": {"mode": "none", "count": 9}})).get_json()["data"]
    assert none["labels"] == 1 and none["blanks"] == 0
    assert rig.post("layout", rig.body(content={"mode": "text", "text": "A", "blanks": {"mode": "lots"}})).status_code == 400
    assert rig.post("layout", rig.body(content={"mode": "text", "text": "A", "blanks": {"mode": "count", "count": -1}})).status_code == 400
    assert rig.post("layout", rig.body(content={"mode": "text", "text": "A", "blanks": {"mode": "count", "count": 99999}})).status_code == 400
    assert rig.post("layout", rig.body(content={"mode": "blank", "count": 3})).status_code == 400  # the old separate mode is gone
    r = rig.post("pdf", rig.body(content={"mode": "text", "text": "Ada", "blanks": {"mode": "fill"}}, style={"size": 11, "fill": "#ffeecc"}))
    assert r.status_code == 200 and r.data.count(b" Tj") == 1 and r.data.count(b" f Q") == 21  # one line of text, 21 coloured labels


def test_cut_lines_and_marks_are_asked_for_in_the_request(rig):
    plain_pdf = rig.post("pdf").data
    assert b" re S" not in plain_pdf and b" l S" not in plain_pdf
    shape = rig.post("pdf", {**rig.body(), "cut": "shape"}).data
    assert shape.count(b" re S") == 2 and b"[1.6 1.2] 0 d" in shape
    grid = rig.post("pdf", {**rig.body(), "cut": "grid", "marks": True}).data
    assert b" re S" not in grid and grid.count(b" l S") > 8
    assert rig.post("pdf", {**rig.body(), "outline": True}).data.count(b" re S") == 2  # an older page's yes/no
    assert rig.post("pdf", {**rig.body(), "cut": "zigzag"}).status_code == 400


def test_the_space_around_a_label_moves_the_label_but_not_the_place(rig):
    base = rig.post("layout", rig.body(style={"size": 11, "pad": 2})).get_json()["data"]["cells"][0]
    inset = rig.post("layout", rig.body(style={"size": 11, "pad": 2, "inset": 3})).get_json()["data"]["cells"][0]
    assert (inset["x"], inset["y"], inset["w"], inset["h"]) == (base["x"], base["y"], base["w"], base["h"])
    assert inset["art"]["w"] == pytest.approx(base["art"]["w"] - 6) and inset["lines"][0]["x"] > base["lines"][0]["x"]
    assert rig.post("layout", rig.body(style={"size": 11, "inset": 40})).status_code == 400


def test_a_plain_sheet_through_the_api(rig):
    d = rig.post("layout", rig.body(sheet=plain_sheet(), style={"size": 11, "shape": "ellipse", "fill": "#ffeecc", "border": "#aa7700", "family": "serif"},
                                    fill=True)).get_json()["data"]
    assert d["per_sheet"] == 24 and d["labels"] == 24 and (d["cols"], d["rows"]) == (4, 6) and d["look"]["shape"] == "ellipse" and d["look"]["family"] == "serif"
    top = rig.post("layout", rig.body(sheet=plain_sheet(area="top"))).get_json()["data"]
    assert top["area"] == "top" and top["region"]["h"] == pytest.approx(148.5) and top["per_sheet"] == 12
    land = rig.post("layout", rig.body(sheet=plain_sheet(orientation="landscape"))).get_json()["data"]
    assert land["orientation"] == "landscape" and land["page_w"] > land["page_h"]
    grid = rig.post("layout", rig.body(sheet=plain_sheet(mode="grid", cols=2, rows=2))).get_json()["data"]
    assert grid["per_sheet"] == 4
    close = rig.post("layout", rig.body(sheet=plain_sheet(margin=1), min_margin=3)).get_json()["data"]
    assert any("margin" in w for w in close["warnings"])
    assert not any("margin" in w for w in rig.post("layout", rig.body(sheet=plain_sheet(margin=1), min_margin=3, borderless=True)).get_json()["data"]["warnings"])


def test_layout_pages_through_a_long_list_and_reports_the_spreadsheet_columns(rig):
    csv_text = "Name,City\n" + "\n".join(f"P{i},C{i}" for i in range(30))
    d = rig.post("layout", rig.body(content={"mode": "csv", "csv": csv_text, "template": "{Name}, {City}"}, page=2)).get_json()["data"]
    assert d["sheets"] == 2 and d["page"] == 2 and d["columns"] == ["Name", "City"] and d["cells"][0]["lines"][0]["t"] == "P21, C21"
    assert rig.post("layout", rig.body(content={"mode": "csv", "csv": csv_text}, page=99)).get_json()["data"]["page"] == 2  # past the end shows the last


def test_layout_reports_a_picture_placement_from_its_size_alone(rig):
    st = {"size": 11, "pad": 2, "logo": {"width": 400, "height": 200, "position": "left", "pct": 30}}
    d = rig.post("layout", rig.body(style=st)).get_json()["data"]
    assert d["cells"][0]["logo"]["w"] > 0 and "logo" not in d["cells"][2]


@pytest.mark.parametrize("change,words", [
    ({"copies": 0}, "Copies"), ({"copies": "2"}, "whole number"), ({"start": 99}, "1 to 21"), ({"content": {"mode": "pdf"}}, "typed text or a spreadsheet"),
    ({"content": {"mode": "text", "text": 5}}, "text wasn"), ({"content": {"mode": "csv", "csv": "A,B\n1,2\n", "template": "{Z}"}}, "no column"),
    ({"style": {"size": "big"}}, "text size"), ({"style": {"shape": "star"}}, "Choose square"), ({"style": {"fill": "red"}}, "colour"),
    ({"style": {"family": "comic"}}, "font"), ({"style": {"logo": 5}}, "logo"), ({"style": {"logo": {"width": 0, "height": 5}}}, "logo"),
    ({"style": {"logo": {"width": 5, "height": 5, "position": "middle"}}}, "logo goes"),
    ({"sheet": {"kind": "pack", "paper": {"width": 210, "height": 297}, "cols": 4, "rows": 7, "width": 63.5, "height": 38.1, "left": 7.2, "top": 15.1}}, "wide"),
    ({"sheet": {"kind": "pack", "paper": {"width": "a", "height": 297}}}, "paper width"), ({"sheet": {"kind": "round", "paper": {"width": 210, "height": 297}}}, "label pack"),
    ({"sheet": plain_sheet(mode="zigzag")}, "how many"), ({"sheet": plain_sheet(width=290, height=290)}, "label")])
def test_layout_refuses_bad_requests_with_a_reason(rig, change, words):
    r = rig.post("layout", rig.body(**change))
    assert r.status_code == 400 and words.lower() in r.get_json()["error"]["message"].lower()


def test_layout_without_a_body_is_a_400_not_a_crash(rig):
    assert rig.client.post("/api/labels/layout", data="nope", headers=rig.h, content_type="application/json").status_code == 400
    assert rig.client.post("/api/labels/layout", json=[1], headers=rig.h).status_code == 400


def test_text_closer_to_the_edge_than_the_printer_reaches_is_warned_about_on_a_pack_unless_borderless(rig):
    tight = rig.body(sheet={**rig.body()["sheet"], "left": 0}, style={"size": 11, "pad": 0.5, "align": "left", "valign": "top"}, min_margin=3)
    assert any("edge" in w for w in rig.post("layout", tight).get_json()["data"]["warnings"])
    assert not any("edge" in w for w in rig.post("layout", {**tight, "borderless": True}).get_json()["data"]["warnings"])
    assert not any("edge" in w for w in rig.post("layout", rig.body(min_margin=3)).get_json()["data"]["warnings"])


def test_the_pdf_is_served_inline_or_as_a_download(rig):
    r = rig.post("pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and "inline" in r.headers["Content-Disposition"] and r.data.startswith(b"%PDF")
    assert r.headers["Content-Security-Policy"] == "sandbox" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in rig.client.post("/api/labels/pdf?download=1", json=rig.body(), headers=rig.h).headers["Content-Disposition"]
    assert rig.post("pdf", rig.body(copies=0)).status_code == 400


def form(rig, path, settings, logo=None):
    import io
    data = {"settings": json.dumps(settings)}
    if logo is not None:
        data["logo"] = (io.BytesIO(logo), "logo.jpg")
    return rig.client.post(f"/api/labels/{path}", data=data, headers=rig.h, content_type="multipart/form-data")


def test_a_pdf_with_a_logo_comes_from_a_form_with_the_picture(rig):
    st = {"size": 11, "pad": 2, "logo": {"width": 300, "height": 200, "position": "top", "pct": 30}}
    r = form(rig, "pdf", rig.body(style=st), make_jpeg(300, 200))
    assert r.status_code == 200 and r.data.count(b"/Subtype /Image") == 1 and r.data.count(b"/Im0 Do") == 2
    missing = form(rig, "pdf", rig.body(style=st))
    assert missing.status_code == 400 and "logo picture is missing" in missing.get_json()["error"]["message"]
    png = form(rig, "pdf", rig.body(style=st), b"\x89PNG\r\n\x1a\n" + b"0" * 50)
    assert png.status_code == 415
    assert form(rig, "pdf", rig.body()).status_code == 200  # a form without a logo is fine when the labels carry none
    bad = rig.client.post("/api/labels/pdf", data={"settings": "{nope"}, headers=rig.h, content_type="multipart/form-data")
    assert bad.status_code == 400


def test_post_requires_the_csrf_token(rig):
    assert rig.client.post("/api/labels/layout", json=rig.body()).status_code == 403


def test_print_sends_the_pdf_with_the_chosen_paper_at_actual_size(rig):
    r = rig.post("print", {**rig.body(), "printer": PID, "options": GOOD})
    d = r.get_json()
    assert r.status_code == 202 and d["data"]["labels"] == 2 and d["data"]["sheets"] == 1
    job = rig.backend.submitted[-1]
    assert job["mime"] == "application/pdf" and job["name"] == "panda-print labels"
    assert job["options"]["media"] == A4 and job["options"]["print-scaling"] == "none" and job["options"]["media-type"] == "stationery"
    assert job["document"].startswith(b"%PDF") and abs(mediabox(job["document"])[0] - 210 * PT) < 0.1


def test_print_a_styled_plain_sheet_with_a_logo_from_a_form(rig):
    st = {"size": None, "shape": "ellipse", "fill": "#ffeecc", "logo": {"width": 300, "height": 200, "position": "background", "pct": 30}}
    r = form(rig, "print", {**rig.body(sheet=plain_sheet(), style=st), "printer": PID, "options": GOOD}, make_jpeg(300, 200))
    assert r.status_code == 202
    doc = rig.backend.submitted[-1]["document"]
    assert b"/Subtype /Image" in doc and b"1 0.933 0.8 rg" in doc


def test_a_landscape_plain_sheet_is_printed_turned_onto_a_portrait_page(rig):
    r = rig.post("print", {**rig.body(sheet=plain_sheet(orientation="landscape", width=70, height=30)), "printer": PID, "options": GOOD})
    assert r.status_code == 202
    doc = rig.backend.submitted[-1]["document"]
    assert mediabox(doc)[0] < mediabox(doc)[1] and b"0 1 -1 0" in doc


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
    sheet = {"kind": "pack", "paper": {"width": 210, "height": 297}, "cols": 3, "rows": 7, "width": 63.5, "height": 38.1}
    r = c.post("/api/labels/print", json={"sheet": sheet, "content": {"mode": "text", "text": "A"}, "style": {"size": 11}, "printer": PID, "options": GOOD}, headers=csrf_headers(c))
    assert r.status_code == 502


def test_printing_is_rate_limited_per_printer_and_a_removed_printers_log_goes_with_it(make_app):
    app, _ = make_app(print_cooldown=45, print_max_per_hour=100)
    c = app.test_client()
    h = csrf_headers(c)
    other = c.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h).get_json()["data"]["id"]
    sheet = {"kind": "pack", "paper": {"width": 210, "height": 297}, "cols": 3, "rows": 7, "width": 63.5, "height": 38.1, "left": 7.2, "top": 15.1}

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
    sheet = {"kind": "pack", "paper": {"width": 210, "height": 297}, "cols": 3, "rows": 7, "width": 63.5, "height": 38.1, "left": 7.2, "top": 15.1}
    c.post("/api/labels/print", json={"sheet": sheet, "content": {"mode": "text", "text": "A"}, "style": {"size": 11}, "printer": other, "options": {"media": A4}}, headers=h)
    assert len(second.submitted) == 1 and len(first.submitted) == 0
    assert c.get(f"/api/labels/config?printer={other}").get_json()["data"]["printer"]["name"] == "Hall"


# ------------------------------------------------------------------ presets through the API

def test_presets_ship_with_the_app_and_people_can_add_and_remove_their_own(rig):
    listed = rig.client.get("/api/labels/presets").get_json()["data"]["presets"]
    assert len(listed) >= 3 and all(p["builtin"] for p in listed)
    made = rig.client.post("/api/labels/presets", json={"name": "Stickers", "settings": SIZE_PRESET}, headers=rig.h)
    assert made.status_code == 201 and made.get_json()["data"]["settings"]["look"]["shape"] == "ellipse"
    again = rig.client.post("/api/labels/presets", json={"name": "stickers", "settings": PACK_PRESET}, headers=rig.h)
    assert again.status_code == 200 and again.get_json()["data"]["replaced"] is True  # the same name replaces
    mine = [p for p in rig.client.get("/api/labels/presets").get_json()["data"]["presets"] if not p["builtin"]]
    assert len(mine) == 1 and mine[0]["settings"]["kind"] == "pack"
    assert rig.client.delete(f"/api/labels/presets/{mine[0]['id']}", headers=rig.h).status_code == 200
    assert rig.client.delete(f"/api/labels/presets/{mine[0]['id']}", headers=rig.h).status_code == 404
    assert rig.client.delete(f"/api/labels/presets/{listed[0]['id']}", headers=rig.h).status_code == 403  # the shipped ones stay


def test_bad_presets_are_refused_by_the_api(rig):
    for body, words in (({"name": "", "settings": PACK_PRESET}, "name"), ({"name": "x", "settings": {**PACK_PRESET, "look": {"shape": "star"}}}, "shape"),
                        ({"name": "x"}, "settings"), ("nope", "preset")):
        r = rig.client.post("/api/labels/presets", json=body, headers=rig.h) if isinstance(body, dict) else rig.client.post("/api/labels/presets", data=body, headers=rig.h, content_type="application/json")
        assert r.status_code == 400 and words in r.get_json()["error"]["message"].lower()


def test_a_preset_can_be_exported_as_yaml_to_ship_with_the_code(rig):
    first = rig.client.get("/api/labels/presets").get_json()["data"]["presets"][0]
    r = rig.client.get(f"/api/labels/presets/{first['id']}/yaml")
    assert r.status_code == 200 and r.mimetype == "text/plain" and "presets:" in r.get_data(as_text=True)
    assert rig.client.get("/api/labels/presets/nope/yaml").status_code == 404


def test_presets_belong_to_everyone_and_do_not_mix_with_the_passport_tools(rig):
    rig.client.post("/api/labels/presets", json={"name": "Mine", "settings": PACK_PRESET}, headers=rig.h)
    passport = rig.client.get("/api/photosheet/presets").get_json()["data"]["presets"]
    assert all(p["name"] != "Mine" for p in passport)
