"""Tool: Passport Sheets. Lay out copies of an edited photo (a passport photo, say) at an exact size on a sheet of paper,
download it as a PDF, or print it through the printer's CUPS queue at actual size."""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from app.core import ratelimit
from app.core.context import AppContext
from app.core.errors import SourceError
from app.core.tool import Tool
from app.printer import ipp
from app.tools.photosheet import capabilities, layout as lay, pdfsheet, presets as pre

HERE = Path(__file__).parent
JOB_NAME = "panda-print photo sheet"
APP_USER = "panda-print"
MIGRATIONS = ["CREATE TABLE IF NOT EXISTS ps_prints (printer_id TEXT NOT NULL, ts REAL NOT NULL);"
              "CREATE INDEX IF NOT EXISTS ps_prints_printer ON ps_prints(printer_id, ts);"]


class BadRequest(Exception):
    def __init__(self, message: str, status: int = 400, hint: str | None = None):
        super().__init__(message)
        self.message, self.status, self.hint = message, status, hint


def create(ctx: AppContext) -> Tool:
    ctx.db.migrate("photosheet", MIGRATIONS)
    ctx.db.migrate("photosheet-presets", pre.MIGRATIONS)
    saved = pre.UserPresets(ctx.db, ctx.clock)
    db, settings, clock = ctx.db, ctx.settings, ctx.clock
    wipe = lambda pid: db.execute("DELETE FROM ps_prints WHERE printer_id=?", (pid,))
    ctx.printers.on_delete(wipe)  # a removed printer's print log goes with it
    ctx.printers.on_add(wipe)  # and a new printer reusing the id starts clean
    rule = ratelimit.RateRule(settings.print_cooldown, settings.print_max_per_hour, 3600.0)
    bp = Blueprint("photosheet", __name__)

    def fail(e: BadRequest):
        err = {"message": e.message}
        if e.hint:
            err["hint"] = e.hint
        return jsonify(ok=False, error=err), e.status

    # ------------------------------------------------------------------ helpers
    def number(d: dict, key: str, default=None, label: str | None = None) -> float:
        v = d.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise BadRequest(f"{label or key} must be a number.")
        return float(v)

    def parse_layout(raw, min_margin: float, paper_override: tuple[float, float] | None = None) -> lay.Request:
        if not isinstance(raw, dict):
            raise BadRequest("The layout settings are missing.")
        paper = raw.get("paper") if isinstance(raw.get("paper"), dict) else {}
        photo = raw.get("photo") if isinstance(raw.get("photo"), dict) else {}
        pw, ph = paper_override or (number(paper, "width", label="Paper width"), number(paper, "height", label="Paper height"))
        m = raw.get("margin", 5)
        if isinstance(m, (int, float)) and not isinstance(m, bool):
            margin = (float(m),) * 4
        elif isinstance(m, list) and len(m) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in m):
            margin = tuple(float(v) for v in m)
        else:
            raise BadRequest("The margins must be a number, or four numbers (top, right, bottom, left).")
        return lay.Request(
            paper_w=min(pw, ph), paper_h=max(pw, ph),  # paper is always described as portrait; orientation is a layout choice
            photo_w=number(photo, "width", label="Photo width"), photo_h=number(photo, "height", label="Photo height"),
            margin=margin, gap=number(raw, "gap", 2.0, "The gap"),
            mode=str(raw.get("mode", "fill")), cols=int(number(raw, "cols", 0)), rows=int(number(raw, "rows", 0)),
            count=int(number(raw, "count", 0)), orientation=str(raw.get("orientation", "auto")),
            align=str(raw.get("align", "center")), min_margin=min_margin, area=str(raw.get("area", "full")))

    def compute(raw, min_margin: float, paper_override=None) -> lay.Layout:
        try:
            return lay.compute(parse_layout(raw, min_margin, paper_override))
        except lay.LayoutError as e:
            raise BadRequest(str(e)) from None

    def read_images() -> list[tuple[bytes, pdfsheet.Jpeg]]:
        """The photos sent with this request (one or several), each checked to be a usable JPEG."""
        request.max_content_length = settings.max_page_mb * 2**20 + 512 * 1024  # all the photos together
        try:
            uploads = request.files.getlist("image")
        except RequestEntityTooLarge:
            raise BadRequest(f"Those pictures are larger than {settings.max_page_mb} MB together.", 413) from None
        if not uploads:
            raise BadRequest("Choose a photo first.")
        if len(uploads) > lay.MAX_PHOTOS:
            raise BadRequest(f"Use at most {lay.MAX_PHOTOS} different photos on one sheet.")
        out = []
        for n, upload in enumerate(uploads, start=1):
            data = upload.read()
            try:
                out.append((data, pdfsheet.jpeg_info(data)))
            except pdfsheet.ImageError as e:
                raise BadRequest(f"Photo {n}: {e}" if len(uploads) > 1 else str(e), 415) from None
        return out

    def plan(raw: dict, n_photos: int, min_margin: float, paper_override=None) -> tuple[lay.Layout, list[int], list[int]]:
        """The layout, how many copies of each photo, and which photo sits in each place."""
        layout_ = compute(raw, min_margin, paper_override)
        asked = raw.get("counts")
        if asked is None:
            asked = [1] * n_photos  # no counts: equal shares
        if (not isinstance(asked, list) or len(asked) != n_photos
                or any(isinstance(c, bool) or not isinstance(c, int) or not 1 <= c <= lay.MAX_COPIES for c in asked)):
            raise BadRequest(f"Give each of the {n_photos} photo{'s' if n_photos != 1 else ''} a number of copies from 1 to {lay.MAX_COPIES}.")
        pin = raw.get("pin")
        if n_photos == 1:
            counts = [layout_.count]
        elif isinstance(pin, dict):  # one photo's number was set: the others give or take, keeping their own ratio
            try:
                counts = lay.pin_count(lay.allocate(asked, layout_.count, True), layout_.count, pin.get("index"), pin.get("count"))
            except lay.LayoutError as e:
                raise BadRequest(str(e)) from None
        else:
            counts = lay.allocate(asked, layout_.count, bool(raw.get("fill_page", True)))
        left_off = sum(1 for c in counts if c == 0)
        if left_off:
            layout_.warnings.append(f"Only {layout_.count} photo{'s' if layout_.count != 1 else ''} fit, so {left_off} of your {n_photos} photos "
                                    f"{'is' if left_off == 1 else 'are'} left off the sheet.")
        trimmed = layout_.count - sum(counts)
        if trimmed > 0:  # fill_page off: the places nobody asked for stay empty
            layout_.cells = layout_.cells[: sum(counts)]
            layout_.count = len(layout_.cells)
        return layout_, counts, lay.assignment(counts)

    def read_json_field(name: str) -> dict:
        try:
            value = json.loads(request.form.get(name, "{}"))
        except ValueError:
            raise BadRequest(f"The {name} were not understood.") from None
        if not isinstance(value, dict):
            raise BadRequest(f"The {name} were not understood.")
        return value

    def unit(v, default=0.5) -> float:
        return min(max(float(v), 0.0), 1.0) if isinstance(v, (int, float)) and not isinstance(v, bool) else default

    def photo_looks(raw: dict, n_photos: int) -> list[dict]:
        """How each photo is cropped or fitted: its own setting, else the sheet-wide one."""
        base_fit = "fit" if raw.get("fit") == "fit" else "fill"
        asked = raw.get("photos")
        if asked is not None and (not isinstance(asked, list) or len(asked) != n_photos or not all(isinstance(a, dict) for a in asked)):
            raise BadRequest(f"Give each of the {n_photos} photo{'s' if n_photos != 1 else ''} its crop settings, or none.")
        out = []
        for i in range(n_photos):
            a = asked[i] if asked else {}
            f = a.get("fit", base_fit)
            if f not in ("fill", "fit"):
                raise BadRequest("The photo shape handling must be fill or fit.")
            out.append({"fit": f, "crop_x": unit(a.get("crop_x", raw.get("crop_x", 0.5))), "crop_y": unit(a.get("crop_y", raw.get("crop_y", 0.5)))})
        return out

    def pdf_for(layout_: lay.Layout, images: list, assign: list[int], raw: dict, for_media: bool) -> bytes:
        return pdfsheet.build_sheet(layout_, images, assign, per_photo=photo_looks(raw, len(images)),
                                    marks=bool(raw.get("marks", True)), outline=bool(raw.get("outline", False)),
                                    for_media=for_media, title="Photo sheet")

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
        out = {"papers": capabilities.fallback()["media"], "presets": [{"label": l, "width": w, "height": h} for l, w, h in lay.PHOTO_PRESETS],
               "limits": {"max_copies": lay.MAX_COPIES, "photo_mm": [lay.MIN_PHOTO_MM, lay.MAX_PHOTO_MM], "max_mb": settings.max_page_mb},
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
        imgs = raw.get("images") if isinstance(raw.get("images"), list) else ([raw["image"]] if isinstance(raw.get("image"), dict) else [])
        n_photos = max(len(imgs), 1)
        if n_photos > lay.MAX_PHOTOS:
            return fail(BadRequest(f"Use at most {lay.MAX_PHOTOS} different photos on one sheet."))
        try:
            min_margin = float(raw.get("min_margin", capabilities.DEFAULT_MIN_MARGIN_MM))
            layout_, counts, assign = plan(raw, n_photos, 0.0 if raw.get("borderless") else min_margin)
            looks = photo_looks(raw, n_photos)
            result = layout_.to_dict()
        except (BadRequest, TypeError, ValueError) as e:
            return fail(e if isinstance(e, BadRequest) else BadRequest("Those numbers weren't understood."))
        result["counts"], result["assign"] = counts, assign
        if result["cells"]:  # how sharp the sharpest-limited photo will be at this size
            try:
                c = result["cells"][0]
                dpis = [pdfsheet.effective_dpi(pdfsheet.Jpeg(int(i["width"]), int(i["height"]), 3), c["w"], c["h"], looks[k]["fit"])
                        for k, (i, used) in enumerate(zip(imgs, counts)) if used]
                if dpis:
                    result["dpi"] = round(min(dpis))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pass
        return jsonify(ok=True, data=result)

    @bp.post("/pdf")
    def post_pdf():
        try:
            images = read_images()
            raw = read_json_field("settings")
            result, _counts, assign = plan(raw, len(images), 0.0)  # a downloaded sheet is never "too close to the edge" for a printer we don't know
            body = pdf_for(result, images, assign, raw, for_media=False)
        except BadRequest as e:
            return fail(e)
        resp = Response(body, mimetype="application/pdf")
        disp = "attachment" if request.args.get("download") == "1" else "inline"
        resp.headers["Content-Disposition"] = f'{disp}; filename="photo-sheet.pdf"'
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
                raise BadRequest("This print queue can't print at actual size, so the photos wouldn't come out at the size you asked for.", 400)
            if not caps["pdf"]:
                raise BadRequest("This print queue doesn't accept PDF files.", 400)
            try:
                media, job, _copies = capabilities.job_options(caps, opts)
            except capabilities.OptionError as e:
                raise BadRequest(str(e)) from None
            result, _counts, assign = plan(raw, len(images), 0.0 if media["borderless"] else caps["min_margin"], (media["width"], media["height"]))
            now = clock()
            times = [r["ts"] for r in db.query("SELECT ts FROM ps_prints WHERE printer_id=? AND ts>=?", (printer.id, now - rule.window))]
            verdict = ratelimit.check(rule, times, now)
            if not verdict.allowed:
                resp = jsonify(ok=False, error={"source": "rate-limit", "message": verdict.reason, "retry_after": verdict.retry_after})
                resp.headers["Retry-After"] = str(verdict.retry_after)
                return resp, 429
            pdf = pdf_for(result, images, assign, raw, for_media=True)  # rotated onto the page the way the paper is fed
            try:
                job_id = backend.submit_document(JOB_NAME, pdf, APP_USER, "application/pdf", options=job)
            except SourceError as e:
                return jsonify(ok=False, error=e.to_dict()), 502
            db.execute("INSERT INTO ps_prints(printer_id, ts) VALUES(?,?)", (printer.id, now))
        except BadRequest as e:
            return fail(e)
        return jsonify(ok=True, data={"job_id": job_id, "count": result.count, "warnings": result.warnings}), 202

    # ------------------------------------------------------------------ presets
    def builtin() -> list[dict]:
        return pre.load_builtin(HERE / "presets.yml")  # small and read on demand, so editing the file needs no restart

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

    @bp.post("/presets/yaml")
    def yaml_of_settings():
        """The YAML for settings as they are right now on the page (a preset being edited), without saving anything."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return fail(BadRequest("The settings weren't understood."))
        try:
            text = pre.to_yaml({"name": pre.clean_name(body.get("name") or "My preset"), "settings": pre.clean_settings(body.get("settings"))})
        except pre.PresetError as e:
            return fail(BadRequest(str(e)))
        return Response(text, mimetype="text/plain")

    @bp.get("/presets/<pid>/yaml")
    def preset_yaml(pid):
        found = next((p for p in [*builtin(), *saved.list()] if p["id"] == pid), None)
        if found is None:
            return fail(BadRequest("That preset doesn't exist (any more).", 404))
        resp = Response(pre.to_yaml(found), mimetype="text/plain")
        resp.headers["Content-Disposition"] = f'inline; filename="{pre.slug(found["name"])}.yml"'
        return resp

    return Tool(
        id="photosheet", title="Passport Sheet Maker", description="Lay out passport and ID photos at an exact size on a sheet of paper, then download or print it",
        icon="image", blueprint=bp, ui_dir=HERE / "ui", order=15, needs_printer=False, parent="utilities")  # listed inside Print Utilities
