"""Presets for the Label Sheet Maker: a saved set of choices (the sheet or label size, the look, the text settings, the
print options) without the labels' text or logo. A few ship with the code (presets.yml); people can save their own, which
live in the database so every device sees them. Settings are checked on the way in, so a stored preset is always usable.
The storage and the YAML helpers are shared with the Passport Sheet Maker."""
from __future__ import annotations

from app.tools.labels import fonts, sheet as sh
from app.tools.photosheet import layout as lay
from app.tools.photosheet.presets import PresetError, _int, _num, clean_name, load_builtin, slug, to_yaml, UserPresets  # noqa: F401 (re-exported)

TABLE = "lb_presets"
MIGRATIONS = ["CREATE TABLE IF NOT EXISTS lb_presets (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);"]
KEYS = {"unit", "kind", "pack", "grid", "paper", "size", "orientation", "area", "margin", "gap", "mode", "cols", "rows", "count", "align",
        "look", "text", "copies", "repeat", "outline", "marks", "media_type", "quality", "resolution", "sheet_copies"}
LOOK_KEYS = {"shape", "radius", "fill", "border", "border_width", "color", "family", "bold", "italic", "line_spacing"}
TEXT_KEYS = {"size", "align", "valign", "pad", "first_bold", "first_scale", "first_color"}


def _bool(d: dict, key: str, default: bool, label: str) -> bool:
    v = d.get(key, default)
    if not isinstance(v, bool):
        raise PresetError(f"{label} must be true or false.")
    return v


def _color(v, label: str) -> str | None:
    try:
        return sh.clean_color(v, label)
    except sh.SheetError as e:
        raise PresetError(str(e)) from None


def clean_settings(raw) -> dict:
    """The settings of a preset, checked and reduced to what the Label Sheet Maker understands. Lengths are in millimetres."""
    if not isinstance(raw, dict):
        raise PresetError("The settings are missing.")
    unknown = set(raw) - KEYS
    if unknown:
        raise PresetError(f"Unknown setting: {sorted(unknown)[0]}.")
    out: dict = {"unit": raw.get("unit", "mm"), "kind": raw.get("kind", "pack")}
    if out["unit"] not in ("mm", "cm", "in"):
        raise PresetError("The unit must be mm, cm or in.")
    if out["kind"] not in ("pack", "size"):
        raise PresetError("The labels must be on a label pack or laid out by size.")
    paper = raw.get("paper")
    if not isinstance(paper, dict):
        raise PresetError("The paper is missing.")
    out["paper"] = {"width": _num(paper, "width", lay.MIN_PAPER_MM, lay.MAX_PAPER_MM, "The paper width"),
                    "height": _num(paper, "height", lay.MIN_PAPER_MM, lay.MAX_PAPER_MM, "The paper height")}
    if paper.get("media") is not None:
        if not isinstance(paper["media"], str) or not 0 < len(paper["media"]) <= 120:
            raise PresetError("The paper name isn't valid.")
        out["paper"]["media"] = paper["media"]
    if isinstance(paper.get("label"), str) and paper["label"].strip():
        out["paper"]["label"] = paper["label"].strip()[:80]
    if out["kind"] == "pack":
        pack = raw.get("pack", "")
        if not isinstance(pack, str) or len(pack) > 40:
            raise PresetError("The label pack isn't valid.")
        out["pack"] = pack
        grid = raw.get("grid")
        if not pack:  # your own measurements
            if not isinstance(grid, dict):
                raise PresetError("The label sheet measurements are missing.")
            size = raw.get("size")
            if not isinstance(size, dict):
                raise PresetError("The label size is missing.")
            out["grid"] = {"cols": _int(grid, "cols", 1, 30, "Labels across"), "rows": _int(grid, "rows", 1, 30, "Labels down"),
                           "left": _num(grid, "left", 0, 500, "The left margin"), "top": _num(grid, "top", 0, 500, "The top margin"),
                           "gap_x": _num(grid, "gap_x", 0, 50, "The gap across"), "gap_y": _num(grid, "gap_y", 0, 50, "The gap down")}
            out["size"] = {"width": _num(size, "width", 5, 300, "The label width"), "height": _num(size, "height", 5, 300, "The label height")}
    else:
        size = raw.get("size")
        if not isinstance(size, dict):
            raise PresetError("The label size is missing.")
        out["size"] = {"width": _num(size, "width", lay.MIN_PHOTO_MM, lay.MAX_PHOTO_MM, "The label width"),
                       "height": _num(size, "height", lay.MIN_PHOTO_MM, lay.MAX_PHOTO_MM, "The label height")}
        out["orientation"] = raw.get("orientation", "auto")
        out["mode"] = raw.get("mode", "fill")
        out["align"] = raw.get("align", "center")
        out["margin"] = _num(raw, "margin", 0, lay.MAX_MARGIN_MM, "The margin") if "margin" in raw else 5.0
        out["gap"] = _num(raw, "gap", 0, lay.MAX_GAP_MM, "The gap") if "gap" in raw else 2.0
        if out["orientation"] not in ("auto", "portrait", "landscape"):
            raise PresetError("The sheet direction must be automatic, portrait or landscape.")
        if out["mode"] not in ("fill", "grid", "count"):
            raise PresetError("How many must be fill, grid or count.")
        if out["align"] not in ("center", "start"):
            raise PresetError("The placement must be centre or start.")
        if out["mode"] == "grid":
            out["cols"], out["rows"] = _int(raw, "cols", 1, 99, "Columns"), _int(raw, "rows", 1, 99, "Rows")
        if out["mode"] == "count":
            out["count"] = _int(raw, "count", 1, sh.MAX_LABELS, "The number of labels")
    out["area"] = raw.get("area", "full")
    if out["area"] not in lay.AREAS:
        raise PresetError("The part of the page must be full, top or bottom.")
    if out["area"] != "full" and out.get("orientation") == "landscape":
        raise PresetError("A half page can't be combined with a landscape sheet.")
    look = raw.get("look", {})
    if not isinstance(look, dict) or set(look) - LOOK_KEYS:
        raise PresetError("The look of the labels isn't valid.")
    shape = look.get("shape", "rect")
    family = look.get("family", "sans")
    if shape not in sh.SHAPES:
        raise PresetError("The shape must be rect, rounded or ellipse.")
    if family not in fonts.FAMILIES:
        raise PresetError("The font must be sans, serif or mono.")
    out["look"] = {"shape": shape, "radius": _num(look, "radius", 0, 50, "The corner rounding") if "radius" in look else 2.0,
                   "fill": _color(look.get("fill"), "The fill colour"), "border": _color(look.get("border"), "The border colour"),
                   "border_width": _num(look, "border_width", 0, 5, "The border width") if "border_width" in look else 0.3,
                   "color": _color(look.get("color", "#000000"), "The text colour") or "#000000", "family": family,
                   "bold": _bool(look, "bold", False, "Bold"), "italic": _bool(look, "italic", False, "Italic"),
                   "line_spacing": _num(look, "line_spacing", 1, 2, "The line spacing") if "line_spacing" in look else 1.2}
    text = raw.get("text", {})
    if not isinstance(text, dict) or set(text) - TEXT_KEYS:
        raise PresetError("The text settings aren't valid.")
    size = text.get("size", 11.0)
    out["text"] = {"size": None if size is None else _num({"size": size}, "size", sh.MIN_SIZE, 72, "The text size"),
                   "align": text.get("align", "left"), "valign": text.get("valign", "middle"),
                   "pad": _num(text, "pad", 0, sh.MAX_PAD, "The space inside") if "pad" in text else 2.0,
                   "first_bold": _bool(text, "first_bold", False, "First line bold"),
                   "first_scale": _num(text, "first_scale", 1, 3, "The first line size") if "first_scale" in text else 1.0,
                   "first_color": _color(text.get("first_color"), "The first line colour")}
    if out["text"]["align"] not in ("left", "center", "right") or out["text"]["valign"] not in ("top", "middle"):
        raise PresetError("Choose left, centre or right, and top or middle.")
    out["copies"] = _int(raw, "copies", 1, 100, "Copies of each label", 1)
    out["repeat"] = _bool(raw, "repeat", False, "Repeat to fill the sheet")
    out["outline"] = _bool(raw, "outline", False, "Cut lines")
    out["marks"] = _bool(raw, "marks", False, "Cut marks")
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
    if "sheet_copies" in raw:
        out["sheet_copies"] = _int(raw, "sheet_copies", 1, 20, "Copies of the sheet")
    return out
