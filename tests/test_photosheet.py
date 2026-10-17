"""Photo Sheets: the layout maths, the PDF writer, and the API (config, layout, PDF, print), all against fakes."""
import itertools
from pathlib import Path
import json
import re
import struct

import pytest

from app.printer import ipp
from app.tools.maintenance import pages
from app.tools.photosheet import capabilities, layout as L, pdfsheet as P
from conftest import PID, csrf_headers

A4 = L.PAPERS["a4"][1:]
K4X6 = "na_index-4x6_4x6in"


def req(**kw):
    base = dict(paper_w=210, paper_h=297, photo_w=35, photo_h=45, margin=(5, 5, 5, 5), gap=2, mode="fill")
    base.update(kw)
    return L.Request(**base)


def make_jpeg(w=700, h=900, comps=3, precision=8, sof=0xC0):
    """Just enough JPEG for the size reader and the PDF (the viewer is not asked to decode it in these tests)."""
    sof_seg = struct.pack(">BHBHHB", 0xFF, 0, 0, 0, 0, 0)[:0] + b"\xff" + bytes([sof]) + struct.pack(">HBHHB", 8 + 3 * comps, precision, h, w, comps) + b"".join(bytes([i + 1, 0x11, 0]) for i in range(comps))
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof_seg + b"\xff\xda\x00\x02\xff\xd9"


# ------------------------------------------------------------------ layout maths

def test_the_requested_examples_fit_on_a4():
    fill = L.compute(req())
    assert (fill.cols, fill.rows, fill.count, fill.orientation) == (5, 6, 30, "portrait")  # 35 x 45 mm on A4: five by six
    grid = L.compute(req(mode="grid", cols=5, rows=5))
    assert (grid.cols, grid.rows, grid.count) == (5, 5, 25) and not grid.warnings


def test_a_small_photo_paper_takes_fewer():
    r = L.compute(req(paper_w=101.6, paper_h=152.4))
    assert (r.cols, r.rows, r.count) == (2, 3, 6)


def test_automatic_direction_picks_the_one_that_fits_the_most():
    r = L.compute(req(paper_w=215.9, paper_h=279.4))  # letter: landscape holds 7 x 4, portrait 5 x 5
    assert r.orientation == "landscape" and r.count == 28
    assert L.compute(req(paper_w=215.9, paper_h=279.4, orientation="portrait")).orientation == "portrait"


def test_a_grid_picks_the_direction_where_it_fits():
    r = L.compute(req(mode="grid", cols=6, rows=4))  # six across needs landscape on A4
    assert r.orientation == "landscape" and (r.cols, r.rows) == (6, 4) and not r.warnings


def test_a_grid_that_cannot_fit_is_cut_down_with_a_warning():
    r = L.compute(req(mode="grid", cols=9, rows=9))
    assert r.count < 81 and any("fit" in w for w in r.warnings)


def test_a_number_of_photos_fills_rows_from_the_top():
    r = L.compute(req(mode="count", count=7))
    assert r.count == 7 and r.cols == 5 and r.rows == 2 and len(r.cells) == 7
    over = L.compute(req(mode="count", count=500))
    assert over.count == over.capacity and any("fit" in w for w in over.warnings)


def test_nothing_that_cannot_work_is_laid_out():
    for bad in (req(photo_w=400), req(photo_w=1), req(margin=(-1, 0, 0, 0)), req(gap=-1), req(photo_w=float("nan")),
                req(margin=(5, 5, 5, float("inf"))), req(mode="everything"), req(orientation="sideways"), req(align="middle")):
        with pytest.raises(L.LayoutError):
            L.compute(bad)
    with pytest.raises(L.LayoutError, match="doesn't fit"):
        L.compute(req(photo_w=190, photo_h=280, margin=(20, 20, 20, 20)))


def test_margins_the_printer_cannot_reach_are_warned_about_unless_borderless():
    r = L.compute(req(margin=(1, 5, 5, 5)))
    assert any("top margin is under" in w for w in r.warnings)
    assert not L.compute(req(margin=(0, 0, 0, 0), min_margin=0.0)).warnings


def test_centred_and_top_left_placement():
    c = L.compute(req(margin=(5, 5, 5, 5)))
    g = c.grid
    assert abs((g["x"] - 5) - (c.page_w - 5 - (g["x"] + g["w"]))) < 1e-6  # equal space left and right of the grid
    s = L.compute(req(align="start"))
    assert s.grid["x"] == 5 and s.grid["y"] == 5


@pytest.mark.parametrize("photo,paper,margin,gap", list(itertools.product(
    [(35, 45), (50.8, 50.8), (20, 25), (50, 70)], [A4, L.PAPERS["4x6"][1:], L.PAPERS["letter"][1:]], [0, 5, 12], [0, 2, 6])))
