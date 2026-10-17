"""HTTP API of the maintenance tool, mounted at /api/maintenance/."""
from __future__ import annotations

from typing import Callable

from flask import Blueprint, jsonify

from app.core.errors import SourceError
from app.tools.maintenance.service import RateLimited, Service


def _envelope(fn: Callable[[], dict], clock) -> tuple:
    """Run a card's loader. Upstream trouble becomes {ok: false, error}, so one
    unreachable source never breaks the other cards."""
    try:
        return jsonify(ok=True, data=fn(), fetched_at=clock()), 200
    except SourceError as e:
        return jsonify(ok=False, error=e.to_dict(), fetched_at=clock()), 200


def build_blueprint(svc: Service) -> Blueprint:
    bp = Blueprint("maintenance", __name__)

    @bp.get("/printer")
    def printer():
        return _envelope(svc.printer_status, svc.clock)

    @bp.get("/queue")
    def queue():
        return _envelope(svc.queue_status, svc.clock)

    @bp.get("/nozzle")
    def nozzle():
        return _envelope(svc.nozzle_status, svc.clock)

    @bp.post("/nozzle/print")
    def nozzle_print():
        try:
            return jsonify(ok=True, data=svc.request_print()), 202
        except RateLimited as e:
            v = e.verdict
            resp = jsonify(ok=False, error={"source": "rate-limit", "message": v.reason,
                                            "retry_after": v.retry_after})
            resp.headers["Retry-After"] = str(v.retry_after)
            return resp, 429
        except SourceError as e:
            return jsonify(ok=False, error=e.to_dict()), 502

    return bp
