"""Presets for Photo Sheets: a saved set of choices (photo size, paper, margins, layout, finishing, print options)
without the photos themselves. A few ship with the code (presets.yml); people can save their own, which live in the
database so every device sees them. Settings are checked on the way in, so a stored preset is always usable."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import yaml

from app.tools.photosheet import layout as lay

log = logging.getLogger(__name__)
MAX_USER_PRESETS = 50
MAX_NAME = 60
UNITS = ("mm", "cm", "in")
KEYS = {"photo", "unit", "paper", "orientation", "margin", "gap", "mode", "cols", "rows", "count", "align", "fit",
        "marks", "outline", "media_type", "quality", "resolution", "copies"}
MIGRATIONS = ["CREATE TABLE IF NOT EXISTS ps_presets (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);"]


class PresetError(ValueError):
    """A preset that can't be saved; the message is for the person saving it."""


def _num(d: dict, key: str, lo: float, hi: float, label: str) -> float:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise PresetError(f"{label} must be a number from {lo:g} to {hi:g}.")
    return float(v)


def _int(d: dict, key: str, lo: int, hi: int, label: str, default: int | None = None) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise PresetError(f"{label} must be a whole number from {lo} to {hi}.")
    return v


def clean_settings(raw) -> dict:
    """The settings of a preset, checked and reduced to what Photo Sheets understands. Lengths are in millimetres."""
    if not isinstance(raw, dict):
        raise PresetError("The settings are missing.")
    unknown = set(raw) - KEYS
    if unknown:
        raise PresetError(f"Unknown setting: {sorted(unknown)[0]}.")
    photo = raw.get("photo")
    if not isinstance(photo, dict):
        raise PresetError("The photo size is missing.")
    paper = raw.get("paper")
    if not isinstance(paper, dict):
        raise PresetError("The paper is missing.")
    out: dict = {
        "photo": {"width": _num(photo, "width", lay.MIN_PHOTO_MM, lay.MAX_PHOTO_MM, "The photo width"),
                  "height": _num(photo, "height", lay.MIN_PHOTO_MM, lay.MAX_PHOTO_MM, "The photo height")},
        "unit": raw.get("unit", "mm"),
        "paper": {"width": _num(paper, "width", lay.MIN_PAPER_MM, lay.MAX_PAPER_MM, "The paper width"),
                  "height": _num(paper, "height", lay.MIN_PAPER_MM, lay.MAX_PAPER_MM, "The paper height")},
        "orientation": raw.get("orientation", "auto"), "mode": raw.get("mode", "fill"), "align": raw.get("align", "center"),
        "fit": raw.get("fit", "fill"),
        "margin": _num(raw, "margin", 0, lay.MAX_MARGIN_MM, "The margin") if "margin" in raw else 5.0,
        "gap": _num(raw, "gap", 0, lay.MAX_GAP_MM, "The gap") if "gap" in raw else 2.0,
        "marks": raw.get("marks", True), "outline": raw.get("outline", False),
    }
    if out["unit"] not in UNITS:
        raise PresetError("The unit must be mm, cm or in.")
    if out["orientation"] not in ("auto", "portrait", "landscape"):
        raise PresetError("The sheet direction must be automatic, portrait or landscape.")
    if out["mode"] not in ("fill", "grid", "count"):
        raise PresetError("How many must be fill, grid or count.")
    if out["align"] not in ("center", "start"):
        raise PresetError("The placement must be centre or start.")
    if out["fit"] not in ("fill", "fit"):
        raise PresetError("The photo shape handling must be fill or fit.")
    for flag in ("marks", "outline"):
        if not isinstance(out[flag], bool):
            raise PresetError(f"{flag} must be true or false.")
    media = paper.get("media")
    if media is not None:
        if not isinstance(media, str) or not 0 < len(media) <= 120:
            raise PresetError("The paper name isn't valid.")
        out["paper"]["media"] = media
    label = paper.get("label")
    if isinstance(label, str) and label.strip():
        out["paper"]["label"] = label.strip()[:80]
    if out["mode"] == "grid":
        out["cols"], out["rows"] = _int(raw, "cols", 1, 99, "Columns"), _int(raw, "rows", 1, 99, "Rows")
    if out["mode"] == "count":
        out["count"] = _int(raw, "count", 1, lay.MAX_COPIES, "The number of photos")
    mt = raw.get("media_type")
    if mt not in (None, ""):
        if not isinstance(mt, str) or len(mt) > 80:
            raise PresetError("The paper type isn't valid.")
        out["media_type"] = mt
    if raw.get("quality") not in (None, ""):
        out["quality"] = _int(raw, "quality", 3, 5, "The print quality")
    res = raw.get("resolution")
    if res not in (None, []):
        if (not isinstance(res, list) or len(res) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or not 50 <= v <= 4800 for v in res)):
            raise PresetError("The resolution isn't valid.")
        out["resolution"] = res
    if "copies" in raw:
        out["copies"] = _int(raw, "copies", 1, 20, "Copies of the sheet")
    return out