def test_every_layout_stays_inside_the_margins_and_never_overlaps(photo, paper, margin, gap):
    try:
        r = L.compute(req(paper_w=paper[0], paper_h=paper[1], photo_w=photo[0], photo_h=photo[1],
                          margin=(margin,) * 4, gap=gap, min_margin=0.0))
    except L.LayoutError:
        return  # too big for the paper: that is a refusal, not a layout
    assert r.count == len(r.cells) <= L.MAX_COPIES
    for c in r.cells:
        assert c["x"] >= margin - 1e-6 and c["y"] >= margin - 1e-6
        assert c["x"] + c["w"] <= r.page_w - margin + 1e-6 and c["y"] + c["h"] <= r.page_h - margin + 1e-6
    for a, b in itertools.combinations(r.cells, 2):
        sep_x = max(b["x"] - (a["x"] + a["w"]), a["x"] - (b["x"] + b["w"]))
        sep_y = max(b["y"] - (a["y"] + a["h"]), a["y"] - (b["y"] + b["h"]))
        assert max(sep_x, sep_y) >= gap - 1e-6


def test_a_huge_page_of_tiny_photos_is_capped():
    r = L.compute(req(paper_w=500, paper_h=500, photo_w=5, photo_h=5, gap=0, margin=(0, 0, 0, 0), min_margin=0.0))
    assert r.count == L.MAX_COPIES and any(str(L.MAX_COPIES) in w for w in r.warnings)


def test_paper_names_from_cups_become_readable_sizes():
    assert L.parse_media("iso_a4_210x297mm")["label"] == "A4 (210 × 297 mm)"
    b = L.parse_media("om_a-4-borderless_210x297mm")
    assert b["borderless"] and b["label"] == "A4 (210 × 297 mm, borderless)"
    m = L.parse_media("na_index-4x6_4x6in")
    assert (m["width"], m["height"]) == (101.6, 152.4) and m["label"] == "4 × 6 in"
    assert L.parse_media("na_letter_8.5x11in")["label"] == "Letter (8.5 × 11 in)"
    for junk in ("", "A4", "iso_a4", None, "x_y_axb"):
        assert L.parse_media(junk) is None


def test_units():
    assert L.to_mm(2, "in") == 50.8 and L.to_mm(3.5, "cm") == 35 and L.to_mm(35, "mm") == 35
    with pytest.raises(KeyError):
        L.to_mm(1, "furlong")


# ------------------------------------------------------------------ the JPEG reader

def test_jpeg_size_and_colour_model_are_read():
    assert P.jpeg_info(make_jpeg(700, 900)) == P.Jpeg(700, 900, 3)
    assert P.jpeg_info(make_jpeg(40, 50, comps=1)).components == 1
    assert P.jpeg_info(make_jpeg(40, 50, sof=0xC2)).width == 40  # progressive is fine


@pytest.mark.parametrize("data,why", [
    (b"", "isn't a JPEG"), (b"\x89PNG\r\n\x1a\n" + b"x" * 50, "isn't a JPEG"), (b"GIF89a" + b"x" * 50, "isn't a JPEG"),
    (make_jpeg(comps=4), "CMYK"), (make_jpeg(precision=12), "8-bit"), (make_jpeg(w=0), "size"),
    (make_jpeg(w=20000, h=20000), "size"), (b"\xff\xd8\xff\xe0\x00\x10JFIF", "damaged"), (b"\xff\xd8\xff", "damaged")])
def test_pictures_that_cannot_be_used_are_refused_with_a_reason(data, why):
    with pytest.raises(P.ImageError, match=why):
        P.jpeg_info(data)


# ------------------------------------------------------------------ the PDF

def objects(pdf: bytes) -> dict:
    """Parse the cross-reference table and check every offset really points at its object."""
    start = int(re.search(rb"startxref\n(\d+)\n%%EOF\n$", pdf)[1])
    assert pdf[start:start + 4] == b"xref"
    n = int(re.match(rb"xref\n0 (\d+)\n", pdf[start:])[1])
    rows = pdf[start:].split(b"\n")[2:2 + n]
    found = {}
    for i, row in enumerate(rows[1:], start=1):
        off = int(row[:10])
        assert pdf[off:off + len(f"{i} 0 obj".encode())] == f"{i} 0 obj".encode(), f"object {i} is not at {off}"
        found[i] = off
    return found


def mediabox(pdf: bytes):
    return [float(v) for v in re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf).groups()]


def test_the_pdf_is_well_formed_and_exactly_paper_sized():
    lay = L.compute(req())
    pdf = P.build_pdf(lay, make_jpeg(), P.Jpeg(700, 900, 3))
    assert pdf.startswith(b"%PDF-1.4") and len(objects(pdf)) == 6
    w, h = mediabox(pdf)
    assert abs(w - 210 / 25.4 * 72) < 0.01 and abs(h - 297 / 25.4 * 72) < 0.01  # A4 in points, to the hundredth
    assert pdf.count(b"/Im0 Do") == 30 and pages.count_pdf_pages(pdf) == 1
    assert b"/Filter /DCTDecode" in pdf and b"/DeviceRGB" in pdf and b"/Width 700 /Height 900" in pdf


def test_the_photo_is_embedded_once_untouched():
    jpeg = make_jpeg()
    pdf = P.build_pdf(L.compute(req()), jpeg, P.Jpeg(700, 900, 3))
    assert pdf.count(jpeg) == 1  # every cell draws the same picture object, byte for byte


