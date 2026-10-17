"""The idle rule for nozzle checks, kept free of I/O so it can move into an
in-app scheduler later.

This is the logic of the host's nozzle-check.py (/opt/stacks/cups-maintenance):
print a check page only when the lifetime page counter hasn't moved for
`idle_days` days. The host keeps "<count> <epoch of last change>" in
/var/lib/printer-nozzle-check/state; `parse_host_state` reads that format and
`decide` is the host script's main() with the side effects removed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DAY = 86400.0


@dataclass(frozen=True)
class HostState:
    count: int
    changed_at: float  # when the host's daily check *noticed* the change (can lag by a day)


def parse_host_state(text: str) -> HostState | None:
    """Parse '<count> <epoch>' (the host script's state file). None if malformed."""
    try:
        count, when = text.split()
        return HostState(int(count), float(when))
    except (ValueError, AttributeError):
        return None


def idle_days(last_print_ts: float, now: float) -> float:
    return max(0.0, (now - last_print_ts) / DAY)


@dataclass(frozen=True)
class NextCheck:
    due_at: float
    days_until: float  # negative when overdue
    overdue: bool


def next_check(last_print_ts: float, now: float, threshold_days: float) -> NextCheck:
    """When the idle rule will next fire, given the last print of any kind
    (a nozzle check counts as a print: it restarts the idle clock)."""
    due_at = last_print_ts + threshold_days * DAY
    days = (due_at - now) / DAY
    return NextCheck(due_at, days, days <= 0)


@dataclass(frozen=True)
class Decision:
    action: Literal["record", "wait", "print"]
    idle_days: float
    state: tuple[int, float]  # the (count, since) state to persist


def decide(state: tuple[int, float] | None, count: int, now: float, threshold_days: float) -> Decision:
    """One run of the host's rule. `state` is the saved (count, time of last change)."""
    if state is None or count != state[0]:
        return Decision("record", 0.0, (count, now))  # activity seen, or first run
    idle = idle_days(state[1], now)
    if idle < threshold_days:
        return Decision("wait", idle, state)
    return Decision("print", idle, state)
