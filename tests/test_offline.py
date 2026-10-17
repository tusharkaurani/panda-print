"""A printer that is switched off or off the network: the dashboard says so plainly and keeps the last known facts
on screen, nothing breaks, nothing prints into the void, and it all comes back by itself."""
import pytest

from app.tools.maintenance.runtimes import Runtimes
from app.tools.maintenance.store import Store
from conftest import PID, csrf_headers
from fakes import seed_history


@pytest.fixture
def rig(make_app, clock, monkeypatch):
    monkeypatch.setattr("app.tools.maintenance.service.LIVE_CACHE_SECONDS", 0)  # no 15 s cache: the next read sees the change
    app, backend = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    client = app.test_client()
    ctx = app.extensions["panda"]["ctx"]
    svc = Runtimes(ctx).get(PID).service

    def card(pid=PID, what="printer"):
        r = client.get(f"/api/maintenance/{pid}/{what}")
        assert r.status_code == 200
        return r.get_json()

    def power(off, pid=PID):
        b = ctx.backend_for(ctx.printers.get(pid))
        b.fail = ("printer", "snmp") if off else ()

    return type("Rig", (), dict(app=app, client=client, ctx=ctx, svc=svc, backend=backend, card=staticmethod(card),
                                power=staticmethod(power), clock=clock, h=csrf_headers(client)))


def test_a_printer_never_seen_answering_shows_the_reason(make_app):
    app, _ = make_app(fail=("printer", "snmp"))
    r = app.test_client().get(f"/api/maintenance/{PID}/printer").get_json()
    assert r["ok"] is False and "did not answer" in r["error"]["message"]  # nothing to remember, so an error card


def test_a_printer_that_goes_off_keeps_its_last_known_facts(rig):
    good = rig.card()["data"]
    seen_at = rig.clock()
    assert good["state"]["label"] == "Idle" and "offline" not in good
    rig.clock.advance(3600)
    rig.power(off=True)
    r = rig.card()
    d = r["data"]
    assert r["ok"] is True and d["state"]["label"] == "Offline" and d["state"]["health"] == "bad"
    assert d["offline"]["as_of"] == pytest.approx(seen_at) and "did not answer" in d["offline"]["error"]["message"]
    assert [i["percent"] for i in d["inks"]] == [i["percent"] for i in good["inks"]] and d["details"] == good["details"]
    assert d["counter"]["value"] == 174  # the page count is the last known too


def test_it_comes_back_by_itself(rig):
    rig.card()
    rig.power(off=True)
    assert rig.card()["data"]["state"]["label"] == "Offline"
    rig.power(off=False)
    back = rig.card()["data"]
    assert back["state"]["label"] == "Idle" and "offline" not in back


def test_the_last_known_facts_survive_a_restart(rig):
    rig.card()
    rig.power(off=True)
    fresh = Runtimes(rig.ctx).get(PID).service  # a new process: nothing in memory
    assert fresh.printer_status()["state"]["label"] == "Offline"


def test_one_printer_off_does_not_blank_another(rig):
    other = rig.client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=rig.h).get_json()["data"]["id"]
    rig.card(other)
    rig.card()
    rig.power(off=True)
    assert rig.card()["data"]["state"]["label"] == "Offline"
    assert rig.card(other)["data"]["state"]["label"] == "Idle"


def test_a_removed_printers_remembered_status_goes_with_it(rig):
    rig.card()
    assert Store(rig.ctx.db, PID).get("last_status")
    rig.client.delete(f"/api/printers/{PID}", headers=rig.h)
    assert Store(rig.ctx.db, PID).get("last_status") is None


def test_a_damaged_remembered_status_is_ignored_not_a_crash(rig):
    Store(rig.ctx.db, PID).put("last_status", "{not json")
    rig.power(off=True)
    r = rig.card()
    assert r["ok"] is False and r["error"]["message"]  # no memory to use, so the plain error


def test_the_automatic_print_waits_for_the_printer_and_then_goes_ahead(rig):
    rig.client.put(f"/api/maintenance/{PID}/automation", json={"enabled": True, "idle_days": 0.5, "run_at": "00:00"}, headers=rig.h)
    rig.power(off=True)
    rig.clock.advance(3600)
    rig.svc.tracker.poll()
    plan = rig.svc.run_schedule()
    assert plan.action == "blocked" and "page count" in plan.reason
    assert not [d for d in rig.backend.submitted]
    rig.power(off=False)
    rig.clock.advance(60)
    rig.svc.tracker.poll()
    assert rig.svc.run_schedule().action == "print" and len(rig.backend.submitted) == 1


def test_print_now_still_queues_the_page_while_the_printer_is_off(rig):
    """CUPS holds the job, so it prints when the printer returns. The UI warns first (see the Print now dialog)."""
    rig.power(off=True)
    r = rig.client.post(f"/api/maintenance/{PID}/nozzle/print", json={}, headers=rig.h)
    assert r.status_code == 202 and len(rig.backend.submitted) == 1


def test_the_page_count_error_is_reported_without_breaking_the_nozzle_card(rig):
    rig.power(off=True)
    rig.svc.tracker.poll()
    n = rig.card(what="nozzle")
    assert n["ok"] is True and "did not answer" in n["data"]["tracking"]["counter_error"]
    assert n["data"]["last_print"] is not None  # last known from before it went off