def test_grey_pictures_use_a_grey_colour_space():
    pdf = P.build_pdf(L.compute(req()), make_jpeg(comps=1), P.Jpeg(700, 900, 1))
    assert b"/DeviceGray" in pdf and b"/DeviceRGB" not in pdf


def test_each_photo_is_exactly_the_size_asked_for():
    lay = L.compute(req(mode="count", count=1))
    pdf = P.build_pdf(lay, make_jpeg(), P.Jpeg(700, 900, 3), marks=False)
    # the clip rectangle of the one cell: width and height in points
    clip = re.search(rb"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re W n", pdf)
    cw, ch = float(clip[3]), float(clip[4])
    assert abs(cw - 35 / 25.4 * 72) < 0.01 and abs(ch - 45 / 25.4 * 72) < 0.01


def test_a_landscape_sheet_is_turned_onto_a_portrait_page_for_printing_only():
    lay = L.compute(req(paper_w=215.9, paper_h=279.4))  # landscape wins on letter
    assert lay.orientation == "landscape"
    natural = P.build_pdf(lay, make_jpeg(), P.Jpeg(700, 900, 3))
    fed = P.build_pdf(lay, make_jpeg(), P.Jpeg(700, 900, 3), for_media=True)
    nw, nh = mediabox(natural)
    fw, fh = mediabox(fed)
    assert nw > nh and fw < fh and abs(nw - fh) < 0.01 and abs(nh - fw) < 0.01
    assert b"0 1 -1 0" in fed and b"0 1 -1 0" not in natural
    portrait = P.build_pdf(L.compute(req()), make_jpeg(), P.Jpeg(700, 900, 3), for_media=True)
    assert b"0 1 -1 0" not in portrait  # a portrait sheet is never turned


def test_cut_marks_and_outlines_are_optional():
    lay = L.compute(req())
    plain = P.build_pdf(lay, make_jpeg(), P.Jpeg(700, 900, 3), marks=False, outline=False)
    marked = P.build_pdf(lay, make_jpeg(), P.Jpeg(700, 900, 3), marks=True)
    outlined = P.build_pdf(lay, make_jpeg(), P.Jpeg(700, 900, 3), marks=False, outline=True)
    assert b" S\n" not in plain and marked.count(b" l S") > 20 and outlined.count(b" re S") == 30


