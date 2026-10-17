from app.core.db import Database
from app.tools.maintenance import store as store_mod
from app.tools.maintenance.store import Store
from app.tools.maintenance.tracker import Tracker, split_delta
from app.settings import Settings
from conftest import Clock, T0

DAY = 86400


class NoPrinter:  # the tracker's pure-DB paths don't touch the printer
    pass


def make(tmp_path, **kw):
    clock = Clock()
    host_state = kw.pop("host_state", str(tmp_path / "state"))
    settings = Settings(**kw)
    db = Database(":memory:")
    store_mod.migrate(db)
    store = Store(db, "p1")
    return Tracker(settings, store, NoPrinter(), clock, host_state), store, clock


def test_split_delta():
    assert split_delta(1, 1) == (1, 0)
    assert split_delta(3, 1) == (1, 2)
    assert split_delta(1, 0) == (0, 1)
    assert split_delta(1, 5) == (1, 0)


def test_first_sample_starts_the_clock_now(tmp_path):
    tr, store, clock = make(tmp_path)
    tr.record_counter(174, clock())
    row = store.last_activity()
    assert row["kind"] == "baseline" and row["ts"] == T0 and store.get("last_count") == "174"


def test_first_sample_uses_host_state_when_it_agrees(tmp_path):
    tr, store, clock = make(tmp_path)
    (tmp_path / "state").write_text(f"174 {T0 - 3 * DAY}\n")
    tr.record_counter(174, clock())
    assert store.last_activity()["ts"] == T0 - 3 * DAY


def test_first_sample_ignores_stale_host_state(tmp_path):
    tr, store, clock = make(tmp_path)
    (tmp_path / "state").write_text(f"170 {T0 - 3 * DAY}\n")  # counter moved since
    tr.record_counter(174, clock())
    assert store.last_activity()["ts"] == T0


def test_unexplained_increase_is_someone_printing(tmp_path):
    tr, store, clock = make(tmp_path)
    tr.record_counter(174, clock())
    clock.advance(300)
    tr.record_counter(177, clock())
    row = store.last_activity()
    assert (row["kind"], row["delta"], row["count"]) == ("external", 3, 177)
    assert row["since_ts"] == T0  # it happened between the two reads


def test_own_nozzle_check_is_not_counted_as_someone_printing(tmp_path):
    tr, store, clock = make(tmp_path)
    tr.record_counter(174, clock())
    store.add_check(clock(), "manual", "submitted", job_id=40)
    clock.advance(60)
    tr.record_counter(175, clock())
    assert store.last_activity()["kind"] == "nozzle"
    assert store.last_activity("external") is None
    assert store.unclaimed_checks(0) == []  # claimed, can't be matched twice


def test_nozzle_plus_a_real_print_in_the_same_interval(tmp_path):
    tr, store, clock = make(tmp_path)
    tr.record_counter(174, clock())
    store.add_check(clock(), "manual", "submitted", job_id=40)
    clock.advance(60)
    tr.record_counter(177, clock())  # 1 nozzle page + 2 document pages
    assert store.last_activity("nozzle")["delta"] == 1
    assert store.last_activity("external")["delta"] == 2


def test_stale_unclaimed_check_does_not_swallow_later_printing(tmp_path):
    tr, store, clock = make(tmp_path, attribution_window=1800)
    tr.record_counter(174, clock())
    store.add_check(clock(), "manual", "submitted", job_id=40)  # never printed
    clock.advance(3 * 3600)
    tr.record_counter(175, clock())
    assert store.last_activity()["kind"] == "external"


def test_counter_going_backwards_restarts_tracking(tmp_path):
    tr, store, clock = make(tmp_path)
    tr.record_counter(174, clock())
    clock.advance(300)
    tr.record_counter(3, clock())  # printer replaced
    assert store.last_activity()["kind"] == "baseline" and store.get("last_count") == "3"


def test_import_host_checks_from_cups_jobs(tmp_path):
    tr, store, clock = make(tmp_path)
    jobs = [
        {"job-id": [34], "job-name": ["nozzle-check.ps"], "job-originating-user-name": ["root"],
         "job-state": [9], "time-at-creation": [1000], "time-at-completed": [1030]},
        {"job-id": [35], "job-name": ["Invoice.pdf"], "job-originating-user-name": ["me"], "job-state": [9],
         "time-at-creation": [1100]},
        {"job-id": [36], "job-name": ["panda-print nozzle check (manual)"],
         "job-originating-user-name": ["panda-print"], "job-state": [9], "time-at-creation": [1200]},
        {"job-id": [37], "job-name": ["nozzle-check.ps"], "job-originating-user-name": ["root"],
         "job-state": [5], "time-at-creation": [1300]},
        {"job-id": [38], "job-name": ["nozzle-check.ps"], "job-originating-user-name": ["root"],
         "job-state": [7], "time-at-creation": [1400]},
    ]
    assert tr.import_host_checks(jobs) == 2  # completed + canceled; not ours, not a document, not in flight
    assert tr.import_host_checks(jobs) == 0  # idempotent
    auto = store.last_check(("auto", "host"))
    assert auto["job_id"] == 34 and auto["ts"] == 1030
    assert [r["state"] for r in store.recent_checks(10)] == ["failed", "completed"]


def test_host_job_without_a_job_name_is_recognised_by_its_document_name(tmp_path):
    """Real CUPS answers for jobs sent with `lp` omit job-name and the user; the file name survives."""
    tr, store, _ = make(tmp_path)
    jobs = [{"job-id": [2], "document-name-supplied": ["nozzle-check.ps"], "job-state": [9],
             "time-at-creation": [1792175189], "time-at-completed": [1792175205]},
            {"job-id": [1], "document-name-supplied": ["testprint"], "job-state": [9],
             "time-at-creation": [1792175166], "time-at-completed": [1792175168]}]
    assert tr.import_host_checks(jobs) == 1
    assert store.last_check(("host",))["job_id"] == 2


def _doc(job_id, name, completed, state=9):
    return {"job-id": [job_id], "job-name": [name], "job-state": [state], "time-at-creation": [completed - 5],
            "time-at-completed": [completed]}


def test_first_sample_starts_from_the_newest_real_job_in_cups(tmp_path):
    tr, store, clock = make(tmp_path)
    jobs = [_doc(1, "old.pdf", T0 - 5 * DAY), _doc(2, "nozzle-check.ps", T0 - 1 * DAY),
            _doc(3, "newer.pdf", T0 - 2 * DAY), _doc(4, "panda-print nozzle check (manual)", T0 - 3600),
            _doc(5, "still printing", T0 - 60, state=5)]
    assert tr.newest_document_ts(jobs) == T0 - 2 * DAY  # not the nozzle checks, not the unfinished one
    tr._newest_document = tr.newest_document_ts(jobs)
    tr.record_counter(174, clock())
    row = store.last_activity()
    assert row["kind"] == "external" and row["ts"] == T0 - 2 * DAY


def test_host_state_beats_cups_history_on_first_sample(tmp_path):
    tr, store, clock = make(tmp_path)
    (tmp_path / "state").write_text(f"174 {T0 - 3 * DAY}\n")
    tr._newest_document = T0 - 2 * DAY
    tr.record_counter(174, clock())
    assert store.last_activity()["kind"] == "baseline" and store.last_activity()["ts"] == T0 - 3 * DAY


def test_no_documents_in_cups_means_no_newest(tmp_path):
    tr, _, _ = make(tmp_path)
    assert tr.newest_document_ts([]) is None
    assert tr.newest_document_ts([_doc(1, "nozzle-check.ps", 1000)]) is None
