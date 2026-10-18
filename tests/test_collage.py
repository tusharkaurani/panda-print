"""Photo Collage Sheet: the halving layouts, the matching of photos to places, the PDF, the presets and the API, against fakes."""
import io
import json
import re

import pytest

from app.tools.collage import collage as C, presets as pre
from app.tools.photosheet import pdfsheet
from conftest import PID, csrf_headers
from test_photosheet import make_jpeg

A4 = "iso_a4_210x297mm"
PT = 72 / 25.4
LANDSCAPE, PORTRAIT = 1.5, 0.667


def req(**kw):
    base = dict(paper_w=210, paper_h=297, margin=(5, 5, 5, 5), gap=3)
    base.update(kw)
    return C.Request(**base)


def best(ratios, **kw):
    return C.arrangements(req(**kw), ratios)


def dims(arr):
    return sorted((round(c["w"]), round(c["h"])) for c in arr.cells)


# ------------------------------------------------------------------ the halving

def test_the_example_three_photos_become_an_a5_and_two_a6():
    arr = best([LANDSCAPE, PORTRAIT, PORTRAIT], orientation="portrait")[0]
    sizes = C.describe(arr)
    assert [(g["count"]) for g in sizes] == [1, 2]
    big, small = sizes
    assert big["w"] > big["h"] and big["w"] == pytest.approx(200, abs=0.1) and big["h"] == pytest.approx(142, abs=0.1)  # an A5 turned landscape (less margins)
    assert small["w"] < small["h"] and small["w"] == pytest.approx(98.5, abs=0.1) and small["h"] == pytest.approx(142, abs=0.1)  # an A6
    # the landscape photo goes in the landscape place, the two portraits in the A6 places
    photo_in = {i: arr.assign[i] for i in range(3)}
    landscape_cell = next(i for i, c in enumerate(arr.cells) if c["w"] > c["h"])
    assert photo_in[landscape_cell] == 0


def test_two_halves_of_a4_are_two_a5_and_a5_halves_into_a6():
    two = best([LANDSCAPE, LANDSCAPE], orientation="portrait")[0]
    assert len(two.cells) == 2 and two.cells[0]["w"] == two.cells[1]["w"] and two.cells[0]["h"] == two.cells[1]["h"]
    four = best([PORTRAIT] * 4, orientation="portrait")[0]
    assert dims(four) == [(98, 142)] * 4 or len({(c["w"], c["h"]) for c in four.cells}) == 1  # four equal A6 places


def test_every_place_is_inside_the_margins_and_none_overlap_and_all_of_the_sheet_is_used():
    for n in (1, 2, 3, 5, 7, 8, 11, 16):
        for arr in best(([LANDSCAPE, PORTRAIT] * 12)[:n]):
            assert len(arr.cells) == n and sorted(arr.assign) == list(range(n))
            for a in arr.cells:
                assert a["x"] >= 5 - 1e-6 and a["y"] >= 5 - 1e-6 and a["x"] + a["w"] <= arr.page_w - 5 + 1e-6 and a["y"] + a["h"] <= arr.page_h - 5 + 1e-6
            for i, a in enumerate(arr.cells):
                for b in arr.cells[i + 1:]:
                    assert a["x"] + a["w"] <= b["x"] + 1e-6 or b["x"] + b["w"] <= a["x"] + 1e-6 or a["y"] + a["h"] <= b["y"] + 1e-6 or b["y"] + b["h"] <= a["y"] + 1e-6
            used = sum(c["w"] * c["h"] for c in arr.cells)
            assert used > 0.55 * (arr.page_w - 10) * (arr.page_h - 10)  # the sheet is well used (the rest is the gaps)


def test_the_gap_separates_the_places():
    arr = best([PORTRAIT, PORTRAIT], orientation="portrait", gap=10)[0]
    a, b = sorted(arr.cells, key=lambda c: (c["y"], c["x"]))
    gap = max(b["x"] - (a["x"] + a["w"]), b["y"] - (a["y"] + a["h"]))
    assert gap == pytest.approx(10, abs=0.01)
    tight = best([PORTRAIT, PORTRAIT], orientation="portrait", gap=0)[0]
    assert sum(c["w"] * c["h"] for c in tight.cells) > sum(c["w"] * c["h"] for c in arr.cells)


# ------------------------------------------------------------------ matching photos to places

def test_a_photo_goes_where_its_shape_fits_so_little_is_cropped():
    arr = best([PORTRAIT, LANDSCAPE, PORTRAIT], orientation="portrait")[0]
    for cell, photo in zip(arr.cells, arr.assign):
        ratio = [PORTRAIT, LANDSCAPE, PORTRAIT][photo]
        assert abs((cell["w"] > cell["h"]) - (ratio > 1)) == 0  # landscape photos in landscape places and portraits in portrait ones


def test_the_assignment_is_the_cheapest_possible():
    from itertools import permutations
    ratios = [1.5, 0.7, 1.0, 0.6]
    arr = best(ratios, orientation="portrait")[0]
    area = sum(c["w"] * c["h"] for c in arr.cells)
    cost = lambda perm: sum(C._mismatch(ratios[p], c["w"] / c["h"]) * c["w"] * c["h"] / area for c, p in zip(arr.cells, perm))
    assert cost(arr.assign) == pytest.approx(min(cost(p) for p in permutations(range(4))), abs=1e-9)


def test_the_hungarian_solver_finds_the_minimum_on_random_matrices():
    import random
    from itertools import permutations
    rnd = random.Random(3)
    for n in (1, 2, 3, 4, 5, 6):
        m = [[rnd.random() for _ in range(n)] for _ in range(n)]
        pick = C._hungarian(m)
        assert sorted(pick) == list(range(n))
        assert sum(m[i][pick[i]] for i in range(n)) == pytest.approx(min(sum(m[i][p[i]] for i in range(n)) for p in permutations(range(n))))


def test_arrangements_are_ranked_best_first_and_are_different_from_each_other():
    ranked = best([LANDSCAPE, PORTRAIT, PORTRAIT, LANDSCAPE, PORTRAIT])
    assert 2 <= len(ranked) <= C.VARIANTS and [a.cost for a in ranked] == sorted(a.cost for a in ranked)
    assert len({a.key for a in ranked}) == len(ranked)


def test_automatic_direction_takes_the_page_turned_when_that_suits_the_photos_better():
    wide = best([2.0, 2.0])[0]
    assert wide.orientation == "landscape" or wide.orientation == "portrait"
    stacked = best([2.0, 2.0], orientation="portrait")[0]
    assert wide.cost <= stacked.cost
    assert best([1.5] * 3, orientation="portrait")[0].page_h > best([1.5] * 3, orientation="portrait")[0].page_w
    assert best([1.5] * 3, orientation="landscape")[0].page_w > best([1.5] * 3, orientation="landscape")[0].page_h


def test_one_photo_fills_the_whole_page_and_twenty_four_still_fit():
    one = best([PORTRAIT], orientation="portrait")[0]
    assert len(one.cells) == 1 and one.cells[0]["w"] == pytest.approx(200) and one.cells[0]["h"] == pytest.approx(287)
    many = best([LANDSCAPE, PORTRAIT] * 12)
    assert len(many[0].cells) == 24


def test_a_half_page_uses_only_that_half_and_is_never_turned():
    top = best([LANDSCAPE, PORTRAIT, PORTRAIT], area="top")[0]
    assert top.orientation == "portrait" and top.area == "top" and all(c["y"] + c["h"] <= 297 / 2 - 5 + 1e-6 for c in top.cells)
    bottom = best([LANDSCAPE, PORTRAIT], area="bottom")[0]
    assert all(c["y"] >= 297 / 2 + 5 - 1e-6 for c in bottom.cells) and bottom.region["y"] == pytest.approx(148.5)
    with pytest.raises(C.CollageError, match="half page"):
        best([1.0], area="top", orientation="landscape")