def test_marks_that_would_fall_off_the_page_are_left_out():
    lay = L.compute(req(margin=(1, 1, 1, 1), min_margin=0.0))  # no room for ticks beside the grid
    pdf = P.build_pdf(lay, make_jpeg(), P.Jpeg(700, 900, 3), marks=True)
    for m in re.finditer(rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", pdf):
        w, h = mediabox(pdf)
        assert all(-0.01 <= float(m[i]) <= (w if i in (1, 3) else h) + 0.01 for i in (1, 2, 3, 4))


def test_crop_to_fill_keeps_the_shape_and_can_be_moved():
    info = P.Jpeg(1000, 1000, 3)  # a square picture into a 35 x 45 portrait cell: the sides are cropped
    lay = L.compute(req(mode="count", count=1, align="start"))
    left = P.build_pdf(lay, make_jpeg(1000, 1000), info, crop_x=0.0, marks=False)
    right = P.build_pdf(lay, make_jpeg(1000, 1000), info, crop_x=1.0, marks=False)
    def image_x(pdf):
        return float(re.search(rb"([\d.]+) 0 0 ([\d.]+) ([\d.-]+) ([\d.-]+) cm /Im0 Do", pdf)[3])
    assert image_x(left) > image_x(right)  # moving the crop shifts the picture
    m = re.search(rb"([\d.]+) 0 0 ([\d.]+) ", left)
    assert float(m[2]) >= 45 / 25.4 * 72 - 0.01  # the picture covers the full cell height


def test_fit_shows_all_of_the_picture():
    info = P.Jpeg(1000, 500, 3)  # wide picture, tall cell: fitting means it is narrower than the picture would need to fill
    lay = L.compute(req(mode="count", count=1))
    pdf = P.build_pdf(lay, make_jpeg(1000, 500), info, fit="fit", marks=False)
    m = re.search(rb"([\d.]+) 0 0 ([\d.]+) ([\d.-]+) ([\d.-]+) cm /Im0 Do", pdf)
    assert abs(float(m[1]) - 35 / 25.4 * 72) < 0.01 and float(m[2]) < 45 / 25.4 * 72  # full width, less than full height


def test_how_sharp_the_picture_will_be():
    info = P.Jpeg(700, 900, 3)
    assert round(P.effective_dpi(info, 35, 45, "fill")) == round(700 / (35 / 25.4))
    assert P.effective_dpi(P.Jpeg(100, 100, 3), 35, 45, "fill") < 100  # a small picture is flagged by a low number


def test_the_title_is_made_safe():
    pdf = P.build_pdf(L.compute(req()), make_jpeg(), P.Jpeg(700, 900, 3), title="Bad (title) \\ \n\x00")
    assert b"(Bad title" in pdf and b"\x00" not in pdf.split(b"/Title")[1][:40]
    assert len(objects(pdf)) == 6


# ------------------------------------------------------------------ what the printer offers

def test_the_ends_of_the_custom_range_and_odd_sizes_are_handled():
    attrs = {"media-supported": ["custom_min_3.5x5in", "custom_max_8.5x14in", "na_monarch_3.875x7.5in", "iso_a4_210x297mm", "na_letter_8.5x11in", "na_index-4x6_4x6in"],
             "media-default": ["iso_a4_210x297mm"]}
    keys = [m["keyword"] for m in capabilities.from_attrs(attrs)["media"]]
    assert keys == ["iso_a4_210x297mm", "na_index-4x6_4x6in", "na_letter_8.5x11in", "na_monarch_3.875x7.5in"]  # no range ends; envelopes last


def test_the_queues_choices_become_form_options():
    attrs = {"media-supported": ["iso_a4_210x297mm", "na_index-4x6_4x6in", "om_a-4-borderless_210x297mm", "custom_x_215x345mm"],
             "media-default": ["na_index-4x6_4x6in"], "media-type-supported": ["stationery", "photographic-glossy", "com.brother-bp-71"],
             "print-quality-supported": [3, 4, 5, 7], "print-quality-default": [4],
             "printer-resolution-supported": [(300, 300, "dpi"), (600, 300, "dpi")], "printer-resolution-default": [(300, 300, "dpi")],
             "print-scaling-supported": ["auto", "fit"], "media-left-margin-supported": [300, 0], "media-bottom-margin-supported": [300, 1200, 0],
             "document-format-supported": ["application/pdf", "image/jpeg"]}
    c = capabilities.from_attrs(attrs)
    assert [m["keyword"] for m in c["media"]] == ["na_index-4x6_4x6in", "iso_a4_210x297mm", "custom_x_215x345mm", "om_a-4-borderless_210x297mm"]  # default first, then by size, borderless last
    assert [t["label"] for t in c["media_types"]][:2] == ["Plain paper", "Glossy photo paper"]
    assert [q["label"] for q in c["qualities"]] == ["Draft", "Normal", "High"] and c["default_quality"] == 4
    assert [r["label"] for r in c["resolutions"]] == ["300 dpi", "600 × 300 dpi"]
    assert c["min_margin"] == 3.0 and c["actual_size"] is False  # 'none' not offered: printing at actual size is refused
    assert capabilities.fallback()["media"][0]["label"] == "A4"


# ------------------------------------------------------------------ the API

@pytest.fixture
def rig(make_app, clock):
    app, backend = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    client = app.test_client()
    h = csrf_headers(client)
    ctx = app.extensions["panda"]["ctx"]

    def settings(**kw):
        s = {"paper": {"width": 210, "height": 297}, "photo": {"width": 35, "height": 45}, "margin": 5, "gap": 2, "mode": "fill"}
        s.update(kw)
        return s

    def send(path, data=None, opts=None, st=None, printer=PID, jpeg=None, name="photo.jpg", headers=h):
        import io
        form = {"settings": json.dumps(st if st is not None else settings())}
        if jpeg is not False:
            form["image"] = (io.BytesIO(jpeg if jpeg is not None else make_jpeg()), name)
        if opts is not None:
            form["options"] = json.dumps(opts)
        if printer:
            form["printer"] = printer
        return client.post(f"/api/photosheet/{path}", data=form, headers=headers, content_type="multipart/form-data")

    return type("Rig", (), dict(app=app, client=client, h=h, ctx=ctx, backend=backend, clock=clock, settings=staticmethod(settings),
                                send=staticmethod(send)))


GOOD = {"media": K4X6, "media_type": "photographic-glossy", "quality": 5, "copies": 1}


def test_config_offers_common_choices_without_a_printer(rig):
    d = rig.client.get("/api/photosheet/config").get_json()["data"]
    assert d["printer"] is None and d["papers"][0]["label"] == "A4" and d["presets"][0]["width"] == 35.0 and d["limits"]["max_copies"] == L.MAX_COPIES


def test_config_lists_what_this_printers_queue_offers(rig):
    p = rig.client.get(f"/api/photosheet/config?printer={PID}").get_json()["data"]["printer"]
    assert p["configured"] is True and p["actual_size"] is True and p["min_margin"] == 3.0
    assert "A4 (210 × 297 mm)" in [m["label"] for m in p["media"]] and "Glossy photo paper" in [t["label"] for t in p["media_types"]]
    assert [q["label"] for q in p["qualities"]] == ["Draft", "Normal", "High"]


def test_config_for_a_printer_without_cups(rig):
    rig.client.put(f"/api/printers/{PID}", json={"cups_queue": ""}, headers=rig.h)
    assert rig.client.get(f"/api/photosheet/config?printer={PID}").get_json()["data"]["printer"]["configured"] is False
    assert rig.client.get("/api/photosheet/config?printer=ghost").status_code == 404


def test_config_when_cups_is_down_says_so(make_app):
    down, _ = make_app(fail=("cups",))
    p = down.test_client().get(f"/api/photosheet/config?printer={PID}").get_json()["data"]["printer"]
    assert p["configured"] is True and "CUPS" in p["error"]["message"]


def test_layout_answers_with_the_cells_and_how_sharp_the_picture_is(rig):
    r = rig.client.post("/api/photosheet/layout", json={**rig.settings(), "image": {"width": 700, "height": 900}}, headers=rig.h).get_json()
    d = r["data"]
    assert r["ok"] and d["count"] == 30 and len(d["cells"]) == 30 and d["dpi"] == round(700 / (35 / 25.4))


def test_layout_refusals_are_plain_400s(rig):
    for bad in ({"photo": {"width": 900, "height": 45}}, {"margin": "wide"}, {"mode": "lots"}, {"gap": None}):
        r = rig.client.post("/api/photosheet/layout", json=rig.settings(**bad), headers=rig.h)
        assert r.status_code == 400 and r.get_json()["error"]["message"], bad
    assert rig.client.post("/api/photosheet/layout", data="not json", headers=rig.h).status_code == 400
    assert rig.client.post("/api/photosheet/layout", json=[1], headers=rig.h).status_code == 400


def test_layout_borderless_lifts_the_edge_warning(rig):
    close = rig.settings(margin=0)
    assert rig.client.post("/api/photosheet/layout", json=close, headers=rig.h).get_json()["data"]["warnings"]
    assert not rig.client.post("/api/photosheet/layout", json={**close, "borderless": True}, headers=rig.h).get_json()["data"]["warnings"]


def test_pdf_download(rig):
    r = rig.send("pdf?download=1", printer=None)
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")
    assert "attachment" in r.headers["Content-Disposition"] and r.headers["Content-Security-Policy"] == "sandbox"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "inline" in rig.send("pdf", printer=None).headers["Content-Disposition"]
    assert len(objects(r.data)) == 6 and r.data.count(b"/Im0 Do") == 30


def test_pdf_refusals(rig):
    assert rig.send("pdf", jpeg=False, printer=None).status_code == 400  # no photo
    png = rig.send("pdf", jpeg=b"\x89PNG\r\n\x1a\n" + b"0" * 100, printer=None)
    assert png.status_code == 415 and "JPEG" in png.get_json()["error"]["message"]
    assert rig.send("pdf", jpeg=make_jpeg(comps=4), printer=None).status_code == 415
    assert rig.send("pdf", st={"photo": {}}, printer=None).status_code == 400  # settings missing
    assert rig.send("pdf", st=rig.settings(photo={"width": 400, "height": 400}), printer=None).status_code == 400
    bad = rig.client.post("/api/photosheet/pdf", data={"image": (__import__("io").BytesIO(make_jpeg()), "p.jpg"), "settings": "{nope"},
                          headers=rig.h, content_type="multipart/form-data")
    assert bad.status_code == 400


def test_a_picture_over_the_size_limit_is_refused(make_app):
    app, _ = make_app(max_page_mb=1)
    c = app.test_client()
    import io
    big = make_jpeg() + b"0" * (2 * 2**20)
    r = c.post("/api/photosheet/pdf", data={"image": (io.BytesIO(big), "p.jpg"), "settings": json.dumps({})},
               headers=csrf_headers(c), content_type="multipart/form-data")
    assert r.status_code == 413 and "larger than 1 MB" in r.get_json()["error"]["message"]


def test_posts_need_a_csrf_token(rig):
    for path in ("layout", "pdf", "print"):
        assert rig.client.post(f"/api/photosheet/{path}", json={}).status_code == 403


def test_print_sends_the_pdf_with_the_chosen_paper_and_quality_at_actual_size(rig):
    r = rig.send("print", opts=GOOD)
    d = r.get_json()
    assert r.status_code == 202 and d["data"]["count"] == 6  # 35 x 45 mm on 4 x 6 in: two across, three down
    job = rig.backend.submitted[-1]
    assert job["mime"] == "application/pdf" and job["name"] == "panda-print photo sheet" and job["sides"] is None
    assert job["options"] == {"media": K4X6, "print-scaling": "none", "media-type": "photographic-glossy", "print-quality": 5}
    assert job["document"].startswith(b"%PDF") and abs(mediabox(job["document"])[0] - 4 * 72) < 0.1  # a 4 x 6 in page, as the paper is
    assert d["data"]["job_id"] == job["job_id"]


def test_printing_uses_the_papers_real_size_not_what_the_browser_says(rig):
    rig.send("print", opts=GOOD, st=rig.settings(paper={"width": 999, "height": 999}))  # ignored: the queue's 4x6 wins
    w, h = mediabox(rig.backend.submitted[-1]["document"])
    assert abs(w - 4 * 72) < 0.1 and abs(h - 6 * 72) < 0.1


def test_a_landscape_sheet_is_fed_the_way_the_paper_goes_in(rig):
    letter = "na_letter_8.5x11in"
    rig.send("print", opts={"media": letter}, st=rig.settings(orientation="landscape"))
    doc = rig.backend.submitted[-1]["document"]
    w, h = mediabox(doc)
    assert w < h and b"0 1 -1 0" in doc  # portrait page, content turned


def test_print_options_that_the_queue_does_not_offer_are_refused(rig):
    for opts in ({"media": "iso_a3_297x420mm"}, {**GOOD, "media_type": "gold-leaf"}, {**GOOD, "quality": 9},
                 {**GOOD, "resolution": [1200, 1200]}, {**GOOD, "copies": 0}, {**GOOD, "copies": 21}, {**GOOD, "copies": "2"}, {}):
        r = rig.send("print", opts=opts)
        assert r.status_code == 400 and r.get_json()["error"]["message"], opts
    assert rig.backend.submitted == []  # none of them reached CUPS


def test_copies_and_resolution_are_passed_on_when_offered(rig):
    rig.backend.options_offered["printer-resolution-supported"] = [(300, 300, "dpi"), (600, 600, "dpi")]
    rig.send("print", opts={**GOOD, "resolution": [600, 600], "copies": 3})
    o = rig.backend.submitted[-1]["options"]
    assert o["copies"] == 3 and o["printer-resolution"] == (600, 600, 3)


def test_a_queue_that_cannot_print_at_actual_size_is_refused(rig):
    rig.backend.options_offered["print-scaling-supported"] = ["auto", "fit"]
    r = rig.send("print", opts=GOOD)
    assert r.status_code == 400 and "actual size" in r.get_json()["error"]["message"] and rig.backend.submitted == []


def test_a_borderless_paper_allows_photos_to_the_edge(rig):
    r = rig.send("print", opts={"media": "oe_4x-6-borderless_4x6in"}, st=rig.settings(margin=0))
    assert r.status_code == 202 and not r.get_json()["data"]["warnings"]
    r2 = rig.send("print", opts={"media": K4X6}, st=rig.settings(margin=0))
    assert r2.status_code == 202 and r2.get_json()["data"]["warnings"]  # the same margin on ordinary paper is flagged


def test_print_for_a_ghost_or_a_printer_without_cups(rig):
    assert rig.send("print", opts=GOOD, printer="ghost").status_code == 404
    assert rig.send("print", opts=GOOD, printer="").status_code == 404
    rig.client.put(f"/api/printers/{PID}", json={"cups_queue": ""}, headers=rig.h)
    r = rig.send("print", opts=GOOD)
    assert r.status_code == 400 and "no CUPS" in r.get_json()["error"]["message"] and r.get_json()["error"]["hint"]


def test_print_while_cups_is_down_is_a_502_with_the_reason(make_app):
    import io
    down, _ = make_app(fail=("cups",))
    dc = down.test_client()
    r = dc.post("/api/photosheet/print", data={"printer": PID, "settings": json.dumps({"paper": {"width": 210, "height": 297}, "photo": {"width": 35, "height": 45}}),
                                               "options": json.dumps(GOOD), "image": (io.BytesIO(make_jpeg()), "p.jpg")},
                headers=csrf_headers(dc), content_type="multipart/form-data")
    assert r.status_code == 502 and "CUPS" in r.get_json()["error"]["message"]


def test_printing_is_rate_limited_per_printer(make_app):
    app, backend = make_app(print_cooldown=45, print_max_per_hour=100)
    c = app.test_client()
    h = csrf_headers(c)
    other = c.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h).get_json()["data"]["id"]
    import io
    def go(pid):
        return c.post("/api/photosheet/print", data={"printer": pid, "settings": json.dumps({"paper": {"width": 210, "height": 297}, "photo": {"width": 35, "height": 45}}),
                                                     "options": json.dumps({"media": "iso_a4_210x297mm"}), "image": (io.BytesIO(make_jpeg()), "p.jpg")},
                      headers=h, content_type="multipart/form-data")
    assert go(PID).status_code == 202
    again = go(PID)
    assert again.status_code == 429 and again.headers["Retry-After"] and again.get_json()["error"]["source"] == "rate-limit"
    assert go(other).status_code == 202  # not held up by the other printer


