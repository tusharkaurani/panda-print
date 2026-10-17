"""Several printers at once, against fakes only. The home setup has one printer, so these tests are what
stands behind "add as many as you like": every per-printer thing must stay per-printer, a printer's trouble must
stay its own, and removing, renaming or editing one must not disturb the rest. Extend this file with the tool."""
import io
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.core.printers import PrinterRegistry, reserved_ids
from app.tools.maintenance.poller import Poller
from app.tools.maintenance.runtimes import Runtimes
from app.tools.maintenance.store import Store
from conftest import PID, csrf_headers
from fakes import seed_history

PNG = b"\x89PNG\r\n\x1a\n" + b"a" * 200
PNG2 = b"\x89PNG\r\n\x1a\n" + b"b" * 200
DAY = 86400
HP, HALL = "hp-envy", "hall"


class Fleet:
    """Three printers behind one app: the Brother, an HP and a printer called Hall (every one is a fake)."""

    def __init__(self, make_app, clock, **overrides):
        self.app, _ = make_app(**{"timezone": "UTC", "print_cooldown": 0, "print_max_per_hour": 1000, **overrides})
        self.clock, self.client = clock, self.app.test_client()
        self.h = csrf_headers(self.client)
        self.ctx = self.app.extensions["panda"]["ctx"]
        self.runtimes = Runtimes(self.ctx)
        self.ids = [PID]
        self.add("HP Envy", "192.168.1.23", "HP_ENVY")
        self.add("Hall", "192.168.1.50", "HALL")
        for pid in self.ids:
            self.rt(pid).service.tracker.poll()

    def add(self, name, ip, queue="", seed=True, **extra):
        r = self.client.post("/api/printers", json={"name": name, "ip": ip, "cups_queue": queue, **extra}, headers=self.h)
        assert r.status_code == 201, r.get_json()
        pid = r.get_json()["data"]["id"]
        if seed:
            seed_history(Store(self.ctx.db, pid), self.clock())
        self.ids.append(pid)
        return pid

    def rt(self, pid):
        return self.runtimes.get(pid)

    def backend(self, pid):
        return self.ctx.backend_for(self.ctx.printers.get(pid))

    def get(self, pid, card):
        return self.client.get(f"/api/maintenance/{pid}/{card}")

    def put(self, path, body):
        return self.client.put(path, json=body, headers=self.h)

    def upload(self, data, name):
        r = self.client.post("/api/maintenance/pages", data={"file": (io.BytesIO(data), name)}, headers=self.h,
                             content_type="multipart/form-data")
        assert r.status_code == 201, r.get_json()
        return r.get_json()["data"]

    def configure(self, pid, **values):
        r = self.put(f"/api/maintenance/{pid}/automation", values)
        assert r.status_code == 200, r.get_json()

    def sent(self, pid):
        return [d["name"] for d in self.backend(pid).submitted]


@pytest.fixture
def fleet(make_app, clock):
    return Fleet(make_app, clock)


# ------------------------------------------------------------------ cards stay per printer

def test_every_printer_answers_with_its_own_data(fleet):
    names = {pid: fleet.get(pid, "printer").get_json()["data"]["name"] for pid in fleet.ids}
    assert names[PID] == "Brother DCP-T730DW" and names[HP] == "HP ENVY 6000 series"
    for pid in fleet.ids:
        for card in ("printer", "queue", "nozzle"):
            assert fleet.get(pid, card).get_json()["ok"] is True, (pid, card)
    queues = {pid: fleet.get(pid, "queue").get_json()["data"] for pid in fleet.ids}
    assert len({json.dumps(q.get("name")) for q in queues.values()}) >= 1  # each asked its own queue, none crashed


def test_one_printer_being_down_leaves_the_others_untouched(fleet):
    fleet.backend(HALL).fail = ("printer", "snmp", "cups")
    down = fleet.get(HALL, "printer").get_json()
    assert down["ok"] is False and down["error"]["message"]  # a readable reason, not a 500
    for pid in (PID, HP):
        assert all(fleet.get(pid, c).get_json()["ok"] for c in ("printer", "queue", "nozzle")), pid
    # and it recovers by itself when it comes back
    fleet.backend(HALL).fail = ()
    fleet.rt(HALL).service._printer_cache.clear()
    assert fleet.get(HALL, "printer").get_json()["ok"] is True


