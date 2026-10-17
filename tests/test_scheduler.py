"""The automatic check, end to end against the fake printer and CUPS (nothing here touches a real device)."""
import pytest

from app.tools.maintenance.runtimes import Runtimes
from conftest import PID, csrf_headers

DAY = 86400


@pytest.fixture
def rig(make_app, clock):
    """An app whose printer last printed 2.6 days ago, with a handle on the maintenance service."""
    app, printer = make_app(timezone="UTC", print_cooldown=0)
    client = app.test_client()
    ctx = app.extensions["panda"]["ctx"]
    service = Runtimes(ctx).get(PID).service  # same database as the app, so the same view of the world

    def configure(**values):
        r = client.put(f"/api/maintenance/{PID}/automation", json=values, headers=csrf_headers(client))
        assert r.status_code == 200, r.get_json()

    def auto_jobs():
        return [j for j in printer.jobs if "(auto)" in j["job-name"][0]]

    def rows(source="auto"):
        return ctx.db.query("SELECT * FROM mt_checks WHERE printer_id=? AND source=? ORDER BY ts", (PID, source))

    service.tracker.poll()
    return type("Rig", (), dict(app=app, client=client, printer=printer, service=service, configure=configure,
                                auto_jobs=staticmethod(auto_jobs), rows=staticmethod(rows), ctx=ctx, clock=clock))


def test_switched_off_never_prints(rig):
    rig.configure(idle_days=0.5, run_at="00:00")  # long overdue, but the switch is off
    rig.clock.advance(60)
    rig.service.run_schedule()
    assert rig.auto_jobs() == [] and rig.rows() == []


def test_prints_one_check_when_idle_and_then_waits_a_full_idle_period(rig):
    rig.configure(enabled=True, idle_days=2, run_at="00:00")  # last print was 2.6 days ago
    assert rig.service.run_schedule().action == "print"
    assert len(rig.auto_jobs()) == 1
    row = rig.rows()[0]
    assert (row["state"], row["job_id"]) == ("submitted", rig.auto_jobs()[0]["job-id"][0])
    # the check restarts the idle clock: nothing more is sent, now or a minute later
    assert rig.service.run_schedule().action == "wait"
    rig.clock.advance(60)
    assert rig.service.run_schedule().action in ("wait", "blocked") and len(rig.auto_jobs()) == 1


def test_the_check_is_matched_to_the_counter_and_is_not_a_person_printing(rig):
    rig.configure(enabled=True, idle_days=2, run_at="00:00")
    before = rig.client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_external_print"]
    rig.service.run_schedule()
    rig.clock.advance(60)  # the fake printer finishes the page and the counter moves
    rig.service.tracker.poll()
    d = rig.client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["last_print"]["kind"] == "nozzle" and d["last_external_print"] == before
    assert d["last_auto"]["source"] == "auto" and d["history"][0]["source"] == "auto"


def test_not_yet_idle_enough(rig):
    rig.configure(enabled=True, idle_days=7, run_at="00:00")  # 2.6 days idle, 7 needed
    p = rig.service.run_schedule()
    assert p.action == "wait" and rig.auto_jobs() == []
    d = rig.client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["automation"]
    assert d["enabled"] and d["state"] == "wait" and d["next_run_at"] > rig.clock()


def test_quiet_hours(rig):
    rig.configure(enabled=True, idle_days=2, run_at="23:59")  # the fake clock starts at 08:00 UTC
    assert rig.service.run_schedule().action == "wait" and rig.auto_jobs() == []
    rig.clock.advance(15 * 3600 + 59 * 60 + 30)  # 23:59:30
    rig.service.tracker.poll()
    assert rig.service.run_schedule().action == "print" and len(rig.auto_jobs()) == 1


def test_a_failing_cups_is_tried_once_then_left_alone_for_an_hour(rig):
    rig.configure(enabled=True, idle_days=2, run_at="00:00")
    rig.printer.fail = ("cups",)
    assert rig.service.run_schedule().action == "blocked"
    assert [r["state"] for r in rig.rows()] == ["failed"] and rig.auto_jobs() == []
    for _ in range(3):  # the poller looks every 15 seconds: it must not hammer CUPS
        rig.clock.advance(15)
        rig.service.run_schedule()
    assert len(rig.rows()) == 1
    rig.printer.fail = ()
    rig.clock.advance(3600)
    rig.service.tracker.poll()
    assert rig.service.run_schedule().action == "print" and len(rig.auto_jobs()) == 1


def test_nothing_is_printed_when_the_page_counter_cannot_be_read(rig):
    rig.configure(enabled=True, idle_days=2, run_at="00:00")
    rig.printer.fail = ("snmp",)
    rig.clock.advance(60)
    rig.service.tracker.poll()
    p = rig.service.run_schedule()
    assert p.action == "blocked" and "page count" in p.reason and rig.auto_jobs() == []


def test_someone_printing_just_before_the_check_is_due_cancels_it(rig):
    """The stored reading can be minutes old: the printer is looked at again at the moment of printing."""
    rig.configure(enabled=True, idle_days=2, run_at="00:00")  # due now, as far as the last reading knows
    rig.clock.advance(60)
    rig.printer.counter += 1  # a page came out of the printer after the last poll (no CUPS job: printed directly)
    p = rig.service.run_schedule()
    assert p.action == "wait" and rig.auto_jobs() == [] and rig.rows() == []
    assert rig.client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_print"]["kind"] != "nozzle"


def test_a_printer_that_stops_answering_just_before_the_check_is_not_printed_to(rig):
    rig.configure(enabled=True, idle_days=2, run_at="00:00")
    rig.printer.fail = ("snmp",)  # the last reading is recent, but now the counter can't be read
    p = rig.service.run_schedule()
    assert p.action == "blocked" and "page count" in p.reason and rig.auto_jobs() == [] and rig.rows() == []


def test_a_stuck_check_does_not_restart_the_idle_clock_or_pile_up(rig):
    """The page of a sent check never reached the counter (printer off?): it doesn't count as a print."""
    rig.configure(enabled=True, idle_days=2, run_at="00:00")
    store = rig.service.store
    store.add_check(rig.clock() - 7200, "auto", "submitted", job_id=99)  # sent two hours ago, never claimed
    d = rig.client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["last_print"]["kind"] != "nozzle"  # still counted from the real last print, 2.6 days ago
    rig.service.run_schedule()  # idle limit passed, but a check was sent two hours ago: not another one yet
    assert rig.auto_jobs() == []
    rig.clock.advance(5 * 3600)
    rig.service.tracker.poll()
    assert rig.service.run_schedule().action == "print"


def test_each_printer_has_its_own_switch_and_clock(rig):
    h = csrf_headers(rig.client)
    rig.client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h)
    rig.configure(enabled=True, idle_days=2, run_at="00:00")
    hall = Runtimes(rig.ctx).get("hall").service
    hall.tracker.poll()
    rig.clock.advance(60)
    assert hall.run_schedule().action == "off"  # Hall never switched it on
    assert rig.service.run_schedule().action == "print"
    assert rig.client.get("/api/maintenance/hall/automation").get_json()["data"]["values"]["enabled"] is False


def test_the_default_for_new_printers_can_be_switched_on_in_the_environment(make_app):
    app, _ = make_app(auto_check=True, check_time="06:30")
    d = app.test_client().get(f"/api/maintenance/{PID}/automation").get_json()["data"]
    assert d["defaults"]["enabled"] is True and d["values"]["run_at"] == "06:30"