def test_a_removed_printers_print_log_goes_with_it(rig):
    rig.send("print", opts=GOOD)
    assert rig.ctx.db.one("SELECT COUNT(*) AS n FROM ps_prints WHERE printer_id=?", (PID,))["n"] == 1
    rig.client.delete(f"/api/printers/{PID}", headers=rig.h)
    assert rig.ctx.db.one("SELECT COUNT(*) AS n FROM ps_prints WHERE printer_id=?", (PID,))["n"] == 0


# ------------------------------------------------------------------ the wire

def test_job_options_reach_cups_in_the_right_ipp_types(cups):
    from test_ipp import live  # the same fake CUPS server the other wire tests use
    from conftest import FakeCups
    p = live(cups)
    options = [(ipp.KEYWORD, "media", "iso_a4_210x297mm"), (ipp.KEYWORD, "print-scaling", "none"),
               (ipp.ENUM, "print-quality", 5), (ipp.RESOLUTION, "printer-resolution", (600, 600, 3)), (ipp.INTEGER, "copies", 2)]
    p.submit_document("x", b"%PDF-1.4\n%fake\n", "u", "application/pdf", options=options)
    job = ipp.decode_response(FakeCups.seen[-1][3]).of(ipp.JOB_ATTRS)[0]
    assert job["media"] == ["iso_a4_210x297mm"] and job["print-quality"] == [5] and job["copies"] == [2]
    assert job["print-scaling"] == ["none"]  # replaces the default "fit", not added beside it
    assert job["printer-resolution"] == [(600, 600, "dpi")]


