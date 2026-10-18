"""Tool: Label Sheet Maker. Put names, addresses or any text onto the labels of a sticker sheet (Avery-style packs or your own
grid) from a typed list or a spreadsheet, download the PDF, or print it at actual size through the printer's CUPS queue."""
from __future__ import annotations

from pathlib import Path

import yaml
from flask import Blueprint, Response, jsonify, request

from app.core import ratelimit
from app.core.context import AppContext
from app.core.errors import SourceError
from app.core.tool import Tool
from app.tools.labels import fonts, pdf, sheet as sh
from app.tools.photosheet import capabilities

HERE = Path(__file__).parent
JOB_NAME = "panda-print labels"
APP_USER = "panda-print"
MIGRATIONS = ["CREATE TABLE IF NOT EXISTS lb_prints (printer_id TEXT NOT NULL, ts REAL NOT NULL);"
              "CREATE INDEX IF NOT EXISTS lb_prints_printer ON lb_prints(printer_id, ts);"]
TEMPLATE_KEYS = ("id", "name", "paper", "cols", "rows", "width", "height", "left", "top", "gap_x", "gap_y", "radius")


class BadRequest(Exception):
    def __init__(self, message: str, status: int = 400, hint: str | None = None):
        super().__init__(message)
        self.message, self.status, self.hint = message, status, hint


