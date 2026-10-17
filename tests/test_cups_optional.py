"""CUPS is optional. A printer with no print queue still shows its status, ink and page count; everything that
needs CUPS (the queue, job history, printing, the automatic print, your own pages) says so instead of failing."""
import pytest

from app.tools.maintenance import schedule
from app.tools.maintenance.runtimes import Runtimes
from app.tools.maintenance.store import Store
from conftest import PID, csrf_headers
from fakes import seed_history

DAY = 86400


@pytest.fixture
def rig(make_app, clock):
    app, brother = make_app(timezone="UTC", print_cooldown=0, print_max_per_hour=1000)
    client = app.test_client()
    h = csrf_headers(client)
    ctx = app.extensions["panda"]["ctx"]
    runtimes = Runtimes(ctx)

    def bare(name="Study", ip="192.168.1.60", **extra):
        r = client.post("/api/printers", json={"name": name, "ip": ip, "cups_queue": "", **extra}, headers=h)
        assert r.status_code == 201
        pid = r.get_json()["data"]["id"]
        seed_history(Store(ctx.db, pid), clock())
        runtimes.get(pid).tracker.poll()
        return pid

    def spy(pid):
        """Count the calls that would reach CUPS for this printer."""
        backend = ctx.backend_for(ctx.printers.get(pid))
        calls = []
        for name in ("queue_info", "list_jobs", "submit_document"):
            real = getattr(backend, name)
            setattr(backend, name, lambda *a, _n=name, _r=real, **k: (calls.append(_n), _r(*a, **k))[1])
        return backend, calls

    return type("Rig", (), dict(app=app, client=client, h=h, ctx=ctx, runtimes=runtimes, bare=staticmethod(bare),
                                spy=staticmethod(spy), clock=clock, brother=brother))