def test_print_options_are_read_from_the_queue(cups):
    from test_ipp import live
    attrs = live(cups).print_options()
    assert isinstance(attrs, dict)


# ------------------------------------------------------------------ several photos on one sheet

def test_equal_copies_share_the_sheet_equally_and_leftovers_go_round():
    assert L.allocate([1, 1, 1], 30) == [10, 10, 10]
    assert L.allocate([1, 1, 1], 29) == [10, 10, 9]  # the odd one out is handed to the first in order, not lost
    assert L.allocate([1, 1, 1], 31) == [11, 10, 10]
    assert L.allocate([5], 30) == [30]


def test_copies_are_a_ratio_that_scales_up_or_down_to_the_sheet():
    assert L.allocate([2, 1, 1], 30) == [15, 8, 7]  # fits more: grows in the same ratio, rounding shared out
    assert L.allocate([6, 3, 3], 8) == [4, 2, 2]  # fits fewer: shrinks in the same ratio
    assert L.allocate([10, 1], 6) == [5, 1]  # the small one still gets its place


def test_exact_copies_when_the_page_is_not_to_be_filled():
    assert L.allocate([6, 3, 3], 30, fill=False) == [6, 3, 3]
    assert L.allocate([6, 3, 3], 12, fill=False) == [6, 3, 3]
    assert sum(L.allocate([20, 10], 12, fill=False)) == 12  # too many asked for: still scaled down to fit


