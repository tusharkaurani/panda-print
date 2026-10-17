"""Runtime settings, all overridable via environment variables.

Nothing in here is secret; the only secret (the CSRF signing key) is read from
SECRET_KEY or generated into DATA_DIR on first start.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

TRUE = {"1", "true", "yes", "on"}


def _bool(v: str | None, default: bool = False) -> bool:
    return default if v is None or v == "" else v.strip().lower() in TRUE


def _num(v: str | None, default: float) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        raise SystemExit(f"Invalid number in environment: {v!r}")


@dataclass(frozen=True)
class Settings:
    # --- server
    host: str = "0.0.0.0"
    port: int = 8095
    mock: bool = False
    # MOCK_FAIL=printer,snmp,cups makes those mock sources fail, to see the error cards
    mock_fail: tuple[str, ...] = ()
    # mock data is regenerated relative to "now" on every start, so by default MOCK=1
    # keeps its database in memory; MOCK_PERSIST=1 stores it in DATA_DIR like normal
    mock_persist: bool = False
    # MOCK_SETUP=1 starts mock mode with no printers, to try the setup wizard
    mock_setup: bool = False
    log_level: str = "INFO"
    data_dir: Path = Path("data")
    config_dir: Path = Path("config")
    secret_key: str | None = None
    # extra origins (comma separated in env) accepted for state-changing POSTs
    trusted_origins: tuple[str, ...] = ()

    # --- printer defaults. Printers are added in the UI (setup wizard / Printers page);
    # these only prefill the forms. If PRINTER_IP is set and no printer exists yet, one is
    # added from these values at start-up (handy for unattended installs).
    printer_ip: str = ""
    printer_ipp_url: str = ""
    snmp_community: str = "public"
    snmp_port: int = 161
    snmp_counter_oid: str = "1.3.6.1.2.1.43.10.2.1.4.1.1"  # prtMarkerLifeCount
    net_timeout: float = 5.0
    cups_url: str = "http://127.0.0.1:631"
    cups_queue: str = ""

    # --- nozzle check policy
    idle_days: float = 7.0
    poll_interval: float = 300.0
    host_state_file: Path = Path("/host-state/state")
    # CUPS jobs whose name matches this (case-insensitive substring) and that
    # were not submitted by this app are treated as the host timer's checks.
    nozzle_job_pattern: str = "nozzle-check"
    # how long a submitted check can wait for the counter to move before we
    # stop trying to match it with a counter change
    attribution_window: float = 1800.0
    print_cooldown: float = 120.0
    print_max_per_hour: int = 5

    recent_jobs: int = 10

    @property
    def db_path(self) -> Path:
        return self.data_dir / "panda-print.db"

    @property
    def links_file(self) -> Path:
        return self.config_dir / "links.yml"

    @property
    def logos_dir(self) -> Path:
        return self.config_dir / "logos"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        d = cls()
        return cls(
            host=e.get("HOST", d.host),
            port=int(_num(e.get("PORT"), d.port)),
            mock=_bool(e.get("MOCK")),
            mock_persist=_bool(e.get("MOCK_PERSIST")),
            mock_setup=_bool(e.get("MOCK_SETUP")),
            mock_fail=tuple(x.strip() for x in e.get("MOCK_FAIL", "").split(",") if x.strip()),
            log_level=e.get("LOG_LEVEL", d.log_level).upper(),
            data_dir=Path(e.get("DATA_DIR", str(d.data_dir))),
            config_dir=Path(e.get("CONFIG_DIR", str(d.config_dir))),
            secret_key=e.get("SECRET_KEY") or None,
            trusted_origins=tuple(
                o.strip().rstrip("/") for o in e.get("TRUSTED_ORIGINS", "").split(",") if o.strip()
            ),
            printer_ip=e.get("PRINTER_IP", "").strip(),
            printer_ipp_url=e.get("PRINTER_IPP_URL", ""),
            snmp_community=e.get("SNMP_COMMUNITY", d.snmp_community),
            snmp_port=int(_num(e.get("SNMP_PORT"), d.snmp_port)),
            snmp_counter_oid=e.get("SNMP_COUNTER_OID", d.snmp_counter_oid),
            net_timeout=_num(e.get("NET_TIMEOUT"), d.net_timeout),
            cups_url=e.get("CUPS_URL", d.cups_url).rstrip("/"),
            cups_queue=e.get("CUPS_QUEUE", "").strip(),
            idle_days=_num(e.get("IDLE_DAYS"), d.idle_days),
            poll_interval=_num(e.get("POLL_INTERVAL"), d.poll_interval),
            host_state_file=Path(e.get("HOST_STATE_FILE", str(d.host_state_file))),
            nozzle_job_pattern=e.get("NOZZLE_JOB_PATTERN", d.nozzle_job_pattern),
            attribution_window=_num(e.get("ATTRIBUTION_WINDOW"), d.attribution_window),
            print_cooldown=_num(e.get("PRINT_COOLDOWN"), d.print_cooldown),
            print_max_per_hour=int(_num(e.get("PRINT_MAX_PER_HOUR"), d.print_max_per_hour)),
            recent_jobs=int(_num(e.get("RECENT_JOBS"), d.recent_jobs)),
        )
