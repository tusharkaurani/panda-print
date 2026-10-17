import pytest

from app.settings import Settings
from app.tools.maintenance import automation as auto
from conftest import PID, csrf_headers
from test_tracker import DAY, T0, make


# ------------------------------------------------------------------ the pure rules

def test_defaults_are_todays_environment_values():
    d = auto.defaults(Settings())
    assert (d.idle_days, d.check_minutes, d.job_pattern, d.attribution_minutes) == (7.0, 5.0, "nozzle-check", 30.0)
    assert (d.enabled, d.run_at) == (False, "10:00")  # printing is opt-in; quiet until 10:00
    custom = auto.defaults(Settings(idle_days=10, poll_interval=600, nozzle_job_pattern="ncheck", attribution_window=900,
                                    auto_check=True, check_time="08:30"))
    assert (custom.idle_days, custom.check_minutes, custom.job_pattern, custom.attribution_minutes) == (10, 10, "ncheck", 15)
    assert (custom.enabled, custom.run_at) == (True, "08:30")
    assert auto.defaults(Settings(check_time="soon")).run_at == "10:00"  # a bad environment value can't break the UI


def test_validate_accepts_good_values_and_reports_each_bad_one():
    clean, errors = auto.validate({"idle_days": "3.5", "check_minutes": 10, "job_pattern": "  check  ",
                                   "attribution_minutes": "45", "ignored": "x"})
    assert errors == {} and clean == {"idle_days": 3.5, "check_minutes": 10.0, "job_pattern": "check",
                                      "attribution_minutes": 45.0}
    _, errors = auto.validate({"idle_days": "abc", "check_minutes": 0, "job_pattern": "  ", "attribution_minutes": 5000})
    assert set(errors) == {"idle_days", "check_minutes", "job_pattern", "attribution_minutes"}
    assert "between" in errors["check_minutes"] and "number" in errors["idle_days"]
    assert auto.validate({"idle_days": float("nan")})[1]["idle_days"]  # NaN is not a number you can use
    assert auto.validate({}) == ({}, {})  # only the fields sent are checked


@pytest.mark.parametrize("raw,expected", [(True, True), ("true", True), ("on", True), ("1", True),
                                          (False, False), ("off", False), ("false", False), ("0", False)])
def test_enabled_accepts_the_usual_spellings(raw, expected):
    assert auto.validate({"enabled": raw}) == ({"enabled": expected}, {})


def test_enabled_and_time_are_validated():
    assert "on or off" in auto.validate({"enabled": "maybe"})[1]["enabled"]
    assert auto.validate({"run_at": "07:05"}) == ({"run_at": "07:05"}, {})
    for bad in ("7:05", "24:00", "10:60", "ten", "", None):
        assert "time" in auto.validate({"run_at": bad})[1]["run_at"], bad


def test_only_differences_from_the_defaults_are_kept():
    base = auto.defaults(Settings())
    assert auto.overrides_from({"idle_days": 7.0, "check_minutes": 10.0}, base) == {"check_minutes": 10.0}
    eff = auto.effective(base, {"idle_days": 3.0})
    assert eff.idle_days == 3.0 and eff.check_minutes == 5.0  # the rest follow the defaults


def test_poll_due():
    assert auto.poll_due(None, 5, 1000.0)
    assert not auto.poll_due(1000.0, 5, 1000.0 + 299)
    assert auto.poll_due(1000.0, 5, 1000.0 + 300)
    assert auto.poll_due(1000.0, 1, 1000.0 + 60)  # a shorter interval is honoured


# ------------------------------------------------------------------ the tracker uses them

def test_job_pattern_is_per_printer(tmp_path):
    tr, store, _ = make(tmp_path)
    jobs = [{"job-id": [2], "document-name-supplied": ["nozzle-check.ps"], "job-state": [9],
             "time-at-creation": [1000], "time-at-completed": [1030]}]
    store.set_automation_overrides({"job_pattern": "zzz"})
    assert tr.import_host_checks(jobs) == 0  # not what this printer's timer calls its page
    store.set_automation_overrides({"job_pattern": "CHECK.PS"})  # case-insensitive substring
    assert tr.import_host_checks(jobs) == 1


