"""Rate-limit rule as a pure function, so tools can feed it timestamps from
wherever they keep them (the maintenance tool uses its database, which makes
the limit survive restarts)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RateRule:
    min_interval: float  # seconds required between two events
    max_events: int  # at most this many events ...
    window: float = 3600.0  # ... within this many seconds


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    retry_after: int = 0  # seconds until the next event would be allowed
    reason: str = ""


def check(rule: RateRule, event_times: Iterable[float], now: float) -> Verdict:
    times = sorted(t for t in event_times if t <= now)
    retry = 0.0
    reason = ""
    if times and now - times[-1] < rule.min_interval:
        retry = rule.min_interval - (now - times[-1])
        reason = "Please wait a moment between prints."
    recent = [t for t in times if now - t < rule.window]
    if rule.max_events > 0 and len(recent) >= rule.max_events:
        wait = recent[len(recent) - rule.max_events] + rule.window - now
        if wait > retry:
            retry = wait
            reason = f"Limit reached: at most {rule.max_events} per {int(rule.window // 60)} minutes."
    if retry > 0:
        return Verdict(False, int(retry) + 1, reason)
    return Verdict(True)
