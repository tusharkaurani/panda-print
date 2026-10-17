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
MAX_PHOTOS = 20  # different photos on one sheet
AREAS = ("full", "top", "bottom", "left", "right")  # which part of the paper to use; the rest stays blank
# the sides of the used area that lie on the paper's own edge (the printer can't reach the very edge there)
EDGE_SIDES = {"full": ("top", "right", "bottom", "left"), "top": ("top", "right", "left"), "bottom": ("right", "bottom", "left"),
              "left": ("top", "bottom", "left"), "right": ("top", "right", "bottom")}
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
    area: str = "full"  # full | top | bottom | left | right: print on that part of the paper only, leave the rest blank


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
    region: dict = field(default_factory=dict)  # the part of the paper in use {"x", "y", "w", "h"}; the rest is blank
    area: str = "full"

    def to_dict(self) -> dict:
        return {"page_w": self.page_w, "page_h": self.page_h, "orientation": self.orientation, "cols": self.cols,
                "rows": self.rows, "count": self.count, "capacity": self.capacity, "cells": self.cells,
                "grid": self.grid, "warnings": self.warnings, "region": self.region, "area": self.area}


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
    if req.area not in AREAS:
        raise LayoutError("Choose the whole page, or its top, bottom, left or right half.")
    if req.area != "full" and req.orientation == "landscape":
        raise LayoutError("A half page is laid out the way the paper is fed. Choose portrait or automatic, or use the whole page.")


def _region(area: str, w: float, h: float) -> dict:
    """The part of a page of w x h in use. Halves are exact, so an A4 sheet's top half is an A5 area."""
    return {"full": {"x": 0.0, "y": 0.0, "w": w, "h": h},
            "top": {"x": 0.0, "y": 0.0, "w": w, "h": h / 2}, "bottom": {"x": 0.0, "y": h / 2, "w": w, "h": h / 2},
            "left": {"x": 0.0, "y": 0.0, "w": w / 2, "h": h}, "right": {"x": w / 2, "y": 0.0, "w": w / 2, "h": h}}[area]


def _lay(req: Request, landscape: bool) -> Layout:
    page_w, page_h = (req.paper_h, req.paper_w) if landscape else (req.paper_w, req.paper_h)
    top, right, bottom, left = req.margin
    region = _region(req.area, page_w, page_h)
    avail_w, avail_h = region["w"] - left - right, region["h"] - top - bottom  # margins are measured inside the area in use
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
        ox = region["x"] + left + ((avail_w - grid_w) / 2 if req.align == "center" else 0.0)
        oy = region["y"] + top + ((avail_h - grid_h) / 2 if req.align == "center" else 0.0)
        for i in range(count):
            r, c = divmod(i, cols)
            cells.append({"x": round(ox + c * (req.photo_w + req.gap), 3), "y": round(oy + r * (req.photo_h + req.gap), 3),
                          "w": req.photo_w, "h": req.photo_h})
        grid = {"x": round(ox, 3), "y": round(oy, 3), "w": round(grid_w, 3), "h": round(grid_h, 3)}
    return Layout(page_w, page_h, "landscape" if landscape else "portrait", cols if count else 0, rows if count else 0,
                  count, capacity, cells, grid, warnings, region, req.area)


def compute(req: Request) -> Layout:
    """Lay the photos out. Raises LayoutError if the request makes no sense or nothing fits."""
    _check(req)
    options = {"portrait": [False], "landscape": [True], "auto": [False, True]}[req.orientation]
    if req.area != "full":
        options = [False]  # a part of the page is described as the paper is fed, so the sheet is never turned
    layouts = [_lay(req, landscape) for landscape in options]
    # the best one: for a grid the one where the whole grid fits, otherwise the most photos (portrait wins a tie)
    def score(l: Layout) -> tuple:
        wanted = req.cols * req.rows if req.mode == "grid" else req.count if req.mode == "count" else l.capacity
        return (min(l.count, wanted), -len(l.warnings), l.count)
    best = max(layouts, key=score)
    if best.count == 0:
        raise LayoutError("The photo doesn't fit on this paper with these margins. Try a smaller margin, a bigger paper, or a smaller photo.")
    on_edge = EDGE_SIDES[req.area]  # the edge between the used part and the blank part is not the printer's concern
    close = [s for s, v in zip(("top", "right", "bottom", "left"), req.margin) if s in on_edge and v < req.min_margin]
    if close:
        best.warnings.append(f"The {', '.join(close)} margin is under {req.min_margin:g} mm. Most printers can't print that close to the edge, so the photos there may be cut off. Use a borderless paper size, or a bigger margin.")
    if req.mode == "fill" and best.capacity >= MAX_COPIES:
        best.warnings.append(f"Showing the first {MAX_COPIES} photos.")
    return best


def allocate(requested: list[int], slots: int, fill: bool = True) -> list[int]:
    """How many copies of each photo go on a sheet with `slots` places.

    `requested` are the copies the person asked for; they count as a ratio. Equal requests (the default is 1 each)
    give equal shares. With `fill` the sheet is filled: if it holds more than was asked for, the shares grow in the
    same ratio; if it holds fewer, they shrink in the same ratio. Without `fill`, a request that fits is printed
    exactly as asked. Rounding leftovers are handed out one at a time, to the photos that lost the most to
    rounding and then in order, so no photo is favoured. Every photo keeps at least one place when there is room
    for them all.
    """
    k = len(requested)
    if k == 0 or slots <= 0:
        return [0] * k
    if any(isinstance(c, bool) or not isinstance(c, int) or c < 1 for c in requested):
        raise LayoutError("Each photo needs at least one copy.")
    total = sum(requested)
    if not fill and total <= slots:
        return list(requested)
    exact = [c * slots / total for c in requested]
    got = [int(math.floor(e + 1e-9)) for e in exact]
    for i in sorted(range(k), key=lambda i: (-(exact[i] - got[i]), i))[: slots - sum(got)]:
        got[i] += 1
    if slots >= k:  # nobody disappears from the sheet
        for i in range(k):
            while got[i] == 0:
                donor = max(range(k), key=lambda j: (got[j], -requested[j], -j))  # the biggest share, the smaller ask first
                got[donor] -= 1
                got[i] += 1
    return got


def assignment(counts: list[int]) -> list[int]:
    """Which photo goes in each place, in reading order: all copies of the first photo, then the second, ..."""
    return [i for i, c in enumerate(counts) for _ in range(c)]


def pin_count(current: list[int], slots: int, index: int, count: int) -> list[int]:
    """Set one photo's number of copies; the others give or take so the total stays `slots`, keeping the ratio
    they had between themselves. The photo stays within what leaves everyone else at least one place."""
    k = len(current)
    if not 0 <= index < k:
        raise LayoutError("That photo isn't on the sheet.")
    if isinstance(count, bool) or not isinstance(count, int):
        raise LayoutError("The number of copies must be a whole number.")
    low = 1 if slots >= k else 0
    count = max(low, min(count, slots - low * (k - 1)))
    others = [max(current[j], 1) for j in range(k) if j != index]  # their shares, as they stand
    rest = allocate(others, slots - count) if others and slots - count > 0 else [0] * len(others)
    it = iter(rest)
    return [count if j == index else next(it) for j in range(k)]