def clean_name(raw) -> str:
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not name:
        raise PresetError("Give the preset a name.")
    if len(name) > MAX_NAME:
        raise PresetError(f"Use {MAX_NAME} characters or fewer.")
    return name


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "preset"


def load_builtin(path: Path) -> list[dict]:
    """The presets shipped with the code. A bad entry is skipped with a log line, never fatal."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        log.warning("photo presets file unreadable: %s", e)
        return []
    out, seen = [], set()
    for i, entry in enumerate((doc.get("presets") if isinstance(doc, dict) else None) or [], start=1):
        try:
            if not isinstance(entry, dict):
                raise PresetError("each preset must be a set of key: value lines")
            name = clean_name(entry.get("name"))
            pid = f"builtin-{slug(str(entry.get('id') or name))}"
            if pid in seen:
                raise PresetError("duplicate id")
            seen.add(pid)
            out.append({"id": pid, "name": name, "builtin": True, "settings": clean_settings(entry.get("settings"))})
        except PresetError as e:
            log.warning("photo preset %d skipped: %s", i, e)
    return out


class UserPresets:
    """Presets people saved, in the database."""

    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock

    def list(self) -> list[dict]:
        rows = self.db.query("SELECT id, name, data FROM ps_presets ORDER BY created_at, id")
        out = []
        for r in rows:
            try:
                out.append({"id": r["id"], "name": r["name"], "builtin": False, "settings": json.loads(r["data"])})
            except ValueError:
                continue  # a damaged row is ignored, not shown
        return out

    def save(self, name_raw, settings_raw) -> dict:
        name, settings = clean_name(name_raw), clean_settings(settings_raw)
        existing = next((p for p in self.list() if p["name"].lower() == name.lower()), None)
        if existing is None and self.db.one("SELECT COUNT(*) AS n FROM ps_presets")["n"] >= MAX_USER_PRESETS:
            raise PresetError(f"You can keep up to {MAX_USER_PRESETS} presets. Delete one first.")
        data = json.dumps(settings, separators=(",", ":"))
        if existing:  # same name: replaced
            self.db.execute("UPDATE ps_presets SET name=?, data=? WHERE id=?", (name, data, existing["id"]))
            return {"id": existing["id"], "name": name, "builtin": False, "settings": settings, "replaced": True}
        ids = {r["id"] for r in self.db.query("SELECT id FROM ps_presets")}
        base, n = f"user-{slug(name)}", 1
        pid = base
        while pid in ids:
            n += 1
            pid = f"{base}-{n}"
        self.db.execute("INSERT INTO ps_presets(id, name, data, created_at) VALUES(?,?,?,?)", (pid, name, data, self.clock()))
        return {"id": pid, "name": name, "builtin": False, "settings": settings, "replaced": False}

    def delete(self, pid: str) -> bool:
        if self.db.one("SELECT 1 AS x FROM ps_presets WHERE id=?", (pid,)) is None:
            return False
        self.db.execute("DELETE FROM ps_presets WHERE id=?", (pid,))
        return True


def to_yaml(preset: dict) -> str:
    """A preset as the text to paste into presets.yml (to ship it with the code)."""
    entry = {"id": slug(preset["name"]), "name": preset["name"], "settings": preset["settings"]}
    return yaml.safe_dump({"presets": [entry]}, sort_keys=False, allow_unicode=True, default_flow_style=False)
