"""Application factory: wires settings, storage, printer backend, security and tools."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import urlsplit
from typing import Callable

from flask import Flask, abort, jsonify, make_response, request, send_from_directory

from app import __version__
from app.core import csrf as csrf_mod
from app.core.context import AppContext
from app.core.db import Database
from app.core.links import LinksConfig
from app.core.printers import PrinterRegistry, ValidationError
from app.core.printers_api import build_blueprint as printers_blueprint
from app.core.tool import discover
from app.printer import PrinterAccess, make_access
from app.settings import Settings

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CSP = ("default-src 'self'; img-src 'self' data: http: https:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


def seed_printers(settings: Settings, registry: PrinterRegistry) -> None:
    """Unattended installs: PRINTER_IP in the environment adds the first printer."""
    if len(registry) or not settings.printer_ip:
        return
    try:
        printer = registry.add({
            "name": settings.printer_ip, "ip": settings.printer_ip, "ipp_url": settings.printer_ipp_url,
            "snmp_community": settings.snmp_community, "snmp_port": settings.snmp_port,
            "counter_oid": settings.snmp_counter_oid, "cups_url": settings.cups_url,
            "cups_queue": settings.cups_queue,
            "host_state_file": str(settings.host_state_file) if settings.host_state_file.exists() else ""})
        log.info("added printer %s from PRINTER_IP", printer.ip)
    except ValidationError as e:
        log.error("PRINTER_IP settings are invalid, so no printer was added: %s", e)


def create_app(settings: Settings | None = None, access: PrinterAccess | None = None,
               start_background: bool = True, clock: Callable[[], float] = time.time,
               only_tools: set[str] | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.json.sort_keys = False  # type: ignore[attr-defined]
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    db = Database(settings.db_path)
    registry = PrinterRegistry(db, clock)
    ctx = AppContext(settings, db, registry, access or make_access(settings), clock)
    seed_printers(settings, registry)
    csrf = csrf_mod.Csrf(csrf_mod.load_secret(settings.secret_key, settings.data_dir), settings.trusted_origins)
    links = LinksConfig(settings.links_file, settings.logos_dir, ROOT / "defaults" / "links.yml")
    slots = discover(ctx, only_tools)
    build = str(int(time.time()))  # changes whenever the server is (re)started: open pages use it to spot an update
    app.extensions["panda"] = {"ctx": ctx, "slots": slots, "links": links, "csrf": csrf}
    app.register_blueprint(printers_blueprint(ctx), url_prefix="/api/printers")

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
        return jsonify(ok=True, version=__version__, build=build)

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
                            "entry": f"/tools/{t.id}/ui.js", "css": f"/tools/{t.id}/ui.css" if css else None,
                            "needs_printer": t.needs_printer,
                            "tabs": [{"id": tab.id, "title": tab.title, "icon": tab.icon or t.icon, "description": tab.description or t.description} for tab in t.tabs]})
            else:
                out.append({"id": s.id, "title": s.title, "error": s.error})
        return jsonify(tools=out, version=__version__, build=build)

    @app.get("/api/links")
    def get_links():
        """Launcher tiles. With ?printer=<id>, entries using {ip}, {queue}... are filled in for that printer."""
        host = (request.headers.get("X-Forwarded-Host", "").split(",")[0].strip() or request.host)
        hostname = urlsplit(f"//{host}").hostname or ""
        return jsonify(links.render(registry.get(request.args.get("printer", "")), hostname))

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
