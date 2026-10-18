"""Label sheets: where each label sits on the paper, what goes on it, and how it looks.

Pure functions, lengths in millimetres (text sizes in points), origin at the page's top-left, y growing downwards.
The same numbers draw the live preview and write the PDF.

Two ways to place the labels:
  * a label pack (an Avery-style sheet, or your own measurements): fixed positions on a fixed paper size;
  * by label size: a plain sheet of stickers with no printed grid, filled by the same layout rules as the
    Passport Sheet Maker (as many as fit, a grid, or a number; margins, gap, orientation, part of the page).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from app.tools.labels import fonts
from app.tools.photosheet import layout as photo_layout

MM_PER_PT = 25.4 / 72.0
MAX_LABELS = 5000       # after copies are counted
MAX_SHEETS = 100
MAX_SOURCE_CHARS = 400_000
MAX_ROWS = 2000
MIN_SIZE, MAX_SIZE, SIZE_STEP = 5.0, 24.0, 0.5
MAX_PAD = 20.0
PAPERS = {"a4": (210.0, 297.0), "letter": (215.9, 279.4)}
SHAPES = ("rect", "rounded", "ellipse")
POSITIONS = ("top", "left", "background")
INSET_ELLIPSE = 0.7071  # the rectangle inside an ellipse that touches it at the corners: sqrt(2)/2 of each side

# Common label sizes for plain sheets: (name, width mm, height mm, shape). A starting point, like the passport sizes.
SIZES = [
    ("25 mm circle", 25.0, 25.0, "ellipse"), ("38 mm circle", 38.0, 38.0, "ellipse"), ("40 mm circle", 40.0, 40.0, "ellipse"),
    ("50 mm circle", 50.0, 50.0, "ellipse"), ("2 in circle", 50.8, 50.8, "ellipse"), ("3 in circle", 76.2, 76.2, "ellipse"),
    ("40 × 40 mm square", 40.0, 40.0, "rounded"), ("50 × 50 mm square", 50.0, 50.0, "rounded"), ("2 × 2 in square", 50.8, 50.8, "rounded"),
    ("50 × 30 mm", 50.0, 30.0, "rounded"), ("60 × 40 mm", 60.0, 40.0, "rounded"), ("70 × 35 mm", 70.0, 35.0, "rounded"),
    ("89 × 36 mm (address)", 89.0, 36.0, "rounded"), ("99.1 × 38.1 mm (2 across, A4)", 99.1, 38.1, "rounded"), ("99.1 × 67.7 mm (2 across, A4)", 99.1, 67.7, "rounded"),
    ("63.5 × 38.1 mm (3 across, A4)", 63.5, 38.1, "rounded"), ("38.1 × 21.2 mm (5 across, A4)", 38.1, 21.2, "rounded"),
    ("2⅝ × 1 in (Address, Letter)", 66.675, 25.4, "rounded"), ("4 × 2 in (shipping)", 101.6, 50.8, "rounded"), ("4 × 3⅓ in (shipping)", 101.6, 84.667, "rounded"),
    ("3 × 2 in", 76.2, 50.8, "rounded"), ("1¾ × ½ in (return address)", 44.45, 12.7, "rounded"),
]


class SheetError(ValueError):
    """The request can't be laid out; the message is written for the person using the form."""


@dataclass
class Sheet:
    """Where the labels of one sheet sit. `paper_w` x `paper_h` is the page in the direction the labels are laid out
    (a plain sheet can be turned to landscape)."""
    paper_w: float
    paper_h: float
    cells: list[dict]
    width: float
    height: float
    cols: int = 0
    rows: int = 0
    orientation: str = "portrait"
    region: dict = field(default_factory=dict)
    area: str = "full"
    warnings: list[str] = field(default_factory=list)

    @property
    def per_sheet(self) -> int:
        return len(self.cells)


