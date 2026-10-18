"""Presets for the Photo Collage Sheet: a saved set of choices (paper, layout, finishing, print options) without the photos.
A few can ship with the code (presets.yml); people can save their own, which live in the database so every device sees them.
The storage and the YAML helpers are shared with the Passport Sheet Maker."""
from __future__ import annotations

from app.tools.photosheet import layout as lay
from app.tools.photosheet.presets import PresetError, _int, _num, clean_name, load_builtin, slug, to_yaml, UserPresets  # noqa: F401 (re-exported)

TABLE = "cl_presets"
MIGRATIONS = ["CREATE TABLE IF NOT EXISTS cl_presets (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);"]
KEYS = {"unit", "paper", "orientation", "area", "margin", "gap", "fit", "outline", "media_type", "quality", "resolution", "copies"}


def clean_settings(raw) -> dict:
    """The settings of a preset, checked and reduced to what the collage understands. Lengths are in millimetres."""
    if not isinstance(raw, dict):
        raise PresetError("The settings are missing.")
    unknown = set(raw) - KEYS
    if unknown:
        raise PresetError(f"Unknown setting: {sorted(unknown)[0]}.")
    paper = raw.get("paper")
    if not isinstance(paper, dict):
        raise PresetError("The paper is missing.")
    out: dict = {
        "unit": raw.get("unit", "mm"),
        "paper": {"width": _num(paper, "width", lay.MIN_PAPER_MM, lay.MAX_PAPER_MM, "The paper width"),
                  "height": _num(paper, "height", lay.MIN_PAPER_MM, lay.MAX_PAPER_MM, "The paper height")},
        "orientation": raw.get("orientation", "auto"), "area": raw.get("area", "full"), "fit": raw.get("fit", "fill"),
        "margin": _num(raw, "margin", 0, lay.MAX_MARGIN_MM, "The margin") if "margin" in raw else 5.0,
        "gap": _num(raw, "gap", 0, lay.MAX_GAP_MM, "The gap") if "gap" in raw else 3.0,
        "outline": raw.get("outline", False),
    }
    if out["unit"] not in ("mm", "cm", "in"):
        raise PresetError("The unit must be mm, cm or in.")
    if out["orientation"] not in ("auto", "portrait", "landscape"):
        raise PresetError("The sheet direction must be automatic, portrait or landscape.")
    if out["area"] not in lay.AREAS:
        raise PresetError("The part of the page must be full, top or bottom.")
    if out["area"] != "full" and out["orientation"] == "landscape":
        raise PresetError("A half page can't be combined with a landscape sheet.")
    if out["fit"] not in ("fill", "fit"):
        raise PresetError("The photo shape handling must be fill or fit.")
    if not isinstance(out["outline"], bool):
        raise PresetError("outline must be true or false.")
    if paper.get("media") is not None:
        if not isinstance(paper["media"], str) or not 0 < len(paper["media"]) <= 120:
            raise PresetError("The paper name isn't valid.")
        out["paper"]["media"] = paper["media"]
    if isinstance(paper.get("label"), str) and paper["label"].strip():
        out["paper"]["label"] = paper["label"].strip()[:80]
    mt = raw.get("media_type")
    if mt not in (None, ""):
        if not isinstance(mt, str) or len(mt) > 80:
            raise PresetError("The paper type isn't valid.")
        out["media_type"] = mt
    if raw.get("quality") not in (None, ""):
        out["quality"] = _int(raw, "quality", 3, 5, "The print quality")
    res = raw.get("resolution")
    if res not in (None, []):
        if not isinstance(res, list) or len(res) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or not 50 <= v <= 4800 for v in res):
            raise PresetError("The resolution isn't valid.")
        out["resolution"] = res
    if "copies" in raw:
        out["copies"] = _int(raw, "copies", 1, 20, "Copies of the sheet")
    return out
