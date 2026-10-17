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
    """The clock the 'don't print before' time refers to: TZ if set (and valid), else this machine's."""
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            log.warning("unknown time zone %r (TZ); using UTC", name)
            return timezone.utc
    return datetime.now().astimezone().tzinfo or timezone.utc


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
         counter_ok: bool, last_app_check_ts: float | None, last_failure_ts: float | None) -> Plan:
    if not enabled:
        return Plan("off")
    if last_print_ts is None:
        return Plan("blocked", "Waiting for the first page-counter reading.")
    next_run = earliest_run(last_print_ts + idle_days * DAY, now, run_at, tz)
    if now < next_run:
        return Plan("wait", next_run_at=next_run)
    if not counter_ok:
        return Plan("blocked", "The page counter can't be read right now, so the printer's idle time isn't known.", next_run)
    if last_failure_ts is not None and now - last_failure_ts < RETRY_AFTER_FAILURE:
        return Plan("blocked", "The last attempt failed; trying again in a while.", last_failure_ts + RETRY_AFTER_FAILURE)
    if last_app_check_ts is not None and now - last_app_check_ts < COOLDOWN:
        return Plan("blocked", "A check was sent a short while ago; waiting to see it print.", last_app_check_ts + COOLDOWN)
    return Plan("print", next_run_at=next_run)
