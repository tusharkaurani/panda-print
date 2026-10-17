"""Per-printer automation settings for nozzle checks.

These used to be fixed values (environment variables, applied to every printer). Now each printer can
override them from the Nozzle Check tab; anything it does not override follows the defaults, which are
those same environment values (7 idle days, a counter read every 5 minutes, ...). So a newly added
printer starts with exactly today's behaviour.

Pure functions only; storage lives in store.py.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields

from app.settings import Settings

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ValidationError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


@dataclass(frozen=True)
class Automation:
    idle_days: float  # idle days before a nozzle check is due (the host timer's rule)
    check_minutes: float  # how often the page counter is read
    job_pattern: str  # CUPS job names containing this are the timer's nozzle checks
    attribution_minutes: float  # how long a check can wait for the counter to move
    enabled: bool  # print the nozzle check automatically (off unless asked for)
    run_at: str  # "HH:MM": never print before this time of day

    @property
    def attribution_seconds(self) -> float:
        return self.attribution_minutes * 60

    @property
    def check_seconds(self) -> float:
        return self.check_minutes * 60

    def to_dict(self) -> dict:
        return asdict(self)


KEYS = [f.name for f in fields(Automation)]

# (minimum, maximum) for the numeric fields
BOUNDS = {"idle_days": (0.5, 365.0), "check_minutes": (1.0, 1440.0), "attribution_minutes": (1.0, 1440.0)}
LABELS = {"idle_days": "Idle days", "check_minutes": "Counter check interval",
          "job_pattern": "Job name", "attribution_minutes": "Matching window"}


def defaults(settings: Settings) -> Automation:
    """What a printer uses until it is customised: the current (environment) settings."""
    return Automation(
        idle_days=settings.idle_days,
        check_minutes=settings.poll_interval / 60,
        job_pattern=settings.nozzle_job_pattern,
        attribution_minutes=settings.attribution_window / 60,
        enabled=settings.auto_check,
        run_at=settings.check_time if _HHMM.match(settings.check_time) else "10:00",
    )


def effective(base: Automation, overrides: dict) -> Automation:
    return Automation(**{k: overrides.get(k, getattr(base, k)) for k in KEYS})


def validate(payload: dict) -> tuple[dict, dict[str, str]]:
    """Check the fields present in `payload`. Returns (clean values, {field: message})."""
    clean: dict = {}
    errors: dict[str, str] = {}
    for key in KEYS:
        if key not in payload:
            continue
        raw = payload[key]
        if key == "enabled":
            if isinstance(raw, bool):
                clean[key] = raw
            elif str(raw).strip().lower() in ("true", "1", "on", "yes"):
                clean[key] = True
            elif str(raw).strip().lower() in ("false", "0", "off", "no"):
                clean[key] = False
            else:
                errors[key] = "Choose on or off."
            continue
        if key == "run_at":
            text = str(raw if raw is not None else "").strip()
            if _HHMM.match(text):
                clean[key] = text
            else:
                errors[key] = "Enter a time such as 10:00."
            continue
        if key == "job_pattern":
            text = str(raw if raw is not None else "").strip()
            if not text:
                errors[key] = "Enter part of the job name the timer's check page has (for example nozzle-check)."
            elif len(text) > 80 or any(ord(ch) < 32 for ch in text):
                errors[key] = "Use up to 80 plain characters."
            else:
                clean[key] = text
            continue
        lo, hi = BOUNDS[key]
        try:
            number = float(raw)
            if number != number:  # NaN
                raise ValueError
        except (TypeError, ValueError):
            errors[key] = "Enter a number."
            continue
        if not lo <= number <= hi:
            errors[key] = f"Use a value between {lo:g} and {hi:g}."
            continue
        clean[key] = round(number, 2)
    return clean, errors


def overrides_from(clean: dict, base: Automation) -> dict:
    """Keep only what differs from the defaults, so untouched settings keep following them."""
    return {k: v for k, v in clean.items() if v != getattr(base, k)}


def poll_due(last_poll: float | None, check_minutes: float, now: float) -> bool:
    """Is it time to read this printer's counter again?"""
    return last_poll is None or now - last_poll >= check_minutes * 60