def _number(name: str, v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1000:
        raise SheetError(f"The {name} must be a number.")
    return float(v)


def pack_sheet(paper_w, paper_h, cols, rows, width, height, left, top, gap_x=0.0, gap_y=0.0, area="full") -> Sheet:
    """A label pack: fixed places (the sheet's own die cuts) on a portrait page. `area` top or bottom uses only the labels in that
    half of the sheet (a label belongs to the half its middle is in), so the rest can be printed later."""
    for name, v in (("columns", cols), ("rows", rows)):
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 30:
            raise SheetError(f"The number of {name} must be a whole number from 1 to 30.")
    paper_w, paper_h, width, height, left, top, gap_x, gap_y = (_number(n, v) for n, v in (
        ("paper width", paper_w), ("paper height", paper_h), ("label width", width), ("label height", height), ("left margin", left),
        ("top margin", top), ("gap across", gap_x), ("gap down", gap_y)))
    pw, ph = min(paper_w, paper_h), max(paper_w, paper_h)
    if not (40 <= pw and ph <= 500):
        raise SheetError("The paper must be between 40 and 500 mm.")
    if not (5 <= width <= 300 and 5 <= height <= 300):
        raise SheetError("A label must be between 5 and 300 mm each way.")
    if min(left, top, gap_x, gap_y) < 0 or max(gap_x, gap_y) > 50:
        raise SheetError("Margins and gaps can't be negative, and a gap is at most 50 mm.")
    need_w = left + cols * width + (cols - 1) * gap_x
    need_h = top + rows * height + (rows - 1) * gap_y
    if need_w > pw + 0.01:
        raise SheetError(f"{cols} labels across need {need_w:.1f} mm, but the paper is {pw:g} mm wide. Make the labels narrower or use fewer columns.")
    if need_h > ph + 0.01:
        raise SheetError(f"{rows} rows of labels need {need_h:.1f} mm, but the paper is {ph:g} mm tall. Make the labels shorter or use fewer rows.")
    cells = [{"x": round(left + c * (width + gap_x), 3), "y": round(top + r * (height + gap_y), 3), "w": width, "h": height}
             for r in range(rows) for c in range(cols)]
    if area not in photo_layout.AREAS:
        raise SheetError("Choose the whole page, or its top or bottom half.")
    region = {"x": 0.0, "y": 0.0, "w": pw, "h": ph}
    warnings: list[str] = []
    if area != "full":
        region = {"x": 0.0, "y": 0.0 if area == "top" else ph / 2, "w": pw, "h": ph / 2}
        cells = [c for c in cells if (c["y"] + c["h"] / 2 < ph / 2) == (area == "top")]
        if not cells:
            raise SheetError(f"No label of this sheet is in the {area} half. Use the whole page, or the other half.")
        if len(cells) < cols * rows:
            warnings.append(f"Only the {len(cells)} labels in the {area} half of the sheet are used; the rest stay blank.")
    return Sheet(pw, ph, cells, width, height, cols, rows, "portrait", region, area, warnings)


def size_sheet(paper_w, paper_h, width, height, margin=5.0, gap=2.0, mode="fill", cols=0, rows=0, count=0, orientation="auto",
               align="center", area="full", min_margin=0.0) -> Sheet:
    """A plain sheet of stickers (no printed grid): as many labels of this size as fit, a grid, or a number, by the same rules
    as the Passport Sheet Maker."""
    nums = [_number(n, v) for n, v in (("paper width", paper_w), ("paper height", paper_h), ("label width", width), ("label height", height), ("gap", gap))]
    m = margin if isinstance(margin, (list, tuple)) else [margin] * 4
    if len(m) != 4:
        raise SheetError("The margins must be a number, or four numbers.")
    margins = tuple(_number("margin", v) for v in m)
    for name, v in (("cols", cols), ("rows", rows), ("count", count)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise SheetError(f"The {name} must be a whole number.")
    try:
        lay = photo_layout.compute(photo_layout.Request(
            paper_w=min(nums[0], nums[1]), paper_h=max(nums[0], nums[1]), photo_w=nums[2], photo_h=nums[3], margin=margins, gap=nums[4],
            mode=mode, cols=cols, rows=rows, count=count, orientation=orientation, align=align, min_margin=min_margin, area=area))
    except photo_layout.LayoutError as e:
        raise SheetError(str(e).replace("photos", "labels").replace("photo", "label").replace("Photo", "Label")) from None
    warnings = [w.replace("photos", "labels").replace("photo", "label") for w in lay.warnings]
    return Sheet(lay.page_w, lay.page_h, lay.cells, nums[2], nums[3], lay.cols, lay.rows, lay.orientation, lay.region, lay.area, warnings)


# ------------------------------------------------------------------ what goes on the labels
def labels_from_text(text: str, per_line: bool) -> list[list[str]]:
    """Typed or pasted text: blocks separated by a blank line (an address), or one label per line."""
    if len(text) > MAX_SOURCE_CHARS:
        raise SheetError("That text is too long. Use a shorter list.")
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if per_line:
        return [[ln.strip()] for ln in lines if ln.strip()]
    blocks, cur = [], []
    for ln in lines:
        if ln.strip():
            cur.append(ln.strip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


_FIELD = re.compile(r"\{([^{}]+)\}")


def read_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """A spreadsheet saved as CSV (comma, semicolon or tab separated): the header names, and one dict per row."""
    if len(text) > MAX_SOURCE_CHARS:
        raise SheetError("That spreadsheet is too large. Use fewer rows.")
    text = text.lstrip("﻿")
    if not text.strip():
        raise SheetError("The spreadsheet is empty.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise SheetError("The spreadsheet needs a first row of column names and at least one row of data.")
    headers = [c.strip() for c in rows[0]]
    if not any(headers) or len(set(h.lower() for h in headers if h)) != len([h for h in headers if h]):
        raise SheetError("The first row must hold different column names (like Name, Street, City).")
    if len(rows) - 1 > MAX_ROWS:
        raise SheetError(f"The spreadsheet has more than {MAX_ROWS} rows. Split it into parts.")
    data = [{h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(headers) if h} for r in rows[1:]]
    return [h for h in headers if h], data


def labels_from_csv(text: str, template: str) -> tuple[list[list[str]], list[str]]:
    """One label per row. `template` is what a label says, with {Column} where a value goes; empty means every column on its own line.
    A line made only of empty values is dropped, so a missing "Address line 2" leaves no gap."""
    headers, rows = read_csv(text)
    by_lower = {h.lower(): h for h in headers}
    lines = [ln for ln in (template or "").replace("\r\n", "\n").split("\n")]
    if not any(ln.strip() for ln in lines):
        lines = [f"{{{h}}}" for h in headers]
    for ln in lines:
        for name in _FIELD.findall(ln):
            if name.strip().lower() not in by_lower:
                raise SheetError(f"The label has {{{name}}}, but the spreadsheet has no column called that. Its columns are: {', '.join(headers)}.")
    out = []
    for row in rows:
        label = []
        for ln in lines:
            names = _FIELD.findall(ln)
            if names and not any(row.get(by_lower[n.strip().lower()], "") for n in names):
                continue  # all its values are empty
            label.append(_FIELD.sub(lambda m: row.get(by_lower[m[1].strip().lower()], ""), ln).strip())
        label = [ln for i, ln in enumerate(label) if ln or (0 < i < len(label) - 1)]  # keep blank lines only between text
        if label:
            out.append(label)
    return out, headers


# ------------------------------------------------------------------ how a label looks
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def clean_color(value, label: str, allow_none: bool = True) -> str | None:
    if value in (None, "") and allow_none:
        return None
    if not isinstance(value, str) or not _COLOR.match(value):
        raise SheetError(f"{label} must be a colour like #1a2b3c.")
    return value.lower()


@dataclass
class Logo:
    """A picture on every label (the file itself is only needed for the PDF; the layout needs its shape)."""
    width: int
    height: int
    position: str = "top"      # top | left | background
    pct: float = 30.0          # of the label's height (top) or width (left)


@dataclass
class Style:
    size: float | None = 11.0     # points; None = the largest that fits every label
    align: str = "left"           # left | center | right
    valign: str = "middle"        # top | middle
    family: str = "sans"          # sans | serif | mono
    bold: bool = False
    italic: bool = False
    first_bold: bool = False
    first_scale: float = 1.0      # the first line this many times larger
    first_color: str | None = None
    color: str = "#000000"
    line_spacing: float = 1.2     # of the text size
    pad: float = 2.0              # mm inside each label
    inset: float = 0.0            # mm between the cut line and the label itself (the cut area is the whole place)
    shape: str = "rect"           # rect | rounded | ellipse
    radius: float = 2.0           # mm, for rounded
    fill: str | None = None       # background colour
    border: str | None = None
    border_width: float = 0.3     # mm
    logo: Logo | None = None


def check_style(style: Style, sheet: Sheet) -> None:
    if style.size is not None and not MIN_SIZE <= style.size <= 72:
        raise SheetError("The text size must be between 5 and 72 points.")
    if style.align not in ("left", "center", "right") or style.valign not in ("top", "middle"):
        raise SheetError("Choose left, centre or right, and top or middle.")
    if style.family not in fonts.FAMILIES:
        raise SheetError("Choose a sans-serif, serif or typewriter font.")
    if not 1.0 <= style.first_scale <= 3.0:
        raise SheetError("The first line can be 1 to 3 times larger.")
    if not 1.0 <= style.line_spacing <= 2.0:
        raise SheetError("The line spacing must be from 1 to 2.")
    if not 0 <= style.inset <= MAX_PAD or style.inset * 2 >= min(sheet.width, sheet.height) - 2:
        raise SheetError("The space around each label is too big for the label.")
    if not 0 <= style.pad <= MAX_PAD or style.pad * 2 >= min(sheet.width, sheet.height) - 2 * style.inset:
        raise SheetError("The space inside a label is too big for the label.")
    if style.shape not in SHAPES:
        raise SheetError("Choose square corners, rounded corners, or a circle or oval.")
    if not 0 <= style.radius <= 50:
        raise SheetError("The corner rounding must be from 0 to 50 mm.")
    if not 0 <= style.border_width <= 5:
        raise SheetError("The border width must be from 0 to 5 mm.")
    if style.logo and (style.logo.position not in POSITIONS or not 10 <= style.logo.pct <= 70):
        raise SheetError("The logo goes at the top, at the left or behind the text, and takes 10 to 70 percent.")


def shape_inset(style: Style, w: float, h: float) -> float:
    """How far text must stay in from a label's edge so a rounded label's corners don't clip it (mm, each side)."""
    if style.shape == "rounded":
        return min(style.radius, w / 2, h / 2) * 0.29
    return 0.0


def art_rect(cell: dict, style: Style) -> dict:
    """The label itself inside its place: the place (what the cut lines go around) less the space around the label."""
    i = style.inset
    return {"x": round(cell["x"] + i, 3), "y": round(cell["y"] + i, 3), "w": round(cell["w"] - 2 * i, 3), "h": round(cell["h"] - 2 * i, 3)}


def text_box(cell: dict, style: Style) -> tuple[dict, dict | None]:
    """The rectangle a label's text may use (inside the shape and the padding), and where its logo goes."""
    x, y, w, h = cell["x"], cell["y"], cell["w"], cell["h"]
    if style.shape == "ellipse":
        ix, iy = w * (1 - INSET_ELLIPSE) / 2, h * (1 - INSET_ELLIPSE) / 2
    else:
        ix = iy = shape_inset(style, w, h)
    bx, by, bw, bh = x + ix, y + iy, w - 2 * ix, h - 2 * iy
    logo = None
    if style.logo and style.logo.position == "top":
        used = bh * style.logo.pct / 100
        logo = {"x": bx + style.pad, "y": by + style.pad, "w": bw - 2 * style.pad, "h": used - style.pad}
        by, bh = by + used, bh - used
    elif style.logo and style.logo.position == "left":
        used = bw * style.logo.pct / 100
        logo = {"x": bx + style.pad, "y": by + style.pad, "w": used - style.pad, "h": bh - 2 * style.pad}
        bx, bw = bx + used, bw - used
    elif style.logo:
        logo = {"x": x, "y": y, "w": w, "h": h, "cover": True}
    return {"x": bx + style.pad, "y": by + style.pad, "w": max(bw - 2 * style.pad, 0.1), "h": max(bh - 2 * style.pad, 0.1)}, logo


def fit_logo(box: dict, logo: Logo) -> dict:
    """The picture drawn inside `box`: all of it, centred (`contain`), or for a background cropped to cover the box."""
    ar = logo.width / logo.height
    if box.get("cover"):
        w = max(box["w"], box["h"] * ar)
        h = w / ar
    else:
        w = min(box["w"], box["h"] * ar)
        h = w / ar
    return {"x": round(box["x"] + (box["w"] - w) / 2, 3), "y": round(box["y"] + (box["h"] - h) / 2, 3), "w": round(w, 3), "h": round(h, 3),
            "cover": bool(box.get("cover"))}


@dataclass
class Result:
    sheet: Sheet
    pages: list[list[dict]]
    size: float
    labels: int
    style: Style
    warnings: list[str] = field(default_factory=list)
    blanks: int = 0  # how many of `labels` have no text

    def to_dict(self, page: int = 1) -> dict:
        page = min(max(page, 1), len(self.pages))
        st = self.style
        return {"page_w": self.sheet.paper_w, "page_h": self.sheet.paper_h, "per_sheet": self.sheet.per_sheet, "sheets": len(self.pages),
                "page": page, "size": self.size, "labels": self.labels, "blanks": self.blanks, "cells": self.pages[page - 1], "warnings": self.warnings,
                "orientation": self.sheet.orientation, "region": self.sheet.region, "area": self.sheet.area, "cols": self.sheet.cols, "rows": self.sheet.rows,
                "look": {"shape": st.shape, "radius": min(st.radius, (self.sheet.width - 2 * st.inset) / 2, (self.sheet.height - 2 * st.inset) / 2), "fill": st.fill, "border": st.border,
                         "border_width": st.border_width, "family": st.family, "italic": st.italic, "color": st.color}}


def _font(style: Style, i: int) -> str:
    return fonts.font_name(style.family, style.bold or (style.first_bold and i == 0), style.italic)


def _set(label: list[str], style: Style, size: float, box: dict) -> tuple[list[dict], bool, bool]:
    """The wrapped lines of one label at a size: [{t, font, size, color, bold}], whether they fit the box's height, and
    whether a word had to be split."""
    inner_w = box["w"] / MM_PER_PT
    lines: list[dict] = []
    split = False
    for i, para in enumerate(label):
        sz = size * (style.first_scale if i == 0 else 1.0)
        font = _font(style, i)
        for ln in fonts.wrap(para, sz, inner_w, font):
            lines.append({"t": ln, "font": font, "size": sz, "bold": "Bold" in font, "color": (style.first_color or style.color) if i == 0 else style.color})
        if any(fonts.text_width(x, sz, font) > inner_w for x in para.split(" ") if x):
            split = True
    height = sum(ln["size"] * style.line_spacing * MM_PER_PT for ln in lines)
    return lines, height <= box["h"] + 1e-9, split


def choose_size(labels: list[list[str]], style: Style, box: dict) -> tuple[float, int]:
    """One size for every label. Returns the size and how many labels still don't fit at it."""
    ok = lambda lb, s: (lambda r: r[1] and not r[2])(_set(lb, style, s, box))
    if style.size is not None:
        size = style.size
    else:
        unique = list({tuple(lb): lb for lb in labels}.values())
        size = MIN_SIZE
        s = MAX_SIZE
        while s >= MIN_SIZE:
            if all(ok(lb, s) for lb in unique):
                size = s
                break
            s -= SIZE_STEP
    return size, sum(1 for lb in labels if not ok(lb, size))


def compute(sheet: Sheet, labels: list[list[str]], style: Style, copies: int = 1, fill: bool = False, start: int = 1,
            blanks: int = 0, blanks_fill: bool = False) -> Result:
    """Lay the labels out. After them come `blanks` labels with no text (to write on by hand), or, with `blanks_fill`, as many as
    it takes to fill the last sheet; they share the sheet, colour, border and picture of the printed labels."""
    if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= 100:
        raise SheetError("Copies of each label must be a whole number from 1 to 100.")
    cap = sheet.per_sheet
    if isinstance(start, bool) or not isinstance(start, int) or not 1 <= start <= cap:
        raise SheetError(f"Start at a place from 1 to {cap}.")
    check_style(style, sheet)
    warnings: list[str] = list(sheet.warnings)
    lost: list[str] = []
    clean: list[list[str]] = []
    for lb in labels:
        row = []
        for para in lb:
            text, bad = fonts.encode(para)
            row.append(text)
            lost += [c for c in bad if c not in lost]
        clean.append(row)
    if lost:
        warnings.append(f"These characters can't be printed in this font and show as “?”: {' '.join(lost[:8])}")
    expanded = [lb for lb in clean for _ in range(copies)]
    if fill and expanded:
        total_pages = -(-(start - 1 + len(expanded)) // cap)
        target = total_pages * cap - (start - 1)
        base, i = list(expanded), 0
        while len(expanded) < target:
            expanded.append(base[i % len(base)])
            i += 1
    if isinstance(blanks, bool) or not isinstance(blanks, int) or not 0 <= blanks <= MAX_LABELS:
        raise SheetError(f"The number of blank labels must be from 0 to {MAX_LABELS}.")
    printed = len(expanded)
    if blanks_fill:
        expanded += [[]] * (max(1, -(-(start - 1 + len(expanded)) // cap)) * cap - (start - 1) - len(expanded))
    elif blanks:
        expanded += [[]] * blanks
    if len(expanded) > MAX_LABELS:
        raise SheetError(f"That is {len(expanded)} labels; the most is {MAX_LABELS} at a time.")
    n_sheets = max(1, -(-(start - 1 + len(expanded)) // cap))
    if n_sheets > MAX_SHEETS:
        raise SheetError(f"That needs {n_sheets} sheets; the most is {MAX_SHEETS} at a time.")
    box, _logo = text_box(art_rect(sheet.cells[0], style), style)
    size, bad = choose_size(expanded, style, box) if expanded else (style.size or 10.0, 0)
    if bad:
        warnings.append(f"{bad} label{'s' if bad != 1 else ''} {'has' if bad == 1 else 'have'} more text than fits at {size:g} pt. "
                        f"Use a smaller size, shorter text, or a bigger label.")
    slots = [None] * (start - 1) + expanded
    pages: list[list[dict]] = []
    for p in range(n_sheets):
        page = []
        for k, cell in enumerate(sheet.cells):
            idx = p * cap + k
            label = slots[idx] if idx < len(slots) else None
            entry = dict(cell)
            art = art_rect(cell, style)
            entry["art"] = art
            entry["lines"] = _place(art, label, style, size) if label is not None else []
            entry["used"] = label is not None  # empty places (before the start, after the last label) get no shape or picture
            if style.logo and label is not None:
                entry["logo"] = fit_logo(text_box(art, style)[1], style.logo)
            page.append(entry)
        pages.append(page)
    return Result(sheet, pages, size, len(expanded), style, warnings, blanks=len(expanded) - printed)


def _place(cell: dict, label: list[str], style: Style, size: float) -> list[dict]:
    box, _logo = text_box(cell, style)
    lines, _fits, _split = _set(label, style, size, box)
    heights = [ln["size"] * style.line_spacing * MM_PER_PT for ln in lines]
    block = sum(heights)
    top = box["y"]
    if style.valign == "middle":
        top = max(cell["y"], box["y"] + (box["h"] - block) / 2)
    out = []
    y = top
    for ln, lh in zip(lines, heights):
        width = fonts.text_width(ln["t"], ln["size"], ln["font"]) * MM_PER_PT
        if style.align == "left":
            x = box["x"]
        elif style.align == "center":
            x = box["x"] + (box["w"] - width) / 2
        else:
            x = box["x"] + box["w"] - width
        baseline = y + ((style.line_spacing - 1) / 2 + 0.8) * ln["size"] * MM_PER_PT
        out.append({"t": ln["t"], "x": round(x, 3), "y": round(baseline, 3), "bold": ln["bold"], "size": round(ln["size"], 3),
                    "font": ln["font"], "color": ln["color"]})
        y += lh
    return out