def test_nobody_disappears_while_there_is_room_and_when_there_is_not_the_biggest_ratio_wins():
    assert all(c >= 1 for c in L.allocate([100, 1, 1], 3))
    assert L.allocate([1, 1, 1, 1], 3) == [1, 1, 1, 0]  # three places, four photos: someone is left off
    assert L.allocate([1, 1], 0) == [0, 0] and L.allocate([], 5) == []


@pytest.mark.parametrize("bad", [[0], [-1, 2], [1.5], [True], ["2"], [None]])
def test_copies_must_be_whole_numbers_from_one(bad):
    with pytest.raises(L.LayoutError):
        L.allocate(bad, 10)


def test_every_allocation_adds_up_and_respects_the_ratio():
    import random
    rnd = random.Random(7)
    for _ in range(600):
        k = rnd.randint(1, 8)
        req_ = [rnd.randint(1, 9) for _ in range(k)]
        slots = rnd.randint(1, 60)
        for fill in (True, False):
            got = L.allocate(req_, slots, fill)
            assert len(got) == k and all(g >= 0 for g in got)
            if fill or sum(req_) > slots:
                assert sum(got) == slots, (req_, slots, fill)
            else:
                assert got == req_
            if slots >= k:
                assert min(got) >= 1, (req_, slots, got)
            if fill and slots >= sum(req_) and len(set(req_)) == 1:
                assert max(got) - min(got) <= 1, (req_, slots, got)  # equal asks give equal shares
            for i in range(k):  # asking for more never gets you less
                for j in range(k):
                    if req_[i] > req_[j] and fill:
                        assert got[i] >= got[j] - 0, (req_, slots, got)


def test_places_go_to_photos_in_blocks_in_reading_order():
    assert L.assignment([2, 3, 1]) == [0, 0, 1, 1, 1, 2] and L.assignment([]) == []


def jpeg_n(rig_unused=None, n=3, size=(700, 900), sizes=None):
    return [make_jpeg(*(sizes[i] if sizes else size)) + bytes([i]) * 3 for i in range(n)]  # distinct bytes per photo


def send_many(rig, path, photos, counts=None, fill=None, opts=None, printer=None, **extra):
    import io
    st = rig.settings(**extra)
    if counts is not None:
        st["counts"] = counts
    if fill is not None:
        st["fill_page"] = fill
    form = {"settings": json.dumps(st), "image": [(io.BytesIO(p), f"p{i}.jpg") for i, p in enumerate(photos)]}
    if opts is not None:
        form["options"] = json.dumps(opts)
    if printer:
        form["printer"] = printer
    return rig.client.post(f"/api/photosheet/{path}", data=form, headers=rig.h, content_type="multipart/form-data")


def test_the_layout_answer_says_how_many_of_each_and_which_goes_where(rig):
    body = {**rig.settings(), "images": [{"width": 700, "height": 900}] * 3}
    d = rig.client.post("/api/photosheet/layout", json=body, headers=rig.h).get_json()["data"]
    assert d["counts"] == [10, 10, 10] and len(d["assign"]) == 30 and d["assign"][:10] == [0] * 10 and d["assign"][-1] == 2
    d = rig.client.post("/api/photosheet/layout", json={**body, "counts": [2, 1, 1]}, headers=rig.h).get_json()["data"]
    assert d["counts"] == [15, 8, 7] and [d["assign"].count(i) for i in range(3)] == [15, 8, 7]
    d = rig.client.post("/api/photosheet/layout", json={**body, "counts": [2, 1, 1], "fill_page": False}, headers=rig.h).get_json()["data"]
    assert d["counts"] == [2, 1, 1] and d["count"] == 4 and len(d["cells"]) == 4


