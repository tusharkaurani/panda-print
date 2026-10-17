"""HTTP API for managing printers: /api/printers/..."""
from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

from app.core.context import AppContext
from app.core.printers import ValidationError, clean_url

log = logging.getLogger(__name__)


def build_blueprint(ctx: AppContext) -> Blueprint:
    bp = Blueprint("printers", __name__)
    registry, access, settings = ctx.printers, ctx.access, ctx.settings

    def body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def not_found():
        return jsonify(ok=False, error={"message": "That printer isn't set up (any more)."}), 404

    def invalid(e: ValidationError):
        return jsonify(ok=False, error={"message": "Please check the highlighted fields.", "fields": e.errors}), 400

    def defaults() -> dict:
        """What the add/edit forms are prefilled with."""
        state = str(settings.host_state_file)
        used = any(p.host_state_file == state for p in registry.list())
        return {
            "cups_url": settings.cups_url, "cups_queue": settings.cups_queue,
            "snmp_community": settings.snmp_community, "snmp_port": settings.snmp_port,
            "counter_oid": settings.snmp_counter_oid,
            # the host timer's state file belongs to one printer: offer it only if it exists and is unclaimed
            "host_state_file": state if os.path.isfile(state) and not used else "",
        }

    @bp.get("")
    def list_printers():
        return jsonify(ok=True, data={"printers": [p.to_dict() for p in registry.list()], "defaults": defaults()})

    @bp.post("")
    def add():
        try:
            printer = registry.add(body())
        except ValidationError as e:
            return invalid(e)
        log.info("printer added: %s (%s)", printer.name, printer.ip)
        return jsonify(ok=True, data=printer.to_dict()), 201

    @bp.put("/<pid>")
    def update(pid):
        try:
            printer = registry.update(pid, body())
        except KeyError:
            return not_found()
        except ValidationError as e:
            return invalid(e)
        log.info("printer updated: %s", printer.name)
        return jsonify(ok=True, data=printer.to_dict())

    @bp.delete("/<pid>")
    def delete(pid):
        if not registry.delete(pid):
            return not_found()
        log.info("printer removed: %s", pid)
        return jsonify(ok=True)

    @bp.post("/discover")
    def discover():
        cups_url = clean_url(str(body().get("cups_url") or settings.cups_url), ("http", "https")) or settings.cups_url
        result = access.discover(cups_url)
        by_ip = {p.ip: p.id for p in registry.list()}
        for c in result["candidates"]:
            c["already_added"] = by_ip.get(c["ip"])
        return jsonify(ok=True, data=result)

    @bp.post("/probe")
    def probe():
        return jsonify(ok=True, data=access.probe(body()))

    @bp.post("/<pid>/test")
    def test_saved(pid):
        printer = registry.get(pid)
        if printer is None:
            return not_found()
        return jsonify(ok=True, data=access.probe(printer.to_dict()))

    return bp
