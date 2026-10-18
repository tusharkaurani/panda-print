"""Photo collage layouts: fit any number of photos onto one sheet by halving the paper.

A sheet is cut in two equal halves, and a half can be cut in two again, the way A4 is two A5 and A5 is two A6. Three photos
on A4 become one A5 (turned to landscape if the photo is) and two A6. The halving keeps every cell the shape of a standard
paper size, so the prints trim to familiar sizes.

The module tries the ways of halving that give `n` places, matches photos to places so that as little as possible of any photo is
cropped (or left white), and ranks the results. Pure functions, all lengths in millimetres, origin at the page's top-left,
y growing downwards: the same numbers draw the live preview and the PDF.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.tools.photosheet import layout as photo_layout

MAX_PHOTOS = 24
VARIANTS = 8            # ranked arrangements offered
KEEP = 10               # arrangements kept per sub-sheet while searching
MIN_CELL_MM = 15.0
MAX_ASPECT = 4.0        # no place is more than 4 times longer than wide
TYPICAL = 1.4           # the shape of a photo print (3:2, 4:3, A-series): used to prefer sensible places
IMBALANCE = 0.03        # how much to dislike one photo being much bigger than another


class CollageError(ValueError):
    """The request can't be laid out; the message is written for the person using the form."""


@dataclass(frozen=True)
class Request:
    paper_w: float
    paper_h: float
    margin: tuple[float, float, float, float] = (5.0, 5.0, 5.0, 5.0)  # top, right, bottom, left
    gap: float = 3.0
    orientation: str = "auto"  # auto | portrait | landscape
    area: str = "full"         # full | top | bottom
    min_margin: float = 0.0    # the closest the printer can print to an edge (mm)


@dataclass
class Arrangement:
    page_w: float
    page_h: float
    orientation: str
    cells: list[dict]              # {"x","y","w","h"} of each place
    assign: list[int]              # the photo in each place
    cost: float
    region: dict = field(default_factory=dict)
    area: str = "full"
    key: tuple = ()                # the places' sizes, for telling arrangements apart


def _mismatch(photo_ar: float, cell_ar: float) -> float:
    """The part of a photo that is cropped when it fills a place (or left white when it is shown whole): 0 is a perfect match."""
    r = photo_ar / cell_ar
    return 1.0 - min(r, 1.0 / r)


def _intrinsic(cells: list[tuple[float, float, float, float]]) -> float:
    """How sensible the places are as photo frames, whatever the photos: close to the shape of a print, either way up."""
    total = sum(w * h for _, _, w, h in cells)
    return sum((w * h / total) * min(abs(math.log(w / h / TYPICAL)), abs(math.log(w / h * TYPICAL))) for _, _, w, h in cells)


