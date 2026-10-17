"""Tool: Photo Sheets. Lay out copies of an edited photo (a passport photo, say) at an exact size on a sheet of paper,
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
from app.tools.photosheet import capabilities, layout as lay, pdfsheet

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
            align=str(raw.get("align", "center")), min_margin=min_margin)

    def compute(raw, min_margin: float, paper_override=None) -> lay.Layout:
        try:
            return lay.compute(parse_layout(raw, min_margin, paper_override))
        except lay.LayoutError as e:
            raise BadRequest(str(e)) from None

    def read_image() -> tuple[bytes, pdfsheet.Jpeg]:
        request.max_content_length = settings.max_page_mb * 2**20 + 512 * 1024
        try:
            upload = request.files.get("image")
        except RequestEntityTooLarge:
            raise BadRequest(f"That picture is larger than {settings.max_page_mb} MB.", 413) from None
        if upload is None:
            raise BadRequest("Choose a photo first.")
        data = upload.read()
        try:
            return data, pdfsheet.jpeg_info(data)
        except pdfsheet.ImageError as e:
            raise BadRequest(str(e), 415) from None

    def read_json_field(name: str) -> dict:
        try:
            value = json.loads(request.form.get(name, "{}"))
        except ValueError:
            raise BadRequest(f"The {name} were not understood.") from None
        if not isinstance(value, dict):
            raise BadRequest(f"The {name} were not understood.")
        return value

    def pdf_for(layout_: lay.Layout, data: bytes, info: pdfsheet.Jpeg, raw: dict, for_media: bool) -> bytes:
        fit = "fit" if raw.get("fit") == "fit" else "fill"
        def unit(key):
            v = raw.get(key, 0.5)
            return min(max(float(v), 0.0), 1.0) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.5
        return pdfsheet.build_pdf(layout_, data, info, fit=fit, crop_x=unit("crop_x"), crop_y=unit("crop_y"),
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
        try:
            min_margin = float(raw.get("min_margin", capabilities.DEFAULT_MIN_MARGIN_MM))
            result = compute(raw, 0.0 if raw.get("borderless") else min_margin).to_dict()
        except (BadRequest, TypeError, ValueError) as e:
            return fail(e if isinstance(e, BadRequest) else BadRequest("Those numbers weren't understood."))
        img = raw.get("image")
        if isinstance(img, dict) and result["cells"]:  # how sharp the picture will be at this size
            try:
                info = pdfsheet.Jpeg(int(img["width"]), int(img["height"]), 3)
                c = result["cells"][0]
                result["dpi"] = round(pdfsheet.effective_dpi(info, c["w"], c["h"], "fit" if raw.get("fit") == "fit" else "fill"))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pass
        return jsonify(ok=True, data=result)

    @bp.post("/pdf")
    def post_pdf():
        try:
            data, info = read_image()
            raw = read_json_field("settings")
            result = compute(raw, 0.0)  # a downloaded sheet is never "too close to the edge" for a printer we don't know
            body = pdf_for(result, data, info, raw, for_media=False)
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
            data, info = read_image()
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
            media = next((m for m in caps["media"] if m["keyword"] == opts.get("media")), None)
            if media is None:
                raise BadRequest("Choose one of the paper sizes this printer offers.")
            job: list[tuple[int, str, object]] = [(ipp.KEYWORD, "media", media["keyword"]), (ipp.KEYWORD, "print-scaling", "none")]
            mt = opts.get("media_type")
            if mt:
                if mt not in {t["value"] for t in caps["media_types"]}:
                    raise BadRequest("Choose one of the paper types this printer offers.")
                job.append((ipp.KEYWORD, "media-type", mt))
            q = opts.get("quality")
            if q is not None:
                if q not in {x["value"] for x in caps["qualities"]}:
                    raise BadRequest("Choose one of the print qualities this printer offers.")
                job.append((ipp.ENUM, "print-quality", int(q)))
            res = opts.get("resolution")
            if res:
                if res not in [r["value"] for r in caps["resolutions"]]:
                    raise BadRequest("Choose one of the resolutions this printer offers.")
                job.append((ipp.RESOLUTION, "printer-resolution", (int(res[0]), int(res[1]), 3)))
            copies = opts.get("copies", 1)
            if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= 20:
                raise BadRequest("Copies must be a whole number from 1 to 20.")
            if copies > 1:
                job.append((ipp.INTEGER, "copies", copies))
            result = compute(raw, 0.0 if media["borderless"] else caps["min_margin"], (media["width"], media["height"]))
            now = clock()
            times = [r["ts"] for r in db.query("SELECT ts FROM ps_prints WHERE printer_id=? AND ts>=?", (printer.id, now - rule.window))]
            verdict = ratelimit.check(rule, times, now)
            if not verdict.allowed:
                resp = jsonify(ok=False, error={"source": "rate-limit", "message": verdict.reason, "retry_after": verdict.retry_after})
                resp.headers["Retry-After"] = str(verdict.retry_after)
                return resp, 429
            pdf = pdf_for(result, data, info, raw, for_media=True)  # rotated onto the page the way the paper is fed
            try:
                job_id = backend.submit_document(JOB_NAME, pdf, APP_USER, "application/pdf", options=job)
            except SourceError as e:
                return jsonify(ok=False, error=e.to_dict()), 502
            db.execute("INSERT INTO ps_prints(printer_id, ts) VALUES(?,?)", (printer.id, now))
        except BadRequest as e:
            return fail(e)
        return jsonify(ok=True, data={"job_id": job_id, "count": result.count, "warnings": result.warnings}), 202

    return Tool(
        id="photosheet", title="Photo Sheets", description="Lay out passport and ID photos at an exact size on a sheet of paper",
        icon="image", blueprint=bp, ui_dir=HERE / "ui", order=15, needs_printer=False)