def test_a_printer_with_no_cups_queue_still_shows_status_and_fails_to_print_politely(fleet):
    bare = fleet.add("Spare", "192.168.1.60", "")
    assert fleet.get(bare, "printer").get_json()["ok"] is True
    q = fleet.get(bare, "queue").get_json()
    assert q["ok"] is False and "No CUPS queue" in q["error"]["message"]
    r = fleet.client.post(f"/api/maintenance/{bare}/nozzle/print", json={}, headers=fleet.h)
    assert r.status_code in (400, 502) and "queue" in r.get_json()["error"]["message"].lower()
    fleet.configure(bare, enabled=True, idle_days=0.5, run_at="00:00")
    fleet.clock.advance(60)
    fleet.rt(bare).service.tracker.poll()
    assert fleet.rt(bare).service.run_schedule().action in ("blocked", "wait", "print")  # never raises
    assert fleet.get(PID, "nozzle").get_json()["ok"] is True


def test_two_queues_on_one_physical_printer_keep_separate_books(fleet):
    twin = fleet.add("Brother (photo tray)", "192.168.1.18", "T730_PHOTO")
    manual = lambda pid: [r for r in fleet.get(pid, "nozzle").get_json()["data"]["history"] if r["source"] == "manual"]
    before_brother, before_twin = len(manual(PID)), len(manual(twin))
    assert fleet.client.post(f"/api/maintenance/{twin}/nozzle/print", json={}, headers=fleet.h).status_code == 202
    assert len(fleet.sent(twin)) == 1 and fleet.sent(PID) == []
    assert len(manual(twin)) == before_twin + 1 and len(manual(PID)) == before_brother


# ------------------------------------------------------------------ settings and rotation stay per printer

def test_automation_settings_belong_to_one_printer(fleet):
    fleet.configure(HP, enabled=True, idle_days=3, run_at="07:30")
    seen = {pid: fleet.get(pid, "automation").get_json()["data"]["values"] for pid in fleet.ids}
    assert seen[HP]["enabled"] is True and seen[HP]["idle_days"] == 3 and seen[HP]["run_at"] == "07:30"
    for pid in (PID, HALL):
        assert seen[pid]["enabled"] is False and seen[pid]["idle_days"] == 7.0 and seen[pid]["run_at"] == "10:00"
    assert fleet.client.delete(f"/api/maintenance/{HP}/automation", headers=fleet.h).status_code == 200
    fleet.configure(PID, idle_days=2)
    assert fleet.get(HALL, "automation").get_json()["data"]["values"]["idle_days"] == 7.0
    assert fleet.get(PID, "automation").get_json()["data"]["values"]["idle_days"] == 2


def test_the_library_is_shared_but_each_printer_has_its_own_order_switches_and_cursor(fleet):
    a, b = fleet.upload(PNG, "Bunny")["id"], fleet.upload(PNG2, "Fish")["id"]
    for pid in fleet.ids:  # a new page shows up for every printer
        ids = [i["id"] for i in fleet.get(pid, "rotation").get_json()["data"]["items"]]
        assert {a, b} <= set(ids), pid
    order = [{"id": b, "enabled": True}, {"id": "standard", "enabled": False}, {"id": a, "enabled": True}]
    assert fleet.put(f"/api/maintenance/{HP}/rotation", {"items": order}).status_code == 200
    mine = lambda pid: [(i["id"], i["enabled"]) for i in fleet.get(pid, "rotation").get_json()["data"]["items"]]
    assert mine(HP) == [(b, True), ("standard", False), (a, True)]
    assert mine(PID) == mine(HALL) == [("standard", True), (a, True), (b, True)]
    for pid in (PID, HP):
        fleet.configure(pid, rotate=True)
    names = lambda pid: [n.split(" - ", 1)[1] for n in fleet.sent(pid)]
    for pid in (PID, HP, PID):
        assert fleet.client.post(f"/api/maintenance/{pid}/nozzle/print", json={}, headers=fleet.h).status_code == 202
        fleet.clock.advance(1)
    assert names(PID) == ["Standard test page", "Bunny"] and names(HP) == ["Fish"] and fleet.sent(HALL) == []