def _shapes(k: int, w: float, h: float, gap: float, memo: dict) -> list[list[tuple[float, float, float, float]]]:
    """Ways to cut a w x h sheet into k places by repeated halving (with `gap` between halves). Each is a list of (x, y, w, h)."""
    key = (k, round(w, 3), round(h, 3))
    if key in memo:
        return memo[key]
    result: list[list[tuple[float, float, float, float]]] = []
    if k == 1:
        ar = w / h
        if w >= MIN_CELL_MM and h >= MIN_CELL_MM and 1 / MAX_ASPECT <= ar <= MAX_ASPECT:
            result = [[(0.0, 0.0, w, h)]]
    else:
        seen: set[tuple] = set()
        for a in sorted({k // 2, k - k // 2}):  # the halves hold as near the same number of photos as they can
            b = k - a
            for axis in ("v", "h"):
                if axis == "v":
                    hw, hh = (w - gap) / 2, h
                    offset = (hw + gap, 0.0)
                else:
                    hw, hh = w, (h - gap) / 2
                    offset = (0.0, hh + gap)
                if hw < MIN_CELL_MM or hh < MIN_CELL_MM:
                    continue
                for first in _shapes(a, hw, hh, gap, memo):
                    for second in _shapes(b, hw, hh, gap, memo):
                        cells = first + [(x + offset[0], y + offset[1], cw, ch) for x, y, cw, ch in second]
                        sig = tuple(sorted((round(x, 1), round(y, 1), round(cw, 1), round(ch, 1)) for x, y, cw, ch in cells))
                        if sig not in seen:
                            seen.add(sig)
                            result.append(cells)
        result.sort(key=_intrinsic)
        result = result[:KEEP]
    memo[key] = result
    return result


def _hungarian(cost: list[list[float]]) -> list[int]:
    """The assignment of rows to columns with the least total cost (square matrix). Returns the column of each row."""
    n = len(cost)
    INF = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv, used = [INF] * (n + 1), [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    out = [0] * n
    for j in range(1, n + 1):
        out[p[j] - 1] = j - 1
    return out


def _check(req: Request, n: int) -> None:
    def between(label: str, v: float, lo: float, hi: float):
        if not (isinstance(v, (int, float)) and math.isfinite(v)) or not lo <= v <= hi:
            raise CollageError(f"{label} must be between {lo:g} and {hi:g} mm.")
    between("The paper width", req.paper_w, photo_layout.MIN_PAPER_MM, photo_layout.MAX_PAPER_MM)
    between("The paper height", req.paper_h, photo_layout.MIN_PAPER_MM, photo_layout.MAX_PAPER_MM)
    for side, v in zip(("top", "right", "bottom", "left"), req.margin):
        between(f"The {side} margin", v, 0, photo_layout.MAX_MARGIN_MM)
    between("The gap between photos", req.gap, 0, photo_layout.MAX_GAP_MM)
    if req.orientation not in ("auto", "portrait", "landscape"):
        raise CollageError("Choose portrait, landscape or automatic.")
    if req.area not in photo_layout.AREAS:
        raise CollageError("Choose the whole page, or its top or bottom half.")
    if req.area != "full" and req.orientation == "landscape":
        raise CollageError("A half page is laid out the way the paper is fed. Choose portrait or automatic, or use the whole page.")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_PHOTOS:
        raise CollageError(f"Choose between 1 and {MAX_PHOTOS} photos.")


def arrangements(req: Request, ratios: list[float]) -> list[Arrangement]:
    """The best ways to fit the photos (given by width / height) on the paper, best first, at most VARIANTS of them.
    Raises CollageError if the request makes no sense or the photos can't all fit."""
    n = len(ratios)
    _check(req, n)
    if any(not (isinstance(r, (int, float)) and math.isfinite(r) and r > 0) for r in ratios):
        raise CollageError("A photo's size isn't usable.")
    options = {"portrait": [False], "landscape": [True], "auto": [False, True]}[req.orientation]
    if req.area != "full":
        options = [False]
    found: dict[tuple, Arrangement] = {}
    for landscape in options:
        page_w, page_h = (req.paper_h, req.paper_w) if landscape else (req.paper_w, req.paper_h)
        page_w, page_h = (page_w, page_h) if landscape else (min(page_w, page_h), max(page_w, page_h))
        region = photo_layout._region(req.area, page_w, page_h)
        top, right, bottom, left = req.margin
        cw, ch = region["w"] - left - right, region["h"] - top - bottom
        if cw < MIN_CELL_MM or ch < MIN_CELL_MM:
            raise CollageError("The margins leave no room for photos. Use smaller margins or a bigger paper.")
        ox, oy = region["x"] + left, region["y"] + top
        for shape in _shapes(n, cw, ch, req.gap, {}):
            cells = [(ox + x, oy + y, w, h) for x, y, w, h in shape]
            area_total = sum(w * h for _, _, w, h in cells)
            cost = [[_mismatch(r, w / h) * (w * h / area_total) for r in ratios] for _, _, w, h in cells]
            pick = _hungarian(cost)
            assign_cost = sum(cost[i][pick[i]] for i in range(n))
            mean = area_total / n
            spread = sum(abs(math.log(w * h / mean)) for _, _, w, h in cells) / n
            key = tuple(sorted((round(w, 1), round(h, 1)) for _, _, w, h in cells)) + (landscape,)
            total = assign_cost + IMBALANCE * spread
            if key not in found or total < found[key].cost:
                found[key] = Arrangement(page_w, page_h, "landscape" if landscape else "portrait",
                                         [{"x": round(x, 3), "y": round(y, 3), "w": round(w, 3), "h": round(h, 3)} for x, y, w, h in cells],
                                         pick, total, region, req.area, key)
    if not found:
        raise CollageError(f"{n} photos don't fit on this paper with these margins and gaps. Use fewer photos, smaller margins or gaps, or a bigger paper.")
    ranked = sorted(found.values(), key=lambda a: (a.cost, a.orientation != "portrait"))
    return ranked[:VARIANTS]


def apply_order(arr: Arrangement, order: list[int] | None) -> list[int]:
    """The photo in each place: the best match, or `order` (the person swapped some) when it is a valid arrangement."""
    if order is None:
        return list(arr.assign)
    n = len(arr.cells)
    if (not isinstance(order, list) or len(order) != n or any(isinstance(v, bool) or not isinstance(v, int) for v in order)
            or sorted(order) != list(range(n))):
        raise CollageError("That arrangement of the photos isn't valid.")
    return list(order)


def describe(arr: Arrangement) -> list[dict]:
    """The places grouped by size, biggest first: [{"w", "h", "count"}] (each sized as it lies on the page)."""
    groups: dict[tuple[float, float], int] = {}
    for c in arr.cells:
        k = (round(c["w"], 1), round(c["h"], 1))
        groups[k] = groups.get(k, 0) + 1
    return [{"w": w, "h": h, "count": n} for (w, h), n in sorted(groups.items(), key=lambda kv: -kv[0][0] * kv[0][1])]


def to_layout(arr: Arrangement, req: Request, warnings: list[str] | None = None) -> photo_layout.Layout:
    """The arrangement as the passport tool's Layout, so the same PDF writer draws it."""
    xs = [c["x"] for c in arr.cells] + [c["x"] + c["w"] for c in arr.cells]
    ys = [c["y"] for c in arr.cells] + [c["y"] + c["h"] for c in arr.cells]
    grid = {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}
    return photo_layout.Layout(arr.page_w, arr.page_h, arr.orientation, 0, 0, len(arr.cells), len(arr.cells), arr.cells, grid,
                               list(warnings or []), arr.region, arr.area)


def margin_warnings(arr: Arrangement, req: Request) -> list[str]:
    """Margins closer to a paper edge than the printer can reach (the edge between the used and blank halves isn't the printer's concern)."""
    on_edge = photo_layout.EDGE_SIDES[req.area]
    close = [s for s, v in zip(("top", "right", "bottom", "left"), req.margin) if s in on_edge and v < req.min_margin]
    if not close:
        return []
    return [f"The {', '.join(close)} margin is under {req.min_margin:g} mm. Most printers can't print that close to the edge, so photos there may be cut off. "
            "Use a borderless paper size, or a bigger margin."]
