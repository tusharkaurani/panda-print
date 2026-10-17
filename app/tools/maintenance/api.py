"""HTTP API of the maintenance tool, mounted at /api/maintenance/<printer id>/..."""
from __future__ import annotations

from typing import Callable

from flask import Blueprint, jsonify

from app.core.errors import SourceError
from app.tools.maintenance.runtimes import Runtime, Runtimes
from app.tools.maintenance.service import RateLimited


def build_blueprint(runtimes: Runtimes, clock: Callable[[], float]) -> Blueprint:
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

    @bp.post("/<pid>/nozzle/print")
    def nozzle_print(pid):
        rt = runtimes.get(pid)
        if rt is None:
            return jsonify(ok=False, error={"source": "printers", "message": "That printer isn't set up (any more)."}), 404
        try:
            return jsonify(ok=True, data=rt.service.request_print()), 202
        except RateLimited as e:
            v = e.verdict
            resp = jsonify(ok=False, error={"source": "rate-limit", "message": v.reason,
                                            "retry_after": v.retry_after})
            resp.headers["Retry-After"] = str(v.retry_after)
            return resp, 429
        except SourceError as e:
            return jsonify(ok=False, error=e.to_dict()), 502

    return bp
