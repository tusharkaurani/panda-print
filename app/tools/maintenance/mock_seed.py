"""MOCK=1: give a fresh database a believable past so the dashboard isn't empty.

The scripted history lives in app.printer.mock (the fake CUPS has the same jobs).
Host-timer checks are not inserted here: the tracker imports them from the fake
CUPS job list on its first poll, exactly as it would on the real Pi.
"""
from __future__ import annotations

from app.printer.mock import FINAL_COUNT, history_events
from app.tools.maintenance.store import Store


def seed(store: Store, now: float) -> None:
    if store.activity_count() or store.get("last_count") is not None:
        return
    events = history_events(now)
    first_ts = events[0]["ts"]
    store.put("first_seen_ts", first_ts)
    store.put("last_count", FINAL_COUNT)
    store.put("last_count_ts", now)
    store.add_activity(first_ts - 3600, 0, events[0]["count_after"] - events[0]["pages"], "baseline",
                       note="Mock data")
    previous = first_ts - 3600
    for ev in events:
        kind = "external" if ev["kind"] == "external" else "nozzle"
        store.add_activity(ev["ts"], ev["pages"], ev["count_after"], kind, since_ts=previous)
        previous = ev["ts"]
        if ev["kind"] == "manual":
            store.add_check(ev["ts"], "manual", "submitted", ev["job_id"], claimed_ts=ev["ts"])
