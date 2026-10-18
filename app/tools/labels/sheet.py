"""Label sheets: where each label sits on the paper, what goes on it, and how the text is set.

Pure functions, lengths in millimetres (text sizes in points), origin at the page's top-left, y growing downwards.
The same numbers draw the live preview and write the PDF.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from app.tools.labels import fonts

MM_PER_PT = 25.4 / 72.0
MAX_LABELS = 5000       # after copies are counted
MAX_SHEETS = 100
MAX_SOURCE_CHARS = 400_000
MAX_ROWS = 2000
MIN_SIZE, MAX_SIZE, SIZE_STEP = 5.0, 24.0, 0.5
LINE_HEIGHT = 1.2       # of the text size
BASELINE = 0.9          # of the text size, from the top of a line
MAX_PAD = 20.0
PAPERS = {"a4": (210.0, 297.0), "letter": (215.9, 279.4)}


class SheetError(ValueError):
    """The request can't be laid out; the message is written for the person using the form."""


@dataclass(frozen=True)
class Sheet:
    paper_w: float
    paper_h: float
    cols: int
    rows: int
    width: float
    height: float
    left: float
    top: float
    gap_x: float
    gap_y: float
    radius: float = 0.0

    @property
    def per_sheet(self) -> int:
        return self.cols * self.rows

    def cells(self) -> list[dict]:
        return [{"x": round(self.left + c * (self.width + self.gap_x), 3), "y": round(self.top + r * (self.height + self.gap_y), 3),
                 "w": self.width, "h": self.height} for r in range(self.rows) for c in range(self.cols)]


def make_sheet(paper_w: float, paper_h: float, cols, rows, width, height, left, top, gap_x=0.0, gap_y=0.0, radius=0.0) -> Sheet:
    for name, v in (("columns", cols), ("rows", rows)):
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 30:
            raise SheetError(f"The number of {name} must be a whole number from 1 to 30.")
    nums = {"paper width": paper_w, "paper height": paper_h, "label width": width, "label height": height, "left margin": left,
            "top margin": top, "gap across": gap_x, "gap down": gap_y, "corner radius": radius}
    for name, v in nums.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1000:
            raise SheetError(f"The {name} must be a number.")
    pw, ph = min(paper_w, paper_h), max(paper_w, paper_h)
    if not (40 <= pw and ph <= 500):
        raise SheetError("The paper must be between 40 and 500 mm.")
    if not (5 <= width <= 300 and 5 <= height <= 300):
        raise SheetError("A label must be between 5 and 300 mm each way.")
    if min(left, top, gap_x, gap_y, radius) < 0 or max(gap_x, gap_y) > 50:
        raise SheetError("Margins and gaps can't be negative, and a gap is at most 50 mm.")
    sheet = Sheet(pw, ph, cols, rows, float(width), float(height), float(left), float(top), float(gap_x), float(gap_y),
                  float(min(radius, width / 2, height / 2)))
    need_w = left + cols * width + (cols - 1) * gap_x
    need_h = top + rows * height + (rows - 1) * gap_y
    if need_w > pw + 0.01:
        raise SheetError(f"{cols} labels across need {need_w:.1f} mm, but the paper is {pw:g} mm wide. Make the labels narrower or use fewer columns.")
    if need_h > ph + 0.01:
        raise SheetError(f"{rows} rows of labels need {need_h:.1f} mm, but the paper is {ph:g} mm tall. Make the labels shorter or use fewer rows.")
    return sheet


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


# ------------------------------------------------------------------ setting the text
@dataclass
class Style:
    size: float | None = None     # points; None = the largest that fits every label
    align: str = "left"           # left | center | right
    valign: str = "middle"        # top | middle
    bold: bool = False
    first_bold: bool = False
    pad: float = 2.0              # mm inside each label


@dataclass
class Result:
    sheet: Sheet
    pages: list[list[dict]]
    size: float
    labels: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, page: int = 1) -> dict:
        page = min(max(page, 1), len(self.pages))
        return {"page_w": self.sheet.paper_w, "page_h": self.sheet.paper_h, "per_sheet": self.sheet.per_sheet, "sheets": len(self.pages),
                "page": page, "size": self.size, "labels": self.labels, "radius": self.sheet.radius,
                "cells": self.pages[page - 1], "warnings": self.warnings}


def _set(label: list[str], style: Style, size: float, w_mm: float, h_mm: float) -> tuple[list[tuple[str, bool]], bool, bool]:
    """The wrapped lines of one label at a size: (text, bold) pairs, whether it fits the height, whether a word had to be split."""
    inner_w = (w_mm - 2 * style.pad) / MM_PER_PT
    lines: list[tuple[str, bool]] = []
    split = False
    for i, para in enumerate(label):
        bold = style.bold or (style.first_bold and i == 0)
        for ln in fonts.wrap(para, size, inner_w, bold):
            lines.append((ln, bold))
        words = [x for x in para.split(" ") if x]
        if any(fonts.text_width(x, size, bold) > inner_w for x in words):
            split = True
    fits = len(lines) * size * LINE_HEIGHT * MM_PER_PT <= h_mm - 2 * style.pad + 1e-9
    return lines, fits, split


