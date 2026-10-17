"""Application factory: wires settings, storage, printer backend, security and tools."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from flask import Flask, abort, jsonify, make_response, request, send_from_directory

from app import __version__
from app.core import csrf as csrf_mod
from app.core.context import AppContext
from app.core.db import Database
from app.core.links import LinksConfig
from app.core.tool import discover
from app.printer import PrinterBackend, make_backend
from app.settings import Settings

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CSP = ("default-src 'self'; img-src 'self' data: http: https:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


def create_app(settings: Settings | None = None, printer: PrinterBackend | None = None,
               start_background: bool = True, clock: Callable[[], float] = time.time,
               only_tools: set[str] | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.json.sort_keys = False  # type: ignore[attr-defined]
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    db = Database(settings.db_path)
    ctx = AppContext(settings, db, printer or make_backend(settings), clock)
    csrf = csrf_mod.Csrf(csrf_mod.load_secret(settings.secret_key, settings.data_dir), settings.trusted_origins)
    links = LinksConfig(settings.links_file, settings.logos_dir, ROOT / "defaults" / "links.yml")
    slots = discover(ctx, only_tools)
    app.extensions["panda"] = {"ctx": ctx, "slots": slots, "links": links, "csrf": csrf}

    # ---------------------------------------------------------------- tools
    for slot in slots:
        t = slot.tool
        if not t:
            continue
        if t.blueprint:
            app.register_blueprint(t.blueprint, url_prefix=f"/api/{t.id}")
        if t.start and start_background:
            t.start()

    # ---------------------------------------------------------------- security
    @app.before_request
    def _csrf_guard():
        if request.path.startswith("/api/"):
            hosts = [request.host, request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()]
            problem = csrf.check(request.method, request.headers, request.cookies, hosts)
            if problem:
                return jsonify(ok=False, error={"source": "csrf", "message": problem}), 403

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        elif request.path.startswith(("/static/", "/tools/")) or request.path == "/":
            resp.headers["Cache-Control"] = "no-cache"  # revalidate (ETag), so updates show at once
        return resp

    # ---------------------------------------------------------------- core routes
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, version=__version__, mock=settings.mock)

    @app.get("/api/csrf")
    def csrf_token():
        token = csrf.issue()
        resp = make_response(jsonify(token=token))
        secure = request.is_secure or request.headers.get("X-Forwarded-Proto", "").split(",")[0] == "https"
        resp.set_cookie(csrf_mod.COOKIE, token, httponly=True, samesite="Strict", secure=secure, path="/")
        return resp

    @app.get("/api/tools")
    def tools():
        out = []
        for s in slots:
            if s.tool:
                t = s.tool
                css = t.ui_dir and (t.ui_dir / "ui.css").is_file()
                out.append({"id": t.id, "title": t.title, "description": t.description, "icon": t.icon,
                            "entry": f"/tools/{t.id}/ui.js", "css": f"/tools/{t.id}/ui.css" if css else None})
            else:
                out.append({"id": s.id, "title": s.title, "error": s.error})
        return jsonify(tools=out, mock=settings.mock, version=__version__)

    @app.get("/api/links")
    def get_links():
        return jsonify(links.get())

    @app.get("/logos/<path:name>")
    def logo(name: str):
        resp = send_from_directory(settings.logos_dir.resolve(), name, max_age=3600)
        # an SVG opened directly must not be able to run script
        resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        return resp

    @app.get("/tools/<tool_id>/<path:name>")
    def tool_asset(tool_id: str, name: str):
        slot = next((s for s in slots if s.id == tool_id and s.tool and s.tool.ui_dir), None)
        if not slot:
            abort(404)
        return send_from_directory(slot.tool.ui_dir, name)  # type: ignore[union-attr]

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error={"message": "Not found."}), 404
        return "Not found", 404

    @app.errorhandler(Exception)
    def crash(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error={"message": e.description}), e.code
            return e
        log.exception("unhandled error on %s", request.path)
        return jsonify(ok=False, error={"message": "Something went wrong on the server. See the container log."}), 500

    return app