def load_templates(path: Path) -> list[dict]:
    """The label sheets shipped with the app. A bad entry is skipped, never fatal."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    out, seen = [], set()
    for entry in (doc.get("templates") if isinstance(doc, dict) else None) or []:
        try:
            paper = sh.PAPERS[str(entry["paper"]).lower()]
            sh.make_sheet(paper[0], paper[1], entry["cols"], entry["rows"], entry["width"], entry["height"], entry["left"],
                          entry["top"], entry.get("gap_x", 0), entry.get("gap_y", 0), entry.get("radius", 0))
            tid = str(entry["id"])
            if tid in seen:
                continue
            seen.add(tid)
            out.append({**{k: entry.get(k, 0) for k in TEMPLATE_KEYS}, "id": tid, "name": str(entry["name"]), "paper": str(entry["paper"]).lower(),
                        "paper_width": paper[0], "paper_height": paper[1]})
        except (KeyError, TypeError, sh.SheetError):
            continue
    return out


def create(ctx: AppContext) -> Tool:
    ctx.db.migrate("labels", MIGRATIONS)
    db, settings, clock = ctx.db, ctx.settings, ctx.clock
    wipe = lambda pid: db.execute("DELETE FROM lb_prints WHERE printer_id=?", (pid,))
    ctx.printers.on_delete(wipe)
    ctx.printers.on_add(wipe)
    rule = ratelimit.RateRule(settings.print_cooldown, settings.print_max_per_hour, 3600.0)
    bp = Blueprint("labels", __name__)

    def fail(e: BadRequest):
        err = {"message": e.message}
        if e.hint:
            err["hint"] = e.hint
        return jsonify(ok=False, error=err), e.status

    # ------------------------------------------------------------------ reading the request
    def num(d: dict, key: str, label: str, default=None):
        v = d.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise BadRequest(f"{label} must be a number.")
        return v

    def whole(d: dict, key: str, label: str, default=None) -> int:
        v = d.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise BadRequest(f"{label} must be a whole number.")
        return v

    def parse(raw) -> tuple[sh.Result, dict]:
        """Everything on the form -> the laid-out sheets. Raises BadRequest with a message for the person."""
        if not isinstance(raw, dict):
            raise BadRequest("The label settings are missing.")
        s = raw.get("sheet") if isinstance(raw.get("sheet"), dict) else {}
        paper = s.get("paper") if isinstance(s.get("paper"), dict) else {}
        c = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        st = raw.get("style") if isinstance(raw.get("style"), dict) else {}
        try:
            sheet = sh.make_sheet(num(paper, "width", "The paper width"), num(paper, "height", "The paper height"), whole(s, "cols", "The columns"),
                                  whole(s, "rows", "The rows"), num(s, "width", "The label width"), num(s, "height", "The label height"),
                                  num(s, "left", "The left margin", 0), num(s, "top", "The top margin", 0), num(s, "gap_x", "The gap across", 0),
                                  num(s, "gap_y", "The gap down", 0), num(s, "radius", "The corner radius", 0))
            mode = c.get("mode", "text")
            text = c.get("text", "")
            if not isinstance(text, str) or not isinstance(c.get("csv", ""), str) or not isinstance(c.get("template", ""), str):
                raise BadRequest("The label text wasn't understood.")
            columns: list[str] = []
            if mode == "csv":
                labels, columns = sh.labels_from_csv(c.get("csv", ""), c.get("template", ""))
            elif mode == "text":
                labels = sh.labels_from_text(text, bool(c.get("per_line", False)))
            else:
                raise BadRequest("Choose typed text or a spreadsheet.")
            size = st.get("size")
            if size is not None and (isinstance(size, bool) or not isinstance(size, (int, float))):
                raise BadRequest("The text size must be a number, or automatic.")
            style = sh.Style(size=float(size) if size is not None else None, align=str(st.get("align", "left")), valign=str(st.get("valign", "middle")),
                             bold=bool(st.get("bold", False)), first_bold=bool(st.get("first_bold", False)), pad=float(num(st, "pad", "The space inside", 2.0)))
            result = sh.compute(sheet, labels, style, copies=whole(raw, "copies", "Copies of each label", 1), fill=bool(raw.get("fill", False)),
                                start=whole(raw, "start", "The first place", 1))
        except sh.SheetError as e:
            raise BadRequest(str(e)) from None
        return result, {"columns": columns}

    def edge_warnings(result: sh.Result, min_margin: float) -> list[str]:
        """Text the printer can't reach: closer to a paper edge than its unprintable margin."""
        sheet, size = result.sheet, result.size
        for page in result.pages:
            for cell in page:
                for ln in cell["lines"]:
                    w = fonts.text_width(ln["t"], size, ln["bold"]) * sh.MM_PER_PT
                    if ln["t"] and (ln["x"] < min_margin - 0.01 or ln["x"] + w > sheet.paper_w - min_margin + 0.01
                                    or ln["y"] - size * sh.MM_PER_PT < min_margin - 0.01 or ln["y"] > sheet.paper_h - min_margin + 0.01):
                        return [f"Some text is closer to the paper's edge than this printer can print ({min_margin:g} mm), so it may be cut off. "
                                "Move the text in from the label's edge (Space inside labels) or choose a printer that prints closer to the edge."]
        return []

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
        out = {"templates": load_templates(HERE / "templates.yml"), "papers": capabilities.fallback()["media"], "printer": None,
               "limits": {"max_labels": sh.MAX_LABELS, "max_sheets": sh.MAX_SHEETS, "sizes": [sh.MIN_SIZE, 72]},
               "fallback_min_margin": capabilities.DEFAULT_MIN_MARGIN_MM}
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
        try:
            result, extra = parse(raw)
            page = whole(raw, "page", "The page", 1)
        except BadRequest as e:
            return fail(e)
        data = result.to_dict(page)
        mm = raw.get("min_margin")
        if isinstance(mm, (int, float)) and not isinstance(mm, bool) and mm > 0 and not raw.get("borderless"):
            data["warnings"] = [*data["warnings"], *edge_warnings(result, float(mm))]
        data.update(extra)
        return jsonify(ok=True, data=data)

    @bp.post("/pdf")
    def post_pdf():
        raw = request.get_json(silent=True)
        try:
            result, _extra = parse(raw)
        except BadRequest as e:
            return fail(e)
        body = pdf.build(result, outline=bool(raw.get("outline", False)), title="Labels")
        resp = Response(body, mimetype="application/pdf")
        disp = "attachment" if request.args.get("download") == "1" else "inline"
        resp.headers["Content-Disposition"] = f'{disp}; filename="labels.pdf"'
        resp.headers["Content-Security-Policy"] = "sandbox"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @bp.post("/print")
    def post_print():
        raw = request.get_json(silent=True)
        try:
            printer = printer_or_404(raw.get("printer", "") if isinstance(raw, dict) else "")
            if not printer.cups_queue:
                raise BadRequest("This printer has no CUPS print queue, so it can't print from here.",
                                 400, "Download the PDF instead, or set up CUPS and choose the queue under Printers → Edit.")
            result, _extra = parse(raw)
            opts = raw.get("options") if isinstance(raw.get("options"), dict) else {}
            backend = ctx.backend_for(printer)
            try:
                caps = capabilities.from_attrs(backend.print_options())
            except SourceError as e:
                return jsonify(ok=False, error=e.to_dict()), 502
            if not caps["actual_size"]:
                raise BadRequest("This print queue can't print at actual size, so the labels wouldn't line up with the sheet.", 400)
            if not caps["pdf"]:
                raise BadRequest("This print queue doesn't accept PDF files.", 400)
            try:
                media, job, _copies = capabilities.job_options(caps, opts)
            except capabilities.OptionError as e:
                raise BadRequest(str(e)) from None
            if abs(min(media["width"], media["height"]) - result.sheet.paper_w) > 1.0 or abs(max(media["width"], media["height"]) - result.sheet.paper_h) > 1.0:
                raise BadRequest(f"These labels are laid out for {result.sheet.paper_w:g} × {result.sheet.paper_h:g} mm paper, but you chose "
                                 f"{media['label']}. Pick the paper the label sheet is on.", 400)
            now = clock()
            times = [r["ts"] for r in db.query("SELECT ts FROM lb_prints WHERE printer_id=? AND ts>=?", (printer.id, now - rule.window))]
            verdict = ratelimit.check(rule, times, now)
            if not verdict.allowed:
                resp = jsonify(ok=False, error={"source": "rate-limit", "message": verdict.reason, "retry_after": verdict.retry_after})
                resp.headers["Retry-After"] = str(verdict.retry_after)
                return resp, 429
            body = pdf.build(result, outline=bool(raw.get("outline", False)), title="Labels")
            try:
                job_id = backend.submit_document(JOB_NAME, body, APP_USER, "application/pdf", options=job)
            except SourceError as e:
                return jsonify(ok=False, error=e.to_dict()), 502
            db.execute("INSERT INTO lb_prints(printer_id, ts) VALUES(?,?)", (printer.id, now))
        except BadRequest as e:
            return fail(e)
        return jsonify(ok=True, data={"job_id": job_id, "labels": result.labels, "sheets": len(result.pages), "warnings": result.warnings}), 202

    return Tool(
        id="labels", title="Label Sheet Maker", description="Put names, addresses or any text onto sticker label sheets from a list or spreadsheet, then download or print it",
        icon="list", blueprint=bp, ui_dir=HERE / "ui", order=16, needs_printer=False, parent="utilities")
