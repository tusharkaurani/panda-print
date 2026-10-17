"""HTTP API of the maintenance tool, mounted at /api/maintenance/<printer id>/..."""
from __future__ import annotations

from typing import Callable

from flask import Blueprint, jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from app.core.errors import SourceError
from app.tools.maintenance.automation import ValidationError
from app.tools.maintenance.pages import Library, PageError
from app.tools.maintenance.runtimes import Runtime, Runtimes
from app.tools.maintenance.service import RateLimited


def build_blueprint(runtimes: Runtimes, clock: Callable[[], float], library: Library) -> Blueprint:
    bp = Blueprint("maintenance", __name__)

    def card(pid: str, load: Callable[[Runtime], dict]):
        """Run one card's loader. Upstream trouble becomes {ok: false, error}, so one
        unreachable source never breaks the other cards."""
        rt = runtimes.get(pid)
        if rt is None:
            return jsonify(ok=False, error={"source": "printers", "message": "That printer isn't set up (any more)."}), 404
        try:
            return jsonify(ok=True, data=load(rt), fetched_at=clock()), 200
        except SourceError as e:
            return jsonify(ok=False, error=e.to_dict(), fetched_at=clock()), 200

    @bp.get("/<pid>/printer")
    def printer(pid):
        return card(pid, lambda rt: rt.service.printer_status())

    @bp.get("/<pid>/queue")
    def queue(pid):
        return card(pid, lambda rt: rt.service.queue_status())

    @bp.get("/<pid>/nozzle")
    def nozzle(pid):
        return card(pid, lambda rt: rt.service.nozzle_status())

    # Automation settings for this printer (CSRF-protected like every change)
    def settings_call(pid, fn):
        rt = runtimes.get(pid)
        if rt is None:
            return jsonify(ok=False, error={"source": "printers", "message": "That printer isn't set up (any more)."}), 404
        try:
            return jsonify(ok=True, data=fn(rt))
        except ValidationError as e:
            return jsonify(ok=False, error={"message": "Please check the highlighted fields.", "fields": e.errors}), 400
        except PageError as e:
            return page_error(e)

    @bp.get("/<pid>/automation")
    def automation_get(pid):
        return settings_call(pid, lambda rt: rt.service.automation_status())

    @bp.put("/<pid>/automation")
    def automation_put(pid):
        body = request.get_json(silent=True)
        return settings_call(pid, lambda rt: rt.service.save_automation(body if isinstance(body, dict) else {}))

    @bp.delete("/<pid>/automation")
    def automation_reset(pid):
        return settings_call(pid, lambda rt: rt.service.reset_automation())

    # ---- the shared library of pages
    def page_error(e: PageError):
        return jsonify(ok=False, error={"source": "pages", "message": str(e)}), e.status

    @bp.get("/pages")
    def pages_list():
        return jsonify(ok=True, data={"pages": library.list(), "limits": library.limits()})

    @bp.post("/pages")
    def pages_add():
        request.max_content_length = library.max_file + 64 * 1024  # uploads get their own, larger, size limit
        try:
            upload = request.files.get("file")
        except RequestEntityTooLarge:
            return page_error(PageError(f"That file is larger than {library.max_file // 2**20} MB.", 413))
        if upload is None:
            return page_error(PageError("Choose a file to add."))
        try:
            page = library.add(upload.read(), upload.filename or "")
        except PageError as e:
            return page_error(e)
        return jsonify(ok=True, data=page), 201

    @bp.put("/pages/<page_id>")
    def pages_rename(page_id):
        body = request.get_json(silent=True)
        try:
            return jsonify(ok=True, data=library.rename(page_id, str((body or {}).get("name", ""))))
        except PageError as e:
            return page_error(e)

    @bp.delete("/pages/<page_id>")
    def pages_delete(page_id):
        if not library.delete(page_id):
            return page_error(PageError("That page doesn't exist (any more).", 404))
        return jsonify(ok=True)

    @bp.get("/pages/<page_id>/file")
    def pages_file(page_id):
        page = library.get(page_id)
        if page is None or not library.path(page).is_file():
            return page_error(PageError("That page doesn't exist (any more).", 404))
        resp = send_file(library.path(page), mimetype=page["mime"], conditional=True, max_age=86400)
        resp.headers["Content-Security-Policy"] = "sandbox"  # an uploaded file must never run anything
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # ---- this printer's rotation
    @bp.get("/<pid>/rotation")
    def rotation_get(pid):
        return settings_call(pid, lambda rt: rt.service.rotation_status())

    @bp.put("/<pid>/rotation")
    def rotation_put(pid):
        body = request.get_json(silent=True)
        try:
            return settings_call(pid, lambda rt: rt.service.save_rotation((body or {}).get("items")))
        except PageError as e:
            return page_error(e)

    @bp.post("/<pid>/nozzle/print")
    def nozzle_print(pid):
        rt = runtimes.get(pid)
        if rt is None:
            return jsonify(ok=False, error={"source": "printers", "message": "That printer isn't set up (any more)."}), 404
        body = request.get_json(silent=True)
        page = body.get("page") if isinstance(body, dict) else None  # optional: print this page instead of the next one
        try:
            return jsonify(ok=True, data=rt.service.request_print(page if isinstance(page, str) else None)), 202
        except PageError as e:
            return jsonify(ok=False, error={"source": "pages", "message": str(e)}), e.status
        except RateLimited as e:
            v = e.verdict
            resp = jsonify(ok=False, error={"source": "rate-limit", "message": v.reason,
                                            "retry_after": v.retry_after})
            resp.headers["Retry-After"] = str(v.retry_after)
            return resp, 429
        except SourceError as e:
            return jsonify(ok=False, error=e.to_dict()), 502

    return bp
