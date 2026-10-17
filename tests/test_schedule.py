from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.tools.maintenance import schedule
from app.tools.maintenance.schedule import Plan, plan

UTC = timezone.utc
DAY = 86400


def at(y, mo, d, h, mi=0, tz=UTC):
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


def make_plan(**kw):
    base = dict(enabled=True, now=at(2027, 1, 20, 12), last_print_ts=at(2027, 1, 10, 12), idle_days=7.0,
                run_at="10:00", tz=UTC, counter_ok=True, last_app_check_ts=None, last_failure_ts=None)
    return plan(**{**base, **kw})


def test_off_does_nothing():
    assert make_plan(enabled=False) == Plan("off")


def test_prints_once_the_printer_has_been_idle_long_enough():
    assert make_plan().action == "print"  # idle 10 days, limit 7, and it is past 10:00
    assert make_plan(now=at(2027, 1, 17, 11, 59)).action == "wait"  # 6.99 days
    assert make_plan(now=at(2027, 1, 17, 12)).action == "print"     # exactly 7 days, like the host script


def test_waiting_says_when():
    p = make_plan(now=at(2027, 1, 12, 9))
    assert p.action == "wait" and p.next_run_at == at(2027, 1, 17, 12)


def test_quiet_hours_hold_the_print_until_the_set_time():
    due = at(2027, 1, 17, 3)  # idle limit is reached at 03:00
    kw = dict(last_print_ts=due - 7 * DAY)
    early = make_plan(now=at(2027, 1, 17, 3, 30), **kw)
    assert early.action == "wait" and early.next_run_at == at(2027, 1, 17, 10)
    assert make_plan(now=at(2027, 1, 17, 9, 59), **kw).action == "wait"
    assert make_plan(now=at(2027, 1, 17, 10, 0), **kw).action == "print"


def test_a_check_that_falls_due_after_the_set_time_is_not_pushed_to_tomorrow():
    due = at(2027, 1, 17, 14)
    assert make_plan(now=due, last_print_ts=due - 7 * DAY).action == "print"


def test_quiet_hours_follow_the_configured_time_zone():
    ist = ZoneInfo("Asia/Kolkata")
    due = at(2027, 1, 17, 3, 0)  # 03:00 UTC is 08:30 in Kolkata
    kw = dict(last_print_ts=due - 7 * DAY, now=due, run_at="10:00")
    p = make_plan(tz=ist, **kw)
    assert p.action == "wait" and p.next_run_at == at(2027, 1, 17, 10, 0, ist)
    assert make_plan(tz=UTC, **kw).action == "wait" and make_plan(tz=UTC, **kw).next_run_at == at(2027, 1, 17, 10)
    assert make_plan(tz=ist, now=at(2027, 1, 17, 10, 0, ist), last_print_ts=due - 7 * DAY, run_at="10:00").action == "print"


def test_does_nothing_when_the_counter_cannot_be_read():
    p = make_plan(counter_ok=False)
    assert p.action == "blocked" and "page count" in p.reason  # idle time unknown: don't guess


def test_waits_an_hour_after_a_failed_attempt():
    now = at(2027, 1, 20, 12)
    p = make_plan(last_failure_ts=now - 600)
    assert p.action == "blocked" and p.next_run_at == now - 600 + 3600
    assert make_plan(last_failure_ts=now - 3601).action == "print"


def test_does_not_send_a_second_check_while_the_first_may_still_be_printing():
    now = at(2027, 1, 20, 12)
    assert make_plan(last_app_check_ts=now - 3600).action == "blocked"
    assert make_plan(last_app_check_ts=now - 7 * 3600).action == "print"


def test_no_baseline_yet_means_wait_for_the_first_reading():
    p = make_plan(last_print_ts=None)
    assert p.action == "blocked" and "first" in p.reason


def test_time_zone_names():
    assert str(schedule.tz_for("Europe/London")) == "Europe/London"
    assert str(schedule.tz_for(":Europe/London")) == "Europe/London"  # the form libc accepts too
    assert str(schedule.tz_for("  Asia/Kolkata ")) == "Asia/Kolkata"
    assert schedule.tz_for("Not/AZone") is UTC
    assert schedule.tz_for("") is not None  # the machine's own


def test_an_unusable_tz_is_called_out_in_the_label_not_hidden():
    assert schedule.tz_label("Europe/London") == "Europe/London"
    assert schedule.tz_label(":Europe/London") == "Europe/London"
    bad = schedule.tz_label("Mars/Olympus")
    assert bad.startswith("UTC") and "Mars/Olympus" in bad and "isn't a known" in bad