def choose_size(labels: list[list[str]], style: Style, w_mm: float, h_mm: float) -> tuple[float, int]:
    """One size for every label. Returns the size and how many labels still don't fit at it."""
    if style.size is not None:
        size = style.size
    else:
        unique = {tuple(lb): lb for lb in labels}.values()
        size = MIN_SIZE
        s = MAX_SIZE
        while s >= MIN_SIZE:
            if all((lambda r: r[1] and not r[2])(_set(lb, style, s, w_mm, h_mm)) for lb in unique):
                size = s
                break
            s -= SIZE_STEP
    bad = sum(1 for lb in labels if not (lambda r: r[1] and not r[2])(_set(lb, style, size, w_mm, h_mm)))
    return size, bad


def compute(sheet: Sheet, labels: list[list[str]], style: Style, copies: int = 1, fill: bool = False, start: int = 1) -> Result:
    if not 1 <= copies <= 100:
        raise SheetError("Copies of each label must be a whole number from 1 to 100.")
    cap = sheet.per_sheet
    if isinstance(start, bool) or not isinstance(start, int) or not 1 <= start <= cap:
        raise SheetError(f"Start at a place from 1 to {cap}.")
    if style.size is not None and not MIN_SIZE <= style.size <= 72:
        raise SheetError("The text size must be between 5 and 72 points.")
    if not 0 <= style.pad <= MAX_PAD or style.pad * 2 >= min(sheet.width, sheet.height):
        raise SheetError("The space inside a label is too big for the label.")
    if style.align not in ("left", "center", "right") or style.valign not in ("top", "middle"):
        raise SheetError("Choose left, centre or right, and top or middle.")
    warnings: list[str] = []
    # clean the text for the font, remembering what couldn't be shown
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
        shown = " ".join(lost[:8])
        warnings.append(f"These characters can't be printed in this font and show as “?”: {shown}")
    expanded = [lb for lb in clean for _ in range(copies)]
    if not expanded:
        expanded = []
    if fill and expanded:
        total_pages = -(-(start - 1 + len(expanded)) // cap)
        target = total_pages * cap - (start - 1)
        i = 0
        base = list(expanded)
        while len(expanded) < target:
            expanded.append(base[i % len(base)])
            i += 1
    if len(expanded) > MAX_LABELS:
        raise SheetError(f"That is {len(expanded)} labels; the most is {MAX_LABELS} at a time.")
    n_sheets = max(1, -(-(start - 1 + len(expanded)) // cap))
    if n_sheets > MAX_SHEETS:
        raise SheetError(f"That needs {n_sheets} sheets; the most is {MAX_SHEETS} at a time.")
    size, bad = choose_size(expanded, style, sheet.width, sheet.height) if expanded else (style.size or 10.0, 0)
    if bad:
        what = "has" if bad == 1 else "have"
        warnings.append(f"{bad} label{'s' if bad != 1 else ''} {what} more text than fits at {size:g} pt. "
                        f"Use a smaller size, shorter text, or a bigger label.")
    grid = sheet.cells()
    slots = [None] * (start - 1) + expanded
    pages: list[list[dict]] = []
    for p in range(n_sheets):
        page = []
        for k, cell in enumerate(grid):
            idx = p * cap + k
            label = slots[idx] if idx < len(slots) else None
            entry = dict(cell)
            entry["lines"] = _place(cell, label, style, size) if label is not None else []
            page.append(entry)
        pages.append(page)
    return Result(sheet, pages, size, len(expanded), warnings)


def _place(cell: dict, label: list[str], style: Style, size: float) -> list[dict]:
    lines, _fits, _split = _set(label, style, size, cell["w"], cell["h"])
    lh = size * LINE_HEIGHT * MM_PER_PT
    block = len(lines) * lh
    top = cell["y"] + style.pad
    if style.valign == "middle":
        top = max(cell["y"], cell["y"] + (cell["h"] - block) / 2)
    out = []
    for i, (text, bold) in enumerate(lines):
        width = fonts.text_width(text, size, bold) * MM_PER_PT
        if style.align == "left":
            x = cell["x"] + style.pad
        elif style.align == "center":
            x = cell["x"] + (cell["w"] - width) / 2
        else:
            x = cell["x"] + cell["w"] - style.pad - width
        out.append({"t": text, "x": round(x, 3), "y": round(top + i * lh + size * BASELINE * MM_PER_PT, 3), "bold": bold})
    return out