def test_matching_window_is_per_printer(tmp_path):
    tr, store, clock = make(tmp_path)
    tr.record_counter(174, clock())
    store.add_check(clock(), "manual", "submitted", job_id=1)
    clock.advance(600)  # ten minutes later the counter moves
    store.set_automation_overrides({"attribution_minutes": 5})
    tr.record_counter(175, clock())
    assert store.last_activity()["kind"] == "external"  # too late to belong to the check
    tr2, store2, clock2 = make(tmp_path)
    tr2.record_counter(174, clock2())
    store2.add_check(clock2(), "manual", "submitted", job_id=1)
    clock2.advance(600)
    tr2.record_counter(175, clock2())  # default window is 30 minutes
    assert store2.last_activity()["kind"] == "nozzle"


# ------------------------------------------------------------------ the API

def url(pid=PID):
    return f"/api/maintenance/{pid}/automation"


def test_new_printer_starts_on_the_defaults(client):
    d = client.get(url()).get_json()["data"]
    assert d["values"] == d["defaults"] == {"idle_days": 7.0, "check_minutes": 5.0, "job_pattern": "nozzle-check",
                                            "attribution_minutes": 30.0, "enabled": False, "run_at": "10:00",
                                            "rotate": False, "plain_every": 0}
    assert d["customised"] == []


def test_changing_settings_changes_behaviour(client):
    h = csrf_headers(client)
    before = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert before["idle_days_threshold"] == 7.0
    r = client.put(url(), json={"idle_days": 3, "check_minutes": 15}, headers=h)
    assert r.status_code == 200 and r.get_json()["data"]["customised"] == ["check_minutes", "idle_days"]
    after = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert after["idle_days_threshold"] == 3.0 and after["tracking"]["poll_interval"] == 900.0
    # 2.6 idle days against a 3 day limit is almost due, not 4.4 days away
    assert after["next_check"]["days_until"] == pytest.approx(0.4, abs=0.05)


def test_setting_a_value_back_to_the_default_stops_overriding_it(client):
    h = csrf_headers(client)
    client.put(url(), json={"idle_days": 3}, headers=h)
    d = client.put(url(), json={"idle_days": 7}, headers=h).get_json()["data"]
    assert d["customised"] == []


def test_invalid_settings_are_rejected_with_fields(client):
    r = client.put(url(), json={"idle_days": "soon", "job_pattern": ""}, headers=csrf_headers(client))
    assert r.status_code == 400
    assert set(r.get_json()["error"]["fields"]) == {"idle_days", "job_pattern"}
    assert client.get(url()).get_json()["data"]["customised"] == []  # nothing was saved


def test_reset_restores_the_defaults(client):
    h = csrf_headers(client)
    client.put(url(), json={"idle_days": 2, "job_pattern": "x"}, headers=h)
    d = client.delete(url(), headers=h).get_json()["data"]
    assert d["customised"] == [] and d["values"]["idle_days"] == 7.0


def test_changes_need_csrf_and_a_known_printer(client):
    assert client.put(url(), json={"idle_days": 3}).status_code == 403
    assert client.delete(url()).status_code == 403
    h = csrf_headers(client)
    assert client.put(url("ghost"), json={}, headers=h).status_code == 404
    assert client.get(url("ghost")).status_code == 404


def test_settings_are_per_printer_and_die_with_the_printer(client):
    h = csrf_headers(client)
    client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h)
    client.put(url(), json={"idle_days": 2}, headers=h)
    assert client.get(url("hall")).get_json()["data"]["values"]["idle_days"] == 7.0  # Hall keeps the default
    client.put(url("hall"), json={"idle_days": 10}, headers=h)
    ctx = client.application.extensions["panda"]["ctx"]
    assert ctx.db.one("SELECT COUNT(*) AS n FROM mt_automation")["n"] == 2
    client.delete("/api/printers/hall", headers=h)
    assert ctx.db.one("SELECT COUNT(*) AS n FROM mt_automation WHERE printer_id='hall'")["n"] == 0
    assert client.get(url()).get_json()["data"]["values"]["idle_days"] == 2.0