def test_deleting_a_library_page_removes_it_from_every_printers_rotation(fleet):
    page = fleet.upload(PNG, "Bunny")["id"]
    fleet.put(f"/api/maintenance/{HP}/rotation", {"items": [{"id": page, "enabled": True}, {"id": "standard", "enabled": True}]})
    assert fleet.client.delete(f"/api/maintenance/pages/{page}", headers=fleet.h).status_code == 200
    for pid in fleet.ids:
        assert page not in [i["id"] for i in fleet.get(pid, "rotation").get_json()["data"]["items"]], pid
        fleet.configure(pid, rotate=True)
        assert fleet.client.post(f"/api/maintenance/{pid}/nozzle/print", json={}, headers=fleet.h).status_code == 202


def test_printing_goes_to_the_chosen_printer_only(fleet):
    fleet.client.post(f"/api/maintenance/{HP}/nozzle/print", json={}, headers=fleet.h)
    assert len(fleet.sent(HP)) == 1 and fleet.sent(PID) == [] and fleet.sent(HALL) == []


def test_manual_print_limits_are_counted_per_printer(make_app, clock):
    fleet = Fleet(make_app, clock, print_cooldown=45)
    post = lambda pid: fleet.client.post(f"/api/maintenance/{pid}/nozzle/print", json={}, headers=fleet.h)
    assert post(PID).status_code == 202
    assert post(PID).status_code == 429  # its own cooldown
    assert post(HP).status_code == 202 and post(HALL).status_code == 202  # not held up by the Brother
    clock.advance(46)
    assert post(PID).status_code == 202


# ------------------------------------------------------------------ the automatic check, for each printer

def due(fleet, pid, **extra):
    fleet.configure(pid, enabled=True, idle_days=2, run_at="00:00", **extra)  # the seeded last print was 2.6 days ago


def test_each_idle_printer_gets_exactly_one_check_on_its_own_queue(fleet):
    for pid in fleet.ids:
        due(fleet, pid)
    fleet.clock.advance(60)
    for pid in fleet.ids:
        fleet.rt(pid).service.tracker.poll()
    for _ in range(2):  # a second pass must not print again
        for pid in fleet.ids:
            fleet.rt(pid).service.run_schedule()
    for pid in fleet.ids:
        assert len(fleet.sent(pid)) == 1 and "(auto)" in fleet.sent(pid)[0], pid


def test_only_the_printers_switched_on_print(fleet):
    due(fleet, HP)
    fleet.clock.advance(60)
    for pid in fleet.ids:
        fleet.rt(pid).service.tracker.poll()
        fleet.rt(pid).service.run_schedule()
    assert [pid for pid in fleet.ids if fleet.sent(pid)] == [HP]


def test_a_failing_printer_backs_off_alone(fleet):
    for pid in (PID, HP):
        due(fleet, pid)
    fleet.backend(PID).fail = ("cups",)
    fleet.clock.advance(60)
    for pid in (PID, HP):
        fleet.rt(pid).service.tracker.poll()
    assert fleet.rt(PID).service.run_schedule().action == "blocked"
    assert fleet.rt(HP).service.run_schedule().action == "print"
    fleet.clock.advance(15)
    assert fleet.rt(PID).service.run_schedule().action == "blocked"  # still backing off, not hammering CUPS
    assert len(fleet.sent(HP)) == 1 and fleet.sent(PID) == []


def test_a_page_counter_rising_on_one_printer_is_not_another_printers_print(fleet):
    before = {pid: fleet.get(pid, "nozzle").get_json()["data"]["last_print"] for pid in fleet.ids}
    fleet.clock.advance(120)
    fleet.backend(HP).counter += 3  # someone printed straight to the HP
    for pid in fleet.ids:
        fleet.rt(pid).service.tracker.poll()
    after = {pid: fleet.get(pid, "nozzle").get_json()["data"]["last_print"] for pid in fleet.ids}
    assert after[HP]["ts"] > before[HP]["ts"]
    assert after[PID] == before[PID] and after[HALL] == before[HALL]


def test_a_print_just_before_the_check_cancels_it_for_that_printer_only(fleet):
    for pid in (PID, HP):
        due(fleet, pid)
    fleet.clock.advance(60)
    fleet.backend(HP).counter += 1  # a page came out of the HP after its last reading
    assert fleet.rt(HP).service.run_schedule().action == "wait"
    assert fleet.rt(PID).service.run_schedule().action == "print"


# ------------------------------------------------------------------ the background poller

