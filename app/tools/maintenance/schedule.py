"""When the app prints the automatic nozzle check. Pure functions: no I/O, no clock, easy to test.

The rule is the host timer's, moved here: print one check page once the printer has gone `idle_days`
without printing anything (a nozzle check counts as a print, so it restarts the clock). Added on top:

* quiet hours: never before `run_at` (local time of day), so a check doesn't fire at 3 a.m.;
* it only acts when the page counter can be read, so "idle" is known rather than guessed;
* after a failed attempt it waits an hour before trying again;
* it never sends a second check while an earlier one is still waiting to print.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

DAY = 86400.0
RETRY_AFTER_FAILURE = 3600.0  # seconds
COOLDOWN = 6 * 3600.0  # seconds between two checks the app sends (a stuck job must not pile up copies)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Plan:
    action: str  # "off" | "wait" | "blocked" | "print"
    reason: str = ""  # for "blocked": what is in the way, in words fit for the UI
    next_run_at: float | None = None  # when the app expects to print (or to try again)


def tz_for(name: str) -> tzinfo:
    """The clock the 'don't print before' time refers to: TZ if set (and valid), else this machine's.
    In a container that is whatever the TZ variable says, and UTC when it is not set."""
    name = (name or "").strip().lstrip(":")  # libc also accepts ":Europe/London"
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            log.warning("unknown time zone %r (TZ); using UTC", name)
            return timezone.utc
    return datetime.now().astimezone().tzinfo or timezone.utc


def tz_label(name: str) -> str:
    """A short name for the clock in use, for the UI: 'Europe/London', or 'UTC' / 'IST' for the machine's own.
    An unusable TZ says so rather than silently showing UTC."""
    clean = (name or "").strip().lstrip(":")
    if clean:
        try:
            return ZoneInfo(clean).key
        except Exception:
            return f"UTC (TZ '{clean}' isn't a known time zone)"
    return datetime.now().astimezone().tzname() or "UTC"


def format_local(ts: float, name: str) -> str:
    """'Sat 24 Oct, 10:00 IST': a moment in the container's own time zone."""
    return datetime.fromtimestamp(ts, tz_for(name)).strftime("%a %d %b, %H:%M %Z").strip()


def parse_hhmm(text: str) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else (10, 0)


def earliest_run(idle_due: float, now: float, run_at: str, tz: tzinfo) -> float:
    """The first moment at or after both `idle_due` and `now` that is not in quiet hours."""
    t0 = max(now, idle_due)
    local = datetime.fromtimestamp(t0, tz)
    hour, minute = parse_hhmm(run_at)
    start = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t0 if local >= start else start.timestamp()


def plan(*, enabled: bool, now: float, last_print_ts: float | None, idle_days: float, run_at: str, tz: tzinfo,
         counter_ok: bool, last_app_check_ts: float | None, last_failure_ts: float | None, can_print: bool = True) -> Plan:
    if not enabled:
        return Plan("off")
    if not can_print:  # printing goes through CUPS; without a queue there is nothing to print with
        return Plan("blocked", "CUPS isn't set up for this printer, so it can't print.")
    if last_print_ts is None:
        return Plan("blocked", "Waiting for the first page count.")
    next_run = earliest_run(last_print_ts + idle_days * DAY, now, run_at, tz)
    if now < next_run:
        return Plan("wait", next_run_at=next_run)
    if not counter_ok:
        return Plan("blocked", "Can't read the page count, so it isn't known whether the printer is idle. Is it switched on?", next_run)
    if last_failure_ts is not None and now - last_failure_ts < RETRY_AFTER_FAILURE:
        return Plan("blocked", "The last try failed. Trying again in an hour.", last_failure_ts + RETRY_AFTER_FAILURE)
    if last_app_check_ts is not None and now - last_app_check_ts < COOLDOWN:
        return Plan("blocked", "A page was sent a short while ago. Waiting for it to print.", last_app_check_ts + COOLDOWN)
    return Plan("print", next_run_at=next_run)
