"""Tool: Photo Collage Sheet. Fit any number of photos onto one sheet by halving the paper (A4 is two A5, A5 is two A6, ...),
matching each photo to the place that suits its shape; download the PDF, or print it at actual size through the printer's
CUPS queue."""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from app.core import ratelimit
from app.core.context import AppContext
from app.core.errors import SourceError
from app.core.tool import Tool
from app.tools.collage import collage as col, presets as pre
from app.tools.photosheet import capabilities, layout as photo_layout, pdfsheet

HERE = Path(__file__).parent
JOB_NAME = "panda-print photo collage"
APP_USER = "panda-print"
MIGRATIONS = ["CREATE TABLE IF NOT EXISTS cl_prints (printer_id TEXT NOT NULL, ts REAL NOT NULL);"
              "CREATE INDEX IF NOT EXISTS cl_prints_printer ON cl_prints(printer_id, ts);"]


class BadRequest(Exception):
    def __init__(self, message: str, status: int = 400, hint: str | None = None):
        super().__init__(message)
        self.message, self.status, self.hint = message, status, hint


def create(ctx: AppContext) -> Tool:
    ctx.db.migrate("collage", MIGRATIONS)
    ctx.db.migrate("collage-presets", pre.MIGRATIONS)
    saved = pre.UserPresets(ctx.db, ctx.clock, table=pre.TABLE, clean=pre.clean_settings)
    db, settings, clock = ctx.db, ctx.settings, ctx.clock
    wipe = lambda pid: db.execute("DELETE FROM cl_prints WHERE printer_id=?", (pid,))
    ctx.printers.on_delete(wipe)
    ctx.printers.on_add(wipe)
    rule = ratelimit.RateRule(settings.print_cooldown, settings.print_max_per_hour, 3600.0)
    bp = Blueprint("collage", __name__)

    def fail(e: BadRequest):
        err = {"message": e.message}
        if e.hint:
            err["hint"] = e.hint
        return jsonify(ok=False, error=err), e.status

    # ------------------------------------------------------------------ reading the request
    def number(d: dict, key: str, default=None, label: str | None = None) -> float:
        v = d.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise BadRequest(f"{label or key} must be a number.")
        return float(v)

    def parse_request(raw, min_margin: float, paper_override: tuple[float, float] | None = None) -> col.Request:
        if not isinstance(raw, dict):
            raise BadRequest("The layout settings are missing.")
        paper = raw.get("paper") if isinstance(raw.get("paper"), dict) else {}
        pw, ph = paper_override or (number(paper, "width", label="Paper width"), number(paper, "height", label="Paper height"))
        m = raw.get("margin", 5)
        if isinstance(m, (int, float)) and not isinstance(m, bool):
            margin = (float(m),) * 4
        elif isinstance(m, list) and len(m) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in m):
            margin = tuple(float(v) for v in m)
        else:
            raise BadRequest("The margins must be a number, or four numbers (top, right, bottom, left).")
        return col.Request(paper_w=min(pw, ph), paper_h=max(pw, ph), margin=margin, gap=number(raw, "gap", 3.0, "The gap"),
                           orientation=str(raw.get("orientation", "auto")), area=str(raw.get("area", "full")), min_margin=min_margin)

    def unit(v, default=0.5) -> float:
        return min(max(float(v), 0.0), 1.0) if isinstance(v, (int, float)) and not isinstance(v, bool) else default

    def photo_looks(raw: dict, n: int) -> list[dict]:
        """How each photo is cropped or fitted: its own setting, else the sheet-wide one."""
        base_fit = "fit" if raw.get("fit") == "fit" else "fill"
        asked = raw.get("photos")
        if asked is not None and (not isinstance(asked, list) or len(asked) != n or not all(isinstance(a, dict) for a in asked)):
            raise BadRequest(f"Give each of the {n} photo{'s' if n != 1 else ''} its crop settings, or none.")
        out = []
        for i in range(n):
            a = asked[i] if asked else {}
            f = a.get("fit", base_fit)
            if f not in ("fill", "fit"):
                raise BadRequest("The photo shape handling must be fill or fit.")
            out.append({"fit": f, "crop_x": unit(a.get("crop_x", 0.5)), "crop_y": unit(a.get("crop_y", 0.5))})
        return out

    def plan(raw: dict, dims: list[tuple[int, int]], min_margin: float, paper_override=None):
        """The chosen arrangement and the photo in each place. `raw["variant"]` picks one of the ranked arrangements and
        `raw["order"]` (the photo in each place, after the person swapped some) overrides the best match."""
        req = parse_request(raw, min_margin, paper_override)
        try:
            ranked = col.arrangements(req, [w / h for w, h in dims])
            variant = raw.get("variant", 0)
            if isinstance(variant, bool) or not isinstance(variant, int) or variant < 0:
                raise BadRequest("The arrangement must be a whole number from 0.")
            variant = min(variant, len(ranked) - 1)
            arr = ranked[variant]
            assign = col.apply_order(arr, raw.get("order"))
        except col.CollageError as e:
            raise BadRequest(str(e)) from None
        return req, ranked, variant, arr, assign

    def read_images() -> list[tuple[bytes, pdfsheet.Jpeg]]:
        """The photos sent with this request, each checked to be a usable JPEG."""
        request.max_content_length = settings.max_page_mb * 2**20 + 512 * 1024  # all the photos together
        try:
            uploads = request.files.getlist("image")
        except RequestEntityTooLarge:
            raise BadRequest(f"Those pictures are larger than {settings.max_page_mb} MB together.", 413) from None
        if not uploads:
            raise BadRequest("Choose some photos first.")
        if len(uploads) > col.MAX_PHOTOS:
            raise BadRequest(f"Use at most {col.MAX_PHOTOS} photos on one sheet.")
        out = []
        for n, upload in enumerate(uploads, start=1):
            data = upload.read()
            try:
                out.append((data, pdfsheet.jpeg_info(data)))
            except pdfsheet.ImageError as e:
                raise BadRequest(f"Photo {n}: {e}" if len(uploads) > 1 else str(e), 415) from None
        return out

    def read_json_field(name: str) -> dict:
        try:
            value = json.loads(request.form.get(name, "{}"))
        except ValueError:
            raise BadRequest(f"The {name} were not understood.") from None
        if not isinstance(value, dict):
            raise BadRequest(f"The {name} were not understood.")
        return value

    def pdf_for(raw: dict, images: list, arr: col.Arrangement, req: col.Request, assign: list[int], for_media: bool) -> bytes:
        return pdfsheet.build_sheet(col.to_layout(arr, req), images, assign, per_photo=photo_looks(raw, len(images)), marks=False,
                                    outline=bool(raw.get("outline", False)), for_media=for_media, title="Photo collage")

    def printer_or_404(pid: str):
        printer = ctx.printers.get(pid or "")
        if printer is None:
            raise BadRequest("That printer isn't set up (any more).", 404)
        return printer

    def queue_caps(printer) -> dict:
        if not printer.cups_queue:
            return {"configured": False}
        try:
            caps = capabilities.from_attrs(ctx.backend_for(printer).print_options())
        except SourceError as e:
            return {"configured": True, "error": e.to_dict()}
        return {"configured": True, **caps}

    # ------------------------------------------------------------------ routes
    @bp.get("/config")
    def get_config():
        out = {"papers": capabilities.fallback()["media"], "limits": {"max_photos": col.MAX_PHOTOS, "max_mb": settings.max_page_mb},
               "printer": None, "fallback_min_margin": capabilities.DEFAULT_MIN_MARGIN_MM}
        pid = request.args.get("printer", "")
        if pid:
            try:
                printer = printer_or_404(pid)
            except BadRequest as e:
                return fail(e)
            out["printer"] = {"id": printer.id, "name": printer.name, **queue_caps(printer)}
        return jsonify(ok=True, data=out)

    @bp.post("/layout")
    def post_layout():
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return fail(BadRequest("The layout settings are missing."))
        imgs = raw.get("images")
        if not isinstance(imgs, list) or not imgs:
            return fail(BadRequest("Choose some photos first."))
        if len(imgs) > col.MAX_PHOTOS:
            return fail(BadRequest(f"Use at most {col.MAX_PHOTOS} photos on one sheet."))
        try:
            dims = [(int(i["width"]), int(i["height"])) for i in imgs]
            if any(w < 1 or h < 1 for w, h in dims):
                raise ValueError
            min_margin = 0.0 if raw.get("borderless") else float(raw.get("min_margin", capabilities.DEFAULT_MIN_MARGIN_MM))
            req, ranked, variant, arr, assign = plan(raw, dims, min_margin)
            looks = photo_looks(raw, len(dims))
        except BadRequest as e:
            return fail(e)
        except (KeyError, TypeError, ValueError):
            return fail(BadRequest("Those photo sizes weren't understood."))
        cells, dpis = [], []
        for cell, p in zip(arr.cells, assign):
            w, h = dims[p]
            dpi = round(pdfsheet.effective_dpi(pdfsheet.Jpeg(w, h, 3), cell["w"], cell["h"], looks[p]["fit"]))
            dpis.append(dpi)
            cells.append({**cell, "photo": p, "dpi": dpi, "crop": round(col._mismatch(w / h, cell["w"] / cell["h"]), 3)})
        return jsonify(ok=True, data={
            "page_w": arr.page_w, "page_h": arr.page_h, "orientation": arr.orientation, "cells": cells, "assign": assign, "count": len(cells),
            "variant": variant, "variants": len(ranked), "groups": col.describe(arr), "region": arr.region, "area": arr.area, "dpi": min(dpis),
            "warnings": col.margin_warnings(arr, req)})

    @bp.post("/pdf")
    def post_pdf():
        try:
            images = read_images()
            raw = read_json_field("settings")
            req, _ranked, _variant, arr, assign = plan(raw, [(i.width, i.height) for _, i in images], 0.0)  # a downloaded sheet is never "too close to the edge"
            body = pdf_for(raw, images, arr, req, assign, for_media=False)
        except BadRequest as e:
            return fail(e)
        resp = Response(body, mimetype="application/pdf")
        disp = "attachment" if request.args.get("download") == "1" else "inline"
        resp.headers["Content-Disposition"] = f'{disp}; filename="photo-collage.pdf"'
        resp.headers["Content-Security-Policy"] = "sandbox"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @bp.post("/print")
    def post_print():
        try:
            printer = printer_or_404(request.form.get("printer", ""))
            if not printer.cups_queue:
                raise BadRequest("This printer has no CUPS print queue, so it can't print from here.",
                                 400, "Download the PDF instead, or set up CUPS and choose the queue under Printers → Edit.")
            images = read_images()
            raw = read_json_field("settings")
            opts = read_json_field("options")
            backend = ctx.backend_for(printer)
            try:
                caps = capabilities.from_attrs(backend.print_options())
            except SourceError as e:
                return jsonify(ok=False, error=e.to_dict()), 502
            if not caps["actual_size"]:
                raise BadRequest("This print queue can't print at actual size, so the photos wouldn't come out at the size shown.", 400)
            if not caps["pdf"]:
                raise BadRequest("This print queue doesn't accept PDF files.", 400)
            try:
                media, job, _copies = capabilities.job_options(caps, opts)
            except capabilities.OptionError as e:
                raise BadRequest(str(e)) from None
            req, _ranked, _variant, arr, assign = plan(raw, [(i.width, i.height) for _, i in images], 0.0 if media["borderless"] else caps["min_margin"],
                                                      (media["width"], media["height"]))
            now = clock()
            times = [r["ts"] for r in db.query("SELECT ts FROM cl_prints WHERE printer_id=? AND ts>=?", (printer.id, now - rule.window))]
            verdict = ratelimit.check(rule, times, now)
            if not verdict.allowed:
                resp = jsonify(ok=False, error={"source": "rate-limit", "message": verdict.reason, "retry_after": verdict.retry_after})
                resp.headers["Retry-After"] = str(verdict.retry_after)
                return resp, 429
            pdf = pdf_for(raw, images, arr, req, assign, for_media=True)  # turned onto the page the way the paper is fed
            try:
                job_id = backend.submit_document(JOB_NAME, pdf, APP_USER, "application/pdf", options=job)
            except SourceError as e:
                return jsonify(ok=False, error=e.to_dict()), 502
            db.execute("INSERT INTO cl_prints(printer_id, ts) VALUES(?,?)", (printer.id, now))
        except BadRequest as e:
            return fail(e)
        return jsonify(ok=True, data={"job_id": job_id, "count": len(arr.cells), "warnings": col.margin_warnings(arr, req)}), 202

    # ------------------------------------------------------------------ presets
    def builtin() -> list[dict]:
        return pre.load_builtin(HERE / "presets.yml", clean=pre.clean_settings)

    @bp.get("/presets")
    def get_presets():
        return jsonify(ok=True, data={"presets": [*builtin(), *saved.list()]})

    @bp.post("/presets")
    def post_preset():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return fail(BadRequest("The preset wasn't understood."))
        try:
            made = saved.save(body.get("name"), body.get("settings"))
        except pre.PresetError as e:
            return fail(BadRequest(str(e)))
        return jsonify(ok=True, data=made), 200 if made["replaced"] else 201

    @bp.delete("/presets/<pid>")
    def delete_preset(pid):
        if pid.startswith("builtin-"):
            return fail(BadRequest("Presets that come with the app can't be deleted.", 403))
        if not saved.delete(pid):
            return fail(BadRequest("That preset doesn't exist (any more).", 404))
        return jsonify(ok=True)

    @bp.get("/presets/<pid>/yaml")
    def preset_yaml(pid):
        found = next((p for p in [*builtin(), *saved.list()] if p["id"] == pid), None)
        if found is None:
            return fail(BadRequest("That preset doesn't exist (any more).", 404))
        resp = Response(pre.to_yaml(found), mimetype="text/plain")
        resp.headers["Content-Disposition"] = f'inline; filename="{pre.slug(found["name"])}.yml"'
        return resp

    return Tool(
        id="collage", title="Photo Collage Sheet", description="Fit any number of photos onto one sheet by halving the paper (A4 is two A5, A5 is two A6), then download or print it",
        icon="image", blueprint=bp, ui_dir=HERE / "ui", order=17, needs_printer=False, parent="utilities")