def wait_for(pred, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_a_slow_printer_does_not_hold_up_the_others(fleet):
    slow, hits = threading.Event(), []
    real = fleet.backend(HALL).read_counter
    fleet.backend(HALL).read_counter = lambda: (hits.append(1), slow.wait(10), real())[2]  # a hung address
    for pid in fleet.ids:
        fleet.rt(pid).store.put("last_poll_ts", None)
    poller = Poller(fleet.ctx, fleet.runtimes)
    futures = poller.cycle()
    assert len(futures) == 3
    assert wait_for(lambda: sum(f.done() for f in futures) == 2), "the other two finish while Hall is still waiting"
    assert fleet.rt(PID).store.get("last_poll_ts") is not None and fleet.rt(HP).store.get("last_poll_ts") is not None
    assert len(poller.cycle()) == 2  # Hall is still busy, so it is not started a second time
    assert len(hits) == 1
    slow.set()
    assert wait_for(lambda: not poller._busy)


def test_a_printer_is_never_polled_twice_at_once(fleet):
    gate, calls = threading.Event(), []
    rt = fleet.rt(PID)
    real = rt.tracker.poll
    rt.tracker.poll = lambda: (calls.append(1), gate.wait(10), real())[2]
    rt.store.put("last_poll_ts", None)
    poller = Poller(fleet.ctx, fleet.runtimes)
    poller.cycle()
    assert wait_for(lambda: len(calls) == 1)
    for _ in range(3):
        poller.cycle()
    gate.set()
    assert wait_for(lambda: not poller._busy)
    assert len(calls) == 1


def test_an_error_in_one_printers_turn_does_not_stop_the_others(fleet):
    due(fleet, HP)
    fleet.clock.advance(60)
    fleet.rt(HP).service.tracker.poll()

    def boom():
        raise RuntimeError("tracker blew up")
    fleet.rt(PID).tracker.poll = boom
    fleet.rt(PID).store.put("last_poll_ts", None)
    poller = Poller(fleet.ctx, fleet.runtimes)
    for f in poller.cycle():
        f.result(timeout=5)  # no exception escapes
    assert len(fleet.sent(HP)) == 1  # the HP's check still went out


def test_a_printer_removed_while_it_waits_is_skipped(fleet):
    poller = Poller(fleet.ctx, fleet.runtimes)
    rt = fleet.rt(HALL)
    fleet.client.delete(f"/api/printers/{HALL}", headers=fleet.h)
    poller._busy.add(HALL)
    poller._turn(rt)
    assert fleet.ctx.db.one("SELECT COUNT(*) AS n FROM kv WHERE key LIKE ?", (f"maintenance.{HALL}.%",))["n"] == 0


# ------------------------------------------------------------------ add, edit, remove

def test_removing_the_middle_printer_leaves_the_others_complete(fleet):
    fleet.configure(PID, enabled=True, idle_days=2)
    fleet.configure(HALL, enabled=True, idle_days=4)
    page = fleet.upload(PNG, "Bunny")["id"]
    fleet.client.post(f"/api/maintenance/{HP}/nozzle/print", json={}, headers=fleet.h)
    before = {pid: fleet.get(pid, "nozzle").get_json()["data"]["history"] for pid in (PID, HALL)}
    assert fleet.client.delete(f"/api/printers/{HP}", headers=fleet.h).status_code == 200
    assert [p["id"] for p in fleet.client.get("/api/printers").get_json()["data"]["printers"]] == [PID, HALL]
    for card in ("printer", "queue", "nozzle", "automation", "rotation"):
        assert fleet.get(HP, card).status_code == 404, card
    assert fleet.get(PID, "automation").get_json()["data"]["values"]["idle_days"] == 2
    assert fleet.get(HALL, "automation").get_json()["data"]["values"]["idle_days"] == 4
    assert {pid: fleet.get(pid, "nozzle").get_json()["data"]["history"] for pid in (PID, HALL)} == before
    assert page in [p["id"] for p in fleet.client.get("/api/maintenance/pages").get_json()["data"]["pages"]]  # shared library kept
    assert fleet.client.post(f"/api/maintenance/{PID}/nozzle/print", json={}, headers=fleet.h).status_code == 202


def test_a_removed_printer_leaves_nothing_in_any_table(fleet):
    """Any table with a printer_id column is cleaned. A new tool that adds such a table must register on_delete,
    and this test is what tells it so."""
    fleet.configure(HP, enabled=True)
    fleet.client.post(f"/api/maintenance/{HP}/nozzle/print", json={}, headers=fleet.h)
    fleet.put(f"/api/maintenance/{HP}/rotation", {"items": [{"id": "standard", "enabled": False}]})
    fleet.client.delete(f"/api/printers/{HP}", headers=fleet.h)
    tables = [r["name"] for r in fleet.ctx.db.query("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        cols = [c["name"] for c in fleet.ctx.db.query(f"PRAGMA table_info({t})")]
        if "printer_id" in cols:
            assert fleet.ctx.db.one(f"SELECT COUNT(*) AS n FROM {t} WHERE printer_id=?", (HP,))["n"] == 0, t
    assert fleet.ctx.db.one("SELECT COUNT(*) AS n FROM kv WHERE key LIKE ?", (f"%.{HP}.%",))["n"] == 0


def test_a_new_printer_with_a_reused_name_starts_clean_even_if_a_late_write_landed(fleet):
    fleet.configure(HALL, enabled=True, idle_days=4)
    fleet.client.post(f"/api/maintenance/{HALL}/nozzle/print", json={}, headers=fleet.h)
    fleet.client.delete(f"/api/printers/{HALL}", headers=fleet.h)
    Store(fleet.ctx.db, HALL).put("last_poll_ts", fleet.clock())  # a poll that was in flight when it was removed
    again = fleet.add("Hall", "192.168.1.51", "HALL", seed=False)
    assert again == HALL
    assert fleet.get(again, "automation").get_json()["data"]["values"]["enabled"] is False
    assert Store(fleet.ctx.db, again).get("last_poll_ts") is None
    rows = fleet.get(again, "nozzle").get_json()["data"]["history"]
    assert not [r for r in rows if r["source"] == "manual"]  # the removed Hall's own print is gone (CUPS's old jobs are not ours to hide)


def test_the_last_printer_can_be_removed_and_added_back(fleet):
    for pid in list(fleet.ids):
        assert fleet.client.delete(f"/api/printers/{pid}", headers=fleet.h).status_code == 200
    assert fleet.client.get("/api/printers").get_json()["data"]["printers"] == []
    assert Poller(fleet.ctx, fleet.runtimes).cycle() == []  # nothing to do, nothing breaks
    fleet.ids.clear()
    pid = fleet.add("Brother again", "192.168.1.18", "T730", seed=False)
    assert fleet.get(pid, "printer").get_json()["ok"] is True


def test_editing_one_printer_changes_only_that_printer(fleet):
    fleet.configure(PID, idle_days=2)
    before = {pid: fleet.get(pid, "automation").get_json()["data"]["values"] for pid in (PID, HALL)}
    r = fleet.client.put(f"/api/printers/{HP}", json={"name": "Study HP", "cups_queue": "HP_ENVY"}, headers=fleet.h)
    assert r.status_code == 200 and r.get_json()["data"]["id"] == HP  # the id (and so its history) survives a rename
    names = {p["id"]: p["name"] for p in fleet.client.get("/api/printers").get_json()["data"]["printers"]}
    assert names == {PID: "Brother DCP-T730DW", HP: "Study HP", HALL: "Hall"}
    assert {pid: fleet.get(pid, "automation").get_json()["data"]["values"] for pid in (PID, HALL)} == before


def test_a_name_already_used_by_another_printer_is_refused_on_edit_and_add(fleet):
    r = fleet.client.put(f"/api/printers/{HP}", json={"name": "hall"}, headers=fleet.h)
    assert r.status_code == 400 and "name" in r.get_json()["error"]["fields"]
    r = fleet.client.post("/api/printers", json={"name": "HALL", "ip": "192.168.1.77"}, headers=fleet.h)
    assert r.status_code == 400
    assert fleet.client.put(f"/api/printers/{HP}", json={"name": "HP Envy"}, headers=fleet.h).status_code == 200  # its own name is fine


def test_editing_keeps_the_per_printer_locks_so_two_services_cannot_print_at_once(fleet):
    old = fleet.rt(HP)
    fleet.client.put(f"/api/printers/{HP}", json={"cups_queue": "HP_ENVY_2"}, headers=fleet.h)
    new = fleet.rt(HP)
    assert new is not old and new.service.config.cups_queue == "HP_ENVY_2"  # rebuilt for the new settings
    assert new.service._print_lock is old.service._print_lock and new.tracker._lock is old.tracker._lock
    assert fleet.rt(PID).service._print_lock is not new.service._print_lock


def test_edits_and_removals_from_one_runtime_are_seen_by_another(fleet):
    """The API and the background poller hold different Runtimes objects over one database."""
    other = Runtimes(fleet.ctx)
    assert other.get(HP) is not None
    fleet.client.delete(f"/api/printers/{HP}", headers=fleet.h)
    assert other.get(HP) is None and fleet.runtimes.get(HP) is None
    assert [rt.printer.id for rt in other.all()] == [PID, HALL]


# ------------------------------------------------------------------ ids, names and routes

TRICKY = ["Pages", "Probe", "Discover", "Links", "Setup", "Tools", "Test", "Defaults", "プリンタ", "  Spaces   everywhere ",
          "a/b", "x" * 60, "Hall!", "hall 2", "../etc", "%2e%2e"]


@pytest.mark.parametrize("name", TRICKY)
def test_any_name_makes_a_printer_that_works_everywhere(fleet, name):
    r = fleet.client.post("/api/printers", json={"name": name, "ip": "192.168.1.90", "cups_queue": "Q"}, headers=fleet.h)
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["data"]["id"]
    assert re.fullmatch(r"[a-z0-9-]{1,45}", pid) and pid not in reserved_ids()
    seed_history(Store(fleet.ctx.db, pid), fleet.clock())
    for card in ("printer", "queue", "nozzle", "automation", "rotation"):
        got = fleet.get(pid, card)
        assert got.status_code == 200 and got.get_json()["ok"] is True, (pid, card)
    assert fleet.client.post(f"/api/maintenance/{pid}/nozzle/print", json={}, headers=fleet.h).status_code == 202
    assert fleet.client.put(f"/api/printers/{pid}", json={"cups_queue": "Q2"}, headers=fleet.h).status_code == 200
    assert fleet.client.post(f"/api/printers/{pid}/test", headers=fleet.h).status_code == 200
    # the library and the other printers are unaffected by the odd id
    assert fleet.client.get("/api/maintenance/pages").status_code == 200
    assert fleet.get(PID, "printer").get_json()["ok"] is True
    assert fleet.client.delete(f"/api/printers/{pid}", headers=fleet.h).status_code == 200
    assert fleet.get(pid, "printer").status_code == 404


def test_names_that_slug_alike_get_distinct_ids(fleet):
    ids = [fleet.add(n, "192.168.1.91") for n in ("Hall!", "hall?", "Hall 2", "Hall 2.", "hall-2")]
    assert len(set(ids)) == 5 and HALL in fleet.ids and not set(ids) & {HALL}


def test_every_printer_route_answers_404_for_an_unknown_printer_and_never_500(fleet):
    """Walks the app's own URL map, so a route added later is covered without touching this test."""
    bogus = "no-such-printer"
    checked = 0
    for rule in fleet.app.url_map.iter_rules():
        if "<pid>" not in rule.rule or not rule.rule.startswith("/api/"):
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            url = rule.rule.replace("<pid>", bogus)
            r = fleet.client.open(url, method=method, headers=fleet.h, json={})
            assert r.status_code == 404, (method, rule.rule, r.status_code)
            assert r.get_json()["ok"] is False
            checked += 1
    assert checked >= 12


def test_changes_need_a_csrf_token_for_every_printer_route(fleet):
    for rule in fleet.app.url_map.iter_rules():
        if not rule.rule.startswith("/api/") or not ({"POST", "PUT", "DELETE"} & rule.methods):
            continue
        url = rule.rule.replace("<pid>", HP).replace("<page_id>", "x")
        for method in rule.methods & {"POST", "PUT", "DELETE"}:
            assert fleet.client.open(url, method=method, json={}).status_code in (403, 400), (method, rule.rule)


def test_many_printers_stay_correct_and_fast(make_app, clock):
    fleet = Fleet(make_app, clock)
    start = time.time()
    for i in range(25):
        fleet.add(f"Printer {i}", f"10.0.0.{i + 1}", f"Q{i}")
    assert len(fleet.client.get("/api/printers").get_json()["data"]["printers"]) == 28
    assert len(set(fleet.ids)) == 28
    poller = Poller(fleet.ctx, fleet.runtimes)
    for f in poller.cycle():
        f.result(timeout=30)
    assert time.time() - start < 20


def test_printers_added_at_the_same_moment_all_get_saved(fleet):
    results, errors = [], []

    def go(i):
        try:
            c = fleet.app.test_client()
            r = c.post("/api/printers", json={"name": "Same", "ip": f"10.1.0.{i}"}, headers=csrf_headers(c))
            results.append(r.status_code)
        except Exception as e:  # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=go, args=(i,)) for i in range(1, 9)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert not errors and sorted(results) == [201] + [400] * 7  # one wins the name, the rest are told it is taken
    ids = [p["id"] for p in fleet.client.get("/api/printers").get_json()["data"]["printers"]]
    assert len(ids) == len(set(ids)) == 4


def test_two_different_names_added_concurrently_get_different_ids(fleet):
    out = []

    def go(name, ip):
        c = fleet.app.test_client()
        out.append(c.post("/api/printers", json={"name": name, "ip": ip}, headers=csrf_headers(c)).get_json()["data"]["id"])
    ts = [threading.Thread(target=go, args=(n, f"10.2.0.{i}")) for i, n in enumerate(["Hall!", "hall?", "Hall."])]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert len(out) == 3 and len(set(out)) == 3


# ------------------------------------------------------------------ links and the header selector

def test_launcher_links_follow_the_selected_printer(fleet):
    (fleet.ctx.settings.config_dir / "links.yml").write_text(
        "links:\n  - {name: 'Panel {name}', url: 'http://{ip}/'}\n  - {name: Queue, url: '{cups_url}/printers/{queue}'}\n"
        "  - {name: Only HP, url: 'http://hp.example/', printers: ['HP Envy']}\n  - {name: Global, url: 'https://example.com/'}\n")
    def tiles(pid):
        return {t["name"]: t["url"] for t in fleet.client.get(f"/api/links?printer={pid}").get_json()["links"]}
    brother, hp, hall = tiles(PID), tiles(HP), tiles(HALL)
    assert brother["Panel Brother DCP-T730DW"] == "http://192.168.1.18/" and hp["Panel HP Envy"] == "http://192.168.1.23/"
    assert hall["Panel Hall"] == "http://192.168.1.50/" and brother["Queue"].endswith("/printers/T730") and hall["Queue"].endswith("/printers/HALL")
    assert "Only HP" in hp and "Only HP" not in brother and "Only HP" not in hall
    assert all("Global" in t for t in (brother, hp, hall))


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_printer_selector_logic(tmp_path):
    """static/lib/printer-store.js decides which printer the header shows. Run it for real under node."""
    src = (Path(__file__).resolve().parent.parent / "app/static/lib/printer-store.js").read_text()
    (tmp_path / "store.mjs").write_text(src)
    (tmp_path / "run.mjs").write_text("""
import assert from 'node:assert/strict';
const mem = {}; let blocked = false;
globalThis.localStorage = { getItem: (k) => { if (blocked) throw new Error('blocked'); return mem[k] ?? null; },
                            setItem: (k, v) => { if (blocked) throw new Error('blocked'); mem[k] = v; } };
const { store } = await import('./store.mjs');
const P = (...ids) => ids.map((id) => ({ id, name: id }));
const seen = []; store.subscribe((p) => seen.push(p?.id ?? null));

store.set(P('a', 'b', 'c'), {});                  assert.equal(store.currentId, 'a');           // first by default
store.select('b');                                assert.equal(store.currentId, 'b');
store.select('nope');                             assert.equal(store.currentId, 'b');           // unknown ids ignored
store.set(P('a', 'b', 'c'), {});                  assert.equal(store.currentId, 'b');           // a refresh keeps the choice
store.set(P('a', 'c'), {});                       assert.equal(store.currentId, 'a');           // selected one removed -> first
store.set([], {});                                assert.equal(store.currentId, null); assert.equal(store.current, null);
store.set(P('z'), {});                            assert.equal(store.currentId, 'z');           // wizard adds the first printer
store.set(P('z', 'b'), {});  store.select('b');   assert.equal(mem['pp-printer'], 'b');         // remembered for the next visit
store.set([], {});  store.set(P('a', 'b'), {});   assert.equal(store.currentId, 'b');           // ...and restored when it exists again
const n = seen.length; store.set(P('a', 'b'), {}); assert.equal(seen.length, n);                 // an identical refresh is silent
store.set([{ id: 'a', name: 'a' }, { id: 'b', name: 'renamed' }], {}); assert.equal(seen.length, n + 1); // a rename notifies
blocked = true; store.select('a'); assert.equal(store.currentId, 'a');                           // blocked storage is fine
console.log('ok');
""")
    out = subprocess.run(["node", str(tmp_path / "run.mjs")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr
