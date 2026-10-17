"""Photo sheet layout: how many copies of a photo of a given size fit on a sheet of paper, and where.

Pure functions, all lengths in millimetres, origin at the top-left of the page, y growing downwards. No I/O,
so the same numbers drive the live preview in the browser and the PDF the server writes.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

MM_PER_INCH = 25.4
MAX_COPIES = 600  # a sanity cap: a 5 mm photo on A4 would otherwise ask for thousands
MIN_PHOTO_MM, MAX_PHOTO_MM = 5.0, 300.0
MIN_PAPER_MM, MAX_PAPER_MM = 40.0, 500.0
MAX_MARGIN_MM, MAX_GAP_MM = 100.0, 50.0

# Paper sizes offered when the printer's own list isn't available (no CUPS). (label, width mm, height mm), portrait.
PAPERS = {
    "a4": ("A4", 210.0, 297.0),
    "a5": ("A5", 148.0, 210.0),
    "a6": ("A6", 105.0, 148.0),
    "letter": ("Letter", 215.9, 279.4),
    "legal": ("Legal", 215.9, 355.6),
    "4x6": ("4 × 6 in (10 × 15 cm)", 101.6, 152.4),
    "5x7": ("5 × 7 in (13 × 18 cm)", 127.0, 177.8),
    "3.5x5": ("3.5 × 5 in (9 × 13 cm)", 88.9, 127.0),
}

# Common photo sizes (label, width mm, height mm). Rules change: the user must check their own authority.
PHOTO_PRESETS = [
    ("35 × 45 mm (UK, EU, India, Australia)", 35.0, 45.0),
    ("2 × 2 in (USA, India visa)", 50.8, 50.8),
    ("50 × 70 mm (Canada)", 50.0, 70.0),
    ("33 × 48 mm (China)", 33.0, 48.0),
    ("40 × 50 mm", 40.0, 50.0),
    ("25 × 35 mm (small ID)", 25.0, 35.0),
    ("20 × 25 mm (stamp size)", 20.0, 25.0),
]

_MEDIA = re.compile(r"^(?P<prefix>[a-z0-9.\-]+)_(?P<name>.+)_(?P<w>\d+(?:\.\d+)?)x(?P<h>\d+(?:\.\d+)?)(?P<unit>mm|in)$", re.I)


class LayoutError(ValueError):
    """The request can't be laid out; the message is written for the person using the form."""


def to_mm(value: float, unit: str) -> float:
    return value * {"mm": 1.0, "cm": 10.0, "in": MM_PER_INCH}[unit]


def parse_media(keyword: str) -> dict | None:
    """'iso_a4_210x297mm' -> {'keyword', 'label', 'width', 'height', 'borderless'} (mm). Custom ranges give None."""
    m = _MEDIA.match(keyword or "")
    if not m:
        return None
    scale = MM_PER_INCH if m["unit"].lower() == "in" else 1.0
    w, h = float(m["w"]) * scale, float(m["h"]) * scale
    name = m["name"]
    borderless = "borderless" in name.lower()
    label = _label(m["prefix"], name, m["w"], m["h"], m["unit"])
    if borderless:
        label = label[:-1] + ", borderless)" if label.endswith(")") else label + " (borderless)"
    return {"keyword": keyword, "label": label, "width": round(w, 1),
            "height": round(h, 1), "borderless": borderless}


def _label(prefix: str, name: str, w: str, h: str, unit: str) -> str:
    base = re.sub(r"[-_]?borderless", "", name, flags=re.I).strip("-_ ")
    size = f"{float(w):g} × {float(h):g} {unit}"
    m = re.fullmatch(r"([abc])-?(\d)", base, re.I)  # iso "a4", or a vendor's "a-4"
    if m:
        return f"{m[1].upper()}{m[2]} ({size})"
    if prefix.lower() == "na" and base.lower() == "letter":
        return f"Letter ({size})"
    if prefix.lower() == "na" and base.lower() == "legal":
        return f"Legal ({size})"
    return size


@dataclass(frozen=True)
class Request:
    paper_w: float
    paper_h: float
    photo_w: float
    photo_h: float
    margin: tuple[float, float, float, float] = (5.0, 5.0, 5.0, 5.0)  # top, right, bottom, left
    gap: float = 2.0
    mode: str = "fill"  # fill | grid | count
    cols: int = 0
    rows: int = 0
    count: int = 0
    orientation: str = "auto"  # auto | portrait | landscape
    align: str = "center"  # center | start
    min_margin: float = 3.0  # the closest the printer can print to an edge (mm)


@dataclass
class Layout:
    page_w: float
    page_h: float
    orientation: str
    cols: int
    rows: int
    count: int
    capacity: int
    cells: list[dict]  # {"x", "y", "w", "h"} of each photo
    grid: dict  # bounding box of the photos {"x", "y", "w", "h"}
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"page_w": self.page_w, "page_h": self.page_h, "orientation": self.orientation, "cols": self.cols,
                "rows": self.rows, "count": self.count, "capacity": self.capacity, "cells": self.cells,
                "grid": self.grid, "warnings": self.warnings}


def _fits(avail: float, size: float, gap: float) -> int:
    """How many items of `size` with `gap` between them fit in `avail`."""
    if avail < size - 1e-9:
        return 0
    return int(math.floor((avail + gap + 1e-9) / (size + gap)))


def _check(req: Request) -> None:
    def between(label: str, v: float, lo: float, hi: float):
        if not (isinstance(v, (int, float)) and math.isfinite(v)) or not lo <= v <= hi:
            raise LayoutError(f"{label} must be between {lo:g} and {hi:g} mm.")
    between("The photo width", req.photo_w, MIN_PHOTO_MM, MAX_PHOTO_MM)
    between("The photo height", req.photo_h, MIN_PHOTO_MM, MAX_PHOTO_MM)
    between("The paper width", req.paper_w, MIN_PAPER_MM, MAX_PAPER_MM)
    between("The paper height", req.paper_h, MIN_PAPER_MM, MAX_PAPER_MM)
    for side, v in zip(("top", "right", "bottom", "left"), req.margin):
        between(f"The {side} margin", v, 0, MAX_MARGIN_MM)
    between("The gap between photos", req.gap, 0, MAX_GAP_MM)
    if req.mode not in ("fill", "grid", "count"):
        raise LayoutError("Choose how many photos: fill the page, a grid, or a number.")
    if req.orientation not in ("auto", "portrait", "landscape"):
        raise LayoutError("Choose portrait, landscape or automatic.")
    if req.align not in ("center", "start"):
        raise LayoutError("Choose centred or top-left.")


def _lay(req: Request, landscape: bool) -> Layout:
    page_w, page_h = (req.paper_h, req.paper_w) if landscape else (req.paper_w, req.paper_h)
    top, right, bottom, left = req.margin
    avail_w, avail_h = page_w - left - right, page_h - top - bottom
    max_cols, max_rows = _fits(avail_w, req.photo_w, req.gap), _fits(avail_h, req.photo_h, req.gap)
    capacity = min(max_cols * max_rows, MAX_COPIES)
    warnings: list[str] = []
    if req.mode == "grid":
        cols, rows = max(req.cols, 1), max(req.rows, 1)
        if cols > max_cols or rows > max_rows:
            warnings.append(f"Only {max_cols} × {max_rows} photos fit with these sizes and margins, not {cols} × {rows}.")
            cols, rows = min(cols, max_cols), min(rows, max_rows)
        count = cols * rows
    elif req.mode == "count":
        want = max(req.count, 1)
        if want > capacity:
            warnings.append(f"Only {capacity} photos fit, not {want}.")
        count = min(want, capacity)
        cols = min(max_cols, count) if max_cols else 0
        rows = math.ceil(count / cols) if cols else 0
    else:
        cols, rows, count = max_cols, max_rows, capacity
    cells: list[dict] = []
    grid = {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    if count and cols and rows:
        used_cols = cols
        grid_w = used_cols * req.photo_w + (used_cols - 1) * req.gap
        grid_h = rows * req.photo_h + (rows - 1) * req.gap
        ox = left + ((avail_w - grid_w) / 2 if req.align == "center" else 0.0)
        oy = top + ((avail_h - grid_h) / 2 if req.align == "center" else 0.0)
        for i in range(count):
            r, c = divmod(i, cols)
            cells.append({"x": round(ox + c * (req.photo_w + req.gap), 3), "y": round(oy + r * (req.photo_h + req.gap), 3),
                          "w": req.photo_w, "h": req.photo_h})
        grid = {"x": round(ox, 3), "y": round(oy, 3), "w": round(grid_w, 3), "h": round(grid_h, 3)}
    return Layout(page_w, page_h, "landscape" if landscape else "portrait", cols if count else 0, rows if count else 0,
                  count, capacity, cells, grid, warnings)


def compute(req: Request) -> Layout:
    """Lay the photos out. Raises LayoutError if the request makes no sense or nothing fits."""
    _check(req)
    options = {"portrait": [False], "landscape": [True], "auto": [False, True]}[req.orientation]
    layouts = [_lay(req, landscape) for landscape in options]
    # the best one: for a grid the one where the whole grid fits, otherwise the most photos (portrait wins a tie)
    def score(l: Layout) -> tuple:
        wanted = req.cols * req.rows if req.mode == "grid" else req.count if req.mode == "count" else l.capacity
        return (min(l.count, wanted), -len(l.warnings), l.count)
    best = max(layouts, key=score)
    if best.count == 0:
        raise LayoutError("The photo doesn't fit on this paper with these margins. Try a smaller margin, a bigger paper, or a smaller photo.")
    top, right, bottom, left = req.margin
    close = [s for s, v in zip(("top", "right", "bottom", "left"), req.margin) if v < req.min_margin]
    if close:
        best.warnings.append(f"The {', '.join(close)} margin is under {req.min_margin:g} mm. Most printers can't print that close to the edge, so the photos there may be cut off. Use a borderless paper size, or a bigger margin.")
    if req.mode == "fill" and best.capacity >= MAX_COPIES:
        best.warnings.append(f"Showing the first {MAX_COPIES} photos.")
    return best