def test_one_photo_ignores_the_copies_and_fills_the_sheet(rig):
    d = rig.client.post("/api/photosheet/layout", json={**rig.settings(), "images": [{"width": 700, "height": 900}], "counts": [3]}, headers=rig.h).get_json()["data"]
    assert d["counts"] == [30] and d["assign"] == [0] * 30


def test_bad_copies_and_too_many_photos_are_refused(rig):
    base = {**rig.settings(), "images": [{"width": 700, "height": 900}] * 3}
    for bad in ([1, 1], [1, 1, 1, 1], [0, 1, 1], [1, 1, "x"], [1, 1, 99999], "2,1,1"):
        r = rig.client.post("/api/photosheet/layout", json={**base, "counts": bad}, headers=rig.h)
        assert r.status_code == 400 and "copies" in r.get_json()["error"]["message"], bad
    r = rig.client.post("/api/photosheet/layout", json={**rig.settings(), "images": [{"width": 1, "height": 1}] * 21}, headers=rig.h)
    assert r.status_code == 400 and "at most 20" in r.get_json()["error"]["message"]


def test_more_photos_than_places_leaves_some_off_with_a_warning(rig):
    body = {**rig.settings(photo={"width": 90, "height": 130}), "images": [{"width": 700, "height": 900}] * 6, "margin": 5}
    d = rig.client.post("/api/photosheet/layout", json=body, headers=rig.h).get_json()["data"]
    assert d["count"] == 4 and sum(d["counts"]) == 4 and d["counts"].count(0) == 2
    assert any("left off the sheet" in w for w in d["warnings"])


def test_the_sharpness_warning_looks_at_the_softest_photo_used(rig):
    body = {**rig.settings(), "images": [{"width": 700, "height": 900}, {"width": 120, "height": 150}]}
    d = rig.client.post("/api/photosheet/layout", json=body, headers=rig.h).get_json()["data"]
    assert d["dpi"] < 120  # the small one decides
    d2 = rig.client.post("/api/photosheet/layout", json={**body, "counts": [1, 1], "images": [{"width": 700, "height": 900}] * 2}, headers=rig.h).get_json()["data"]
    assert d2["dpi"] > 400


def test_a_pdf_of_several_photos_draws_each_the_right_number_of_times(rig):
    photos = jpeg_n(n=3)
    r = send_many(rig, "pdf", photos, counts=[2, 1, 1])
    assert r.status_code == 200 and len(objects(r.data)) == 3 + 5
    pdf = r.data
    assert [pdf.count(f"/Im{i} Do".encode()) for i in range(3)] == [15, 8, 7]
    for p in photos:
        assert pdf.count(p) == 1  # each photo is embedded once, untouched
    assert pages.count_pdf_pages(pdf) == 1 and b"/Im0 5 0 R /Im1 6 0 R /Im2 7 0 R" in pdf


def test_the_photos_sit_in_blocks_in_reading_order(rig):
    pdf = send_many(rig, "pdf", jpeg_n(n=2), counts=[1, 1], marks=False).data
    order = [int(m) for m in re.findall(rb"/Im(\d) Do", pdf)]
    assert order == [0] * 15 + [1] * 15


def test_a_bad_photo_among_several_is_named(rig):
    r = send_many(rig, "pdf", [make_jpeg(), b"\x89PNG\r\n\x1a\n" + b"0" * 50, make_jpeg()])
    assert r.status_code == 415 and "Photo 2" in r.get_json()["error"]["message"]
    assert send_many(rig, "pdf", [make_jpeg()] * 21).status_code == 400


def test_counts_must_match_the_photos_when_building_the_pdf(rig):
    r = send_many(rig, "pdf", jpeg_n(n=3), counts=[1, 2])
    assert r.status_code == 400 and "copies" in r.get_json()["error"]["message"]


def test_printing_several_photos_sends_one_pdf_with_all_of_them(rig):
    photos = jpeg_n(n=2)
    r = send_many(rig, "print", photos, counts=[3, 1], opts=GOOD, printer=PID)
    assert r.status_code == 202
    doc = rig.backend.submitted[-1]["document"]
    assert doc.count(photos[0]) == 1 and doc.count(photos[1]) == 1
    assert [doc.count(f"/Im{i} Do".encode()) for i in range(2)] == [5, 1]  # 3:1 on the 6 places of a 4 x 6 sheet... as 5 and 1
    assert r.get_json()["data"]["count"] == 6


def test_the_preview_card_stays_in_view_css_is_in_place():
    css = (Path(__file__).resolve().parent.parent / "app/tools/photosheet/ui/ui.css").read_text()
    assert "align-self: stretch" in css and "position: sticky" in css  # the column is as tall as the form, so the card stays pinned
    assert "max-height: calc(100vh" in css  # and it is never taller than the screen
    assert "display: contents" in css  # on a phone the strip is pinned inside the page-long column