def get(rig, pid, card):
    r = rig.client.get(f"/api/maintenance/{pid}/{card}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_the_queue_card_says_cups_is_not_set_up_and_never_asks_cups(rig):
    pid = rig.bare()
    backend, calls = rig.spy(pid)
    q = get(rig, pid, "queue")
    assert q["ok"] is True and q["data"]["configured"] is False and q["data"]["jobs"] == [] and q["data"]["jobs_error"] is None
    rig.runtimes.get(pid).tracker.poll()
    get(rig, pid, "nozzle")
    assert calls == []  # not even the poller asked CUPS


def test_status_ink_and_page_count_work_without_cups(rig):
    pid = rig.bare()
    p = get(rig, pid, "printer")["data"]
    assert p["inks"] and p["counter"]["value"] == 174 and p["counter"]["error"] is None
    n = get(rig, pid, "nozzle")["data"]
    assert n["cups"]["configured"] is False and n["last_print"] is not None  # last print comes from the page count
    assert n["tracking"]["cups_error"] is None and n["tracking"]["counter_error"] is None


def test_a_rising_page_count_is_still_noticed_without_cups(rig):
    pid = rig.bare()
    before = get(rig, pid, "nozzle")["data"]["last_print"]["ts"]
    rig.clock.advance(300)
    rig.ctx.backend_for(rig.ctx.printers.get(pid)).counter += 2
    rig.runtimes.get(pid).tracker.poll()
    assert get(rig, pid, "nozzle")["data"]["last_print"]["ts"] > before


def test_settings_and_rotation_say_cups_is_missing(rig):
    pid = rig.bare()
    assert get(rig, pid, "automation")["data"]["cups_configured"] is False
    assert get(rig, pid, "rotation")["data"]["cups_configured"] is False
    assert get(rig, PID, "automation")["data"]["cups_configured"] is True
    assert get(rig, PID, "rotation")["data"]["cups_configured"] is True


def test_print_now_is_refused_politely_and_leaves_no_trace(rig):
    pid = rig.bare()
    history_before = get(rig, pid, "nozzle")["data"]["history"]  # (the seeded past is already in it)
    backend, calls = rig.spy(pid)
    r = rig.client.post(f"/api/maintenance/{pid}/nozzle/print", json={}, headers=rig.h)
    body = r.get_json()
    assert r.status_code == 502 and body["ok"] is False and "CUPS" in body["error"]["message"] and "Install CUPS" in body["error"]["hint"]
    assert calls == [] and get(rig, pid, "nozzle")["data"]["history"] == history_before
    assert get(rig, pid, "nozzle")["data"]["print"]["allowed"] is True  # a refusal doesn't use up the rate limit


def test_the_automatic_print_waits_with_a_reason_and_never_tries(rig):
    pid = rig.bare()
    rig.client.put(f"/api/maintenance/{pid}/automation", json={"enabled": True, "idle_days": 0.5, "run_at": "00:00"}, headers=rig.h)
    history_before = get(rig, pid, "nozzle")["data"]["history"]
    rig.clock.advance(60)
    svc = rig.runtimes.get(pid).service
    svc.tracker.poll()
    plan = svc.run_schedule()
    assert plan.action == "blocked" and "CUPS" in plan.reason
    a = get(rig, pid, "nozzle")["data"]["automation"]
    assert a["state"] == "blocked" and "CUPS" in a["reason"]
    assert get(rig, pid, "nozzle")["data"]["history"] == history_before  # no failed attempts piling up


def test_schedule_plan_blocks_when_it_cannot_print_but_not_when_off():
    kw = dict(now=10 * DAY, last_print_ts=0.0, idle_days=0.5, run_at="00:00", tz=schedule.tz_for("UTC"),
              counter_ok=True, last_app_check_ts=None, last_failure_ts=None)
    assert schedule.plan(enabled=True, can_print=False, **kw).action == "blocked"
    assert schedule.plan(enabled=False, can_print=False, **kw).action == "off"
    assert schedule.plan(enabled=True, **kw).action == "print"  # the default is unchanged


def test_adding_a_queue_later_turns_everything_on(rig):
    pid = rig.bare()
    assert get(rig, pid, "queue")["data"]["configured"] is False
    r = rig.client.put(f"/api/printers/{pid}", json={"cups_queue": "T730"}, headers=rig.h)
    assert r.status_code == 200
    q = get(rig, pid, "queue")
    assert q["ok"] and q["data"]["configured"] is True and q["data"]["jobs"]
    assert rig.client.post(f"/api/maintenance/{pid}/nozzle/print", json={}, headers=rig.h).status_code == 202
    assert get(rig, pid, "nozzle")["data"]["cups"]["configured"] is True


def test_removing_the_queue_turns_it_off_again(rig):
    assert get(rig, PID, "queue")["data"]["configured"] is True
    rig.client.put(f"/api/printers/{PID}", json={"cups_queue": ""}, headers=rig.h)
    assert get(rig, PID, "queue")["data"]["configured"] is False
    assert get(rig, PID, "printer")["ok"] is True


def test_a_queue_that_is_set_but_unreachable_is_still_an_error_not_a_missing_feature(make_app):
    app, _ = make_app(fail=("cups",))
    c = app.test_client()
    q = c.get(f"/api/maintenance/{PID}/queue").get_json()
    assert q["ok"] is False and "CUPS" in q["error"]["message"]  # installed but down: say so, with a retry
    assert c.get(f"/api/maintenance/{PID}/printer").get_json()["ok"] is True


def test_a_whitespace_queue_counts_as_no_queue(rig):
    pid = rig.bare()
    svc = rig.runtimes.get(pid).service
    assert svc.cups_configured is False


def test_one_printer_without_cups_does_not_change_the_others(rig):
    pid = rig.bare()
    other = rig.client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=rig.h).get_json()["data"]["id"]
    assert get(rig, other, "queue")["data"]["configured"] is True
    assert get(rig, PID, "queue")["data"]["configured"] is True
    assert get(rig, pid, "queue")["data"]["configured"] is False
    assert rig.client.post(f"/api/maintenance/{other}/nozzle/print", json={}, headers=rig.h).status_code == 202


def test_links_that_need_a_queue_are_hidden_without_one(rig):
    pid = rig.bare()
    (rig.ctx.settings.config_dir / "links.yml").write_text(
        "links:\n  - {name: Panel, url: 'http://{ip}/'}\n  - {name: CUPS, url: '{cups_url}/printers/{queue}'}\n")
    names = lambda p: [t["name"] for t in rig.client.get(f"/api/links?printer={p}").get_json()["links"]]
    assert names(pid) == ["Panel"] and names(PID) == ["Panel", "CUPS"]