def test_it_runs_fast_even_for_the_most_photos():
    import time
    t = time.time()
    best([LANDSCAPE, PORTRAIT] * 12)
    assert time.time() - t < 3


@pytest.mark.parametrize("kw,words", [({"paper_w": 10}, "paper width"), ({"margin": (200, 5, 5, 5)}, "top margin"), ({"gap": 99}, "gap"),
                                      ({"orientation": "sideways"}, "portrait, landscape"), ({"area": "left"}, "top or bottom"),
                                      ({"paper_w": 60, "paper_h": 80, "margin": (38, 5, 38, 5)}, "no room")])
def test_bad_requests_are_explained(kw, words):
    with pytest.raises(C.CollageError, match=words):
        best([1.0], **kw)


def test_no_photos_too_many_or_bad_sizes_are_refused():
    for ratios, words in (([], "between 1 and"), ([1.0] * 25, "between 1 and"), ([0], "isn't usable"), ([float("nan")], "isn't usable"), ([-1], "isn't usable")):
        with pytest.raises(C.CollageError, match=words):
            best(ratios)


def test_too_many_photos_for_a_tiny_paper_say_so():
    with pytest.raises(C.CollageError, match="don't fit"):
        C.arrangements(req(paper_w=60, paper_h=80), [1.0] * 20)


def test_the_order_can_be_set_by_hand_but_only_to_a_real_arrangement():
    arr = best([LANDSCAPE, PORTRAIT, PORTRAIT], orientation="portrait")[0]
    assert C.apply_order(arr, None) == arr.assign
    assert C.apply_order(arr, [2, 0, 1]) == [2, 0, 1]
    for bad in ([0, 1], [0, 0, 1], [0, 1, 3], "012", [True, 1, 2], [0.0, 1, 2]):
        with pytest.raises(C.CollageError, match="isn't valid"):
            C.apply_order(arr, bad)


def test_the_arrangement_converts_to_the_passport_layout_the_pdf_writer_draws():
    arr = best([LANDSCAPE, PORTRAIT, PORTRAIT], orientation="portrait")[0]
    layout = C.to_layout(arr, req(), ["note"])
    assert layout.page_w == arr.page_w and layout.cells == arr.cells and layout.warnings == ["note"] and layout.count == 3 and layout.grid["w"] > 0


def test_margins_closer_to_the_edge_than_the_printer_reaches_are_flagged_on_the_used_edges_only():
    arr = best([1.0, 1.0], margin=(1, 5, 5, 5))[0]
    assert C.margin_warnings(arr, req(margin=(1, 5, 5, 5), min_margin=3))[0].startswith("The top margin is under 3 mm")
    assert C.margin_warnings(arr, req(margin=(5, 5, 5, 5), min_margin=3)) == []
    assert C.margin_warnings(arr, req(margin=(1, 5, 5, 5), min_margin=0)) == []
    top = best([1.0, 1.0], area="top")[0]
    assert C.margin_warnings(top, req(area="top", margin=(5, 5, 1, 5), min_margin=3)) == []  # the bottom of the top half is not the paper's edge


# ------------------------------------------------------------------ presets

PRESET = {"unit": "mm", "paper": {"width": 210, "height": 297, "media": A4, "label": "A4"}, "area": "top", "margin": 4, "gap": 2, "fit": "fit", "outline": True,
          "media_type": "photographic-glossy", "quality": 5, "copies": 2}


def test_a_preset_is_checked_and_filled_in_with_defaults():
    x = pre.clean_settings({"paper": {"width": 210, "height": 297}})
    assert (x["orientation"], x["area"], x["fit"], x["margin"], x["gap"], x["outline"]) == ("auto", "full", "fill", 5.0, 3.0, False)
    y = pre.clean_settings(PRESET)
    assert y["area"] == "top" and y["fit"] == "fit" and y["quality"] == 5 and y["copies"] == 2 and y["paper"]["media"] == A4