# ------------------------------------------------------------------ the on/off switch is not a "setting"

def test_reset_to_defaults_leaves_the_switch_alone(client):
    h = csrf_headers(client)
    client.put(url(), json={"enabled": True}, headers=h)
    client.put(url(), json={"idle_days": 3, "run_at": "07:30"}, headers=h)
    d = client.delete(url(), headers=h).get_json()["data"]
    assert d["values"]["idle_days"] == 7.0 and d["values"]["run_at"] == "10:00"  # settings back to the defaults
    assert d["values"]["enabled"] is True and d["customised"] == []              # the switch stays on
    assert client.get(url()).get_json()["data"]["values"]["enabled"] is True


def test_reset_leaves_the_switch_off_too(client):
    h = csrf_headers(client)
    client.put(url(), json={"idle_days": 3}, headers=h)
    assert client.delete(url(), headers=h).get_json()["data"]["values"]["enabled"] is False


def test_flipping_the_switch_does_not_count_as_a_changed_setting(client):
    d = client.put(url(), json={"enabled": True}, headers=csrf_headers(client)).get_json()["data"]
    assert d["values"]["enabled"] is True and d["customised"] == []  # so "Reset to defaults" stays disabled


# ------------------------------------------------------------------ times are in the container's time zone

def test_time_labels_use_the_configured_zone():
    from app.tools.maintenance import schedule
    ts = 1_800_000_000  # Fri 15 Jan 2027, 08:00 UTC
    assert schedule.format_local(ts, "UTC") == "Fri 15 Jan, 08:00 UTC"
    assert schedule.format_local(ts, "Asia/Kolkata") == "Fri 15 Jan, 13:30 IST"
    assert schedule.format_local(ts, "America/New_York") == "Fri 15 Jan, 03:00 EST"
    assert schedule.tz_label("Europe/London") == "Europe/London"
    assert schedule.tz_label("Not/AZone").startswith("UTC (TZ 'Not/AZone'")  # a bad TZ falls back to UTC, and says so


def test_next_check_is_shown_in_the_container_zone(make_app):
    app, _ = make_app(timezone="Asia/Kolkata")
    c = app.test_client()
    h = csrf_headers(c)
    c.put(url(), json={"enabled": True, "run_at": "00:00", "idle_days": 7}, headers=h)
    a = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["automation"]
    assert a["timezone"] == "Asia/Kolkata" and a["next_run_label"].endswith("IST")
    assert c.get(url()).get_json()["data"]["timezone"] == "Asia/Kolkata"


@pytest.mark.parametrize("tz,expected", [("Asia/Kolkata", "print"), ("UTC", "wait")])
def test_quiet_hours_use_the_container_zone_not_utc(make_app, tz, expected):
    """08:00 UTC is 13:30 in Kolkata: past a 10:00 limit there, but before it in UTC."""
    app, _ = make_app(timezone=tz, print_cooldown=0)
    c = app.test_client()
    c.put(url(), json={"enabled": True, "idle_days": 2, "run_at": "10:00"}, headers=csrf_headers(c))
    a = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["automation"]
    assert a["state"] == expected, a


def test_rotate_and_plain_every_are_validated():
    assert auto.validate({"rotate": "on", "plain_every": "3"}) == ({"rotate": True, "plain_every": 3}, {})
    for bad in ("-1", "51", "2.5", "often", None):
        assert "whole number" in auto.validate({"plain_every": bad})[1]["plain_every"], bad
    assert "on or off" in auto.validate({"rotate": "sometimes"})[1]["rotate"]


def test_reset_keeps_what_to_print_like_it_keeps_the_switch(client):
    h = csrf_headers(client)
    client.put(url(), json={"rotate": True, "plain_every": 4, "idle_days": 3}, headers=h)
    d = client.delete(url(), headers=h).get_json()["data"]
    assert d["values"]["rotate"] is True                         # a choice, not a setting
    assert d["values"]["plain_every"] == 0 and d["values"]["idle_days"] == 7.0  # settings are back to the defaults
    assert d["customised"] == []
    assert client.put(url(), json={"rotate": False}, headers=h).get_json()["data"]["customised"] == []