@pytest.mark.parametrize("change,words", [({"unit": "yd"}, "unit"), ({"paper": None}, "paper"), ({"zzz": 1}, "Unknown setting"), ({"orientation": "x"}, "direction"),
                                          ({"area": "left"}, "full, top or bottom"), ({"area": "top", "orientation": "landscape"}, "half page"),
                                          ({"fit": "stretch"}, "fill or fit"), ({"outline": 1}, "true or false"), ({"quality": 9}, "quality"),
                                          ({"copies": 0}, "Copies"), ({"resolution": [1]}, "resolution")])
def test_bad_presets_are_refused_in_plain_words(change, words):
    with pytest.raises(pre.PresetError, match=words):
        pre.clean_settings({**PRESET, **change})


# ------------------------------------------------------------------ the API

@pytest.fixture
def rig(make_app, clock):
    app, backend = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    client = app.test_client()
    h = csrf_headers(client)

    def settings(**kw):
        s = {"paper": {"width": 210, "height": 297}, "margin": 5, "gap": 3, "orientation": "portrait"}
        s.update(kw)
        return s

    def send(path, jpegs, st=None, opts=None, printer=PID):
        form = {"settings": json.dumps(st if st is not None else settings())}
        form["image"] = [(io.BytesIO(j), f"p{i}.jpg") for i, j in enumerate(jpegs)]
        if opts is not None:
            form["options"] = json.dumps(opts)
        if printer:
            form["printer"] = printer
        return client.post(f"/api/collage/{path}", data=form, headers=h, content_type="multipart/form-data")

    def layout(images, **kw):
        return client.post("/api/collage/layout", json={**settings(**kw), "images": [{"width": w, "height": hh} for w, hh in images]}, headers=h)

    return type("Rig", (), dict(app=app, client=client, h=h, backend=backend, settings=staticmethod(settings), send=staticmethod(send), layout=staticmethod(layout)))


THREE = [(1500, 1000), (700, 1000), (700, 1000)]
GOOD = {"media": A4, "media_type": "stationery", "quality": 4, "copies": 1}


def test_config_lists_the_papers_and_the_printers_choices(rig):
    c = rig.client.get(f"/api/collage/config?printer={PID}").get_json()["data"]
    assert c["printer"]["id"] == PID and c["printer"]["media"] and len(c["papers"]) >= 5 and c["limits"]["max_photos"] == 24
    assert rig.client.get("/api/collage/config").get_json()["data"]["printer"] is None
    assert rig.client.get("/api/collage/config?printer=ghost").status_code == 404


def test_layout_returns_the_places_the_photos_and_the_choices_to_try(rig):
    d = rig.layout(THREE).get_json()["data"]
    assert d["count"] == 3 and sorted(d["assign"]) == [0, 1, 2] and d["variants"] >= 2 and d["variant"] == 0 and d["orientation"] == "portrait"
    assert [g["count"] for g in d["groups"]] == [1, 2]  # one big place and two small ones
    big = next(c for c in d["cells"] if c["w"] > c["h"])
    assert big["photo"] == 0 and all({"x", "y", "w", "h", "photo", "dpi", "crop"} <= set(c) for c in d["cells"]) and d["dpi"] > 0
    assert d["cells"][0]["crop"] == pytest.approx(0, abs=0.35)


def test_another_arrangement_and_a_hand_swap_are_asked_for_in_the_request(rig):
    first = rig.layout(THREE).get_json()["data"]
    other = rig.layout(THREE, variant=1).get_json()["data"]
    assert other["variant"] == 1 and (other["groups"] != first["groups"] or other["orientation"] != first["orientation"] or other["cells"] != first["cells"])
    assert rig.layout(THREE, variant=99).get_json()["data"]["variant"] == first["variants"] - 1  # past the end shows the last
    swapped = rig.layout(THREE, order=list(reversed(first["assign"]))).get_json()["data"]
    assert swapped["assign"] == list(reversed(first["assign"])) and [c["photo"] for c in swapped["cells"]] == swapped["assign"]
    assert rig.layout(THREE, order=[0, 0, 1]).status_code == 400
    assert rig.layout(THREE, variant=-1).status_code == 400 and rig.layout(THREE, variant="a").status_code == 400


def test_the_layout_follows_the_papers_direction_half_and_margins(rig):
    land = rig.layout(THREE, orientation="landscape").get_json()["data"]
    assert land["orientation"] == "landscape" and land["page_w"] > land["page_h"]
    top = rig.layout(THREE, area="top").get_json()["data"]
    assert top["area"] == "top" and top["region"]["h"] == pytest.approx(148.5) and all(c["y"] + c["h"] <= 148.5 for c in top["cells"])
    close = rig.layout(THREE, margin=1, min_margin=3).get_json()["data"]
    assert any("margin" in w for w in close["warnings"])
    assert not any("margin" in w for w in rig.layout(THREE, margin=1, min_margin=3, borderless=True).get_json()["data"]["warnings"])


@pytest.mark.parametrize("body,words", [({"images": []}, "photos first"), ({}, "photos first"), ({"images": [{"width": 0, "height": 5}]}, "weren't understood"),
                                        ({"images": [{"width": "a", "height": 5}]}, "weren't understood"), ({"images": [{"width": 5}]}, "weren't understood")])
def test_layout_refuses_bad_photo_lists(rig, body, words):
    r = rig.client.post("/api/collage/layout", json={**rig.settings(), **body}, headers=rig.h)
    assert r.status_code == 400 and words in r.get_json()["error"]["message"]


def test_layout_refuses_bad_settings_and_too_many_photos(rig):
    assert rig.layout(THREE, gap=99).status_code == 400
    assert rig.layout(THREE, orientation="x").status_code == 400
    assert rig.layout(THREE, margin="wide").status_code == 400
    assert rig.layout(THREE, photos=[{"fit": "x"}] * 3).status_code == 400
    assert rig.layout(THREE, photos=[{}]).status_code == 400
    assert rig.layout([(10, 10)] * 25).status_code == 400
    assert rig.client.post("/api/collage/layout", data="nope", headers=rig.h, content_type="application/json").status_code == 400


def test_the_pdf_is_the_paper_size_with_each_photo_embedded_once_and_drawn_in_its_place(rig):
    jpegs = [make_jpeg(1500, 1000), make_jpeg(700, 1000), make_jpeg(700, 1000)]
    r = rig.send("pdf", jpegs)
    body = r.data
    assert r.status_code == 200 and r.mimetype == "application/pdf" and body.startswith(b"%PDF") and body.count(b"/Subtype /Image") == 3
    box = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", body)
    assert abs(float(box[1]) - 210 * PT) < 0.05 and abs(float(box[2]) - 297 * PT) < 0.05
    assert body.count(b" Do") == 3 and "attachment" in rig.client.post("/api/collage/pdf?download=1", data={"settings": "{}", "image": (io.BytesIO(jpegs[0]), "a.jpg")}, headers=rig.h, content_type="multipart/form-data").headers.get("Content-Disposition", "attachment")


def test_the_pdf_follows_the_chosen_arrangement_and_the_hand_swap(rig):
    jpegs = [make_jpeg(1500, 1000), make_jpeg(700, 1000), make_jpeg(700, 1000)]
    a = rig.send("pdf", jpegs).data
    b = rig.send("pdf", jpegs, rig.settings(variant=1)).data
    c = rig.send("pdf", jpegs, rig.settings(order=[1, 0, 2])).data
    assert a != b and a != c
    assert rig.send("pdf", jpegs, rig.settings(order=[1, 1, 2])).status_code == 400


def test_the_pdf_can_outline_each_photo_and_crop_or_fit_each_one(rig):
    jpegs = [make_jpeg(1500, 1000), make_jpeg(700, 1000)]
    plain = rig.send("pdf", jpegs).data
    outlined = rig.send("pdf", jpegs, rig.settings(outline=True)).data
    assert outlined.count(b" re S") == 2 and b" re S" not in plain
    assert rig.send("pdf", jpegs, rig.settings(photos=[{"fit": "fit"}, {"fit": "fill", "crop_x": 0.1, "crop_y": 0.9}])).status_code == 200
    assert rig.send("pdf", jpegs, rig.settings(photos=[{"fit": "x"}, {}])).status_code == 400


def test_the_pdf_refuses_no_photos_bad_files_and_too_many(rig):
    assert rig.send("pdf", []).status_code == 400
    assert rig.send("pdf", [b"\x89PNG\r\n\x1a\n" + b"0" * 100]).status_code == 415
    assert rig.send("pdf", [make_jpeg()] * 25).status_code == 400
    bad = rig.client.post("/api/collage/pdf", data={"settings": "{nope", "image": (io.BytesIO(make_jpeg()), "a.jpg")}, headers=rig.h, content_type="multipart/form-data")
    assert bad.status_code == 400


def test_post_requires_the_csrf_token(rig):
    assert rig.client.post("/api/collage/layout", json={"images": [{"width": 5, "height": 5}]}).status_code == 403


def test_print_sends_the_pdf_with_the_chosen_paper_at_actual_size(rig):
    r = rig.send("print", [make_jpeg(1500, 1000), make_jpeg(700, 1000), make_jpeg(700, 1000)], opts=GOOD)
    assert r.status_code == 202 and r.get_json()["data"]["count"] == 3
    job = rig.backend.submitted[-1]
    assert job["mime"] == "application/pdf" and job["name"] == "panda-print photo collage"
    assert job["options"]["media"] == A4 and job["options"]["print-scaling"] == "none" and job["options"]["media-type"] == "stationery"
    assert job["document"].startswith(b"%PDF") and job["document"].count(b"/Subtype /Image") == 3


def test_a_landscape_sheet_is_turned_onto_a_portrait_page_for_printing_only(rig):
    jpegs = [make_jpeg(1500, 1000)] * 2
    r = rig.send("print", jpegs, rig.settings(orientation="landscape"), opts=GOOD)
    assert r.status_code == 202
    doc = rig.backend.submitted[-1]["document"]
    box = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", doc)
    assert float(box[1]) < float(box[2]) and b"0 1 -1 0" in doc
    view = rig.send("pdf", jpegs, rig.settings(orientation="landscape")).data
    vbox = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", view)
    assert float(vbox[1]) > float(vbox[2]) and b"0 1 -1 0" not in view


@pytest.mark.parametrize("change,words", [({"media": "iso_a9_37x52mm"}, "paper size"), ({"media_type": "nope"}, "paper types"),
                                          ({"quality": 9}, "qualities"), ({"copies": 99}, "Copies")])
def test_print_options_the_queue_does_not_offer_are_refused(rig, change, words):
    r = rig.send("print", [make_jpeg()], opts={**GOOD, **change})
    assert r.status_code == 400 and words in r.get_json()["error"]["message"]


def test_print_for_a_ghost_or_a_printer_without_cups(rig):
    assert rig.send("print", [make_jpeg()], opts=GOOD, printer="ghost").status_code == 404
    rig.client.put(f"/api/printers/{PID}", json={"cups_queue": ""}, headers=rig.h)
    r = rig.send("print", [make_jpeg()], opts=GOOD)
    assert r.status_code == 400 and "no CUPS print queue" in r.get_json()["error"]["message"]
    assert rig.client.get(f"/api/collage/config?printer={PID}").get_json()["data"]["printer"]["configured"] is False


def test_print_while_cups_is_down_is_a_502(make_app):
    app, _ = make_app(fail=("cups",), timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    c = app.test_client()
    form = {"settings": json.dumps({"paper": {"width": 210, "height": 297}}), "image": (io.BytesIO(make_jpeg()), "a.jpg"), "options": json.dumps(GOOD), "printer": PID}
    assert c.post("/api/collage/print", data=form, headers=csrf_headers(c), content_type="multipart/form-data").status_code == 502


def test_printing_is_rate_limited_per_printer_and_a_removed_printers_log_goes_with_it(make_app):
    app, _ = make_app(print_cooldown=45, print_max_per_hour=100)
    c = app.test_client()
    h = csrf_headers(c)
    other = c.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h).get_json()["data"]["id"]

    def go(pid):
        form = {"settings": json.dumps({"paper": {"width": 210, "height": 297}}), "image": (io.BytesIO(make_jpeg()), "a.jpg"), "options": json.dumps({"media": A4}), "printer": pid}
        return c.post("/api/collage/print", data=form, headers=h, content_type="multipart/form-data")
    assert go(PID).status_code == 202
    again = go(PID)
    assert again.status_code == 429 and again.headers["Retry-After"] and again.get_json()["error"]["source"] == "rate-limit"
    assert go(other).status_code == 202
    ctx = app.extensions["panda"]["ctx"]
    assert ctx.db.one("SELECT COUNT(*) AS n FROM cl_prints WHERE printer_id=?", (PID,))["n"] == 1
    c.delete(f"/api/printers/{PID}", headers=h)
    assert ctx.db.one("SELECT COUNT(*) AS n FROM cl_prints WHERE printer_id=?", (PID,))["n"] == 0


def test_a_sheet_goes_to_the_chosen_printer_only(make_app):
    app, first = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    c = app.test_client()
    h = csrf_headers(c)
    ctx = app.extensions["panda"]["ctx"]
    other = c.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h).get_json()["data"]["id"]
    second = ctx.backend_for(ctx.printers.get(other))
    form = {"settings": json.dumps({"paper": {"width": 210, "height": 297}}), "image": (io.BytesIO(make_jpeg()), "a.jpg"), "options": json.dumps({"media": A4}), "printer": other}
    c.post("/api/collage/print", data=form, headers=h, content_type="multipart/form-data")
    assert len(second.submitted) == 1 and len(first.submitted) == 0
    assert c.get(f"/api/collage/config?printer={other}").get_json()["data"]["printer"]["name"] == "Hall"


def test_presets_start_empty_and_people_can_add_replace_and_remove_their_own(rig):
    assert rig.client.get("/api/collage/presets").get_json()["data"]["presets"] == []
    made = rig.client.post("/api/collage/presets", json={"name": "Family", "settings": PRESET}, headers=rig.h)
    assert made.status_code == 201 and made.get_json()["data"]["settings"]["area"] == "top"
    again = rig.client.post("/api/collage/presets", json={"name": "family", "settings": {**PRESET, "area": "full"}}, headers=rig.h)
    assert again.status_code == 200 and again.get_json()["data"]["replaced"] is True
    mine = rig.client.get("/api/collage/presets").get_json()["data"]["presets"]
    assert len(mine) == 1 and mine[0]["settings"]["area"] == "full"
    assert rig.client.get(f"/api/collage/presets/{mine[0]['id']}/yaml").status_code == 200
    assert rig.client.delete(f"/api/collage/presets/{mine[0]['id']}", headers=rig.h).status_code == 200
    assert rig.client.delete(f"/api/collage/presets/{mine[0]['id']}", headers=rig.h).status_code == 404
    assert rig.client.delete("/api/collage/presets/builtin-x", headers=rig.h).status_code == 403
    assert rig.client.get("/api/collage/presets/nope/yaml").status_code == 404


def test_bad_presets_are_refused_by_the_api_and_do_not_mix_with_the_other_tools(rig):
    for body, words in (({"name": "", "settings": PRESET}, "name"), ({"name": "x", "settings": {**PRESET, "area": "left"}}, "full, top or bottom"), ({"name": "x"}, "settings")):
        r = rig.client.post("/api/collage/presets", json=body, headers=rig.h)
        assert r.status_code == 400 and words in r.get_json()["error"]["message"].lower()
    rig.client.post("/api/collage/presets", json={"name": "Mine", "settings": PRESET}, headers=rig.h)
    assert all(p["name"] != "Mine" for p in rig.client.get("/api/photosheet/presets").get_json()["data"]["presets"])
    assert all(p["name"] != "Mine" for p in rig.client.get("/api/labels/presets").get_json()["data"]["presets"])
