"""Text in the PDF: the standard Helvetica fonts every PDF viewer and printer already has, so nothing is embedded.

Widths are in 1/1000 of the font size for the characters of Windows code page 1252 (codes 32 to 255), the encoding the
PDF uses (/WinAnsiEncoding). That covers English and most Western European languages. A character outside it is
replaced with "?" and reported, so the person can see it instead of getting a silent gap.
Pure functions: the same numbers drive the live preview and the PDF.
"""
from __future__ import annotations

import unicodedata

_REGULAR = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
    556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556,
    833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500,
    556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
    334, 260, 334, 584, 0, 556, 0, 222, 556, 333, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0, 0, 222, 222,
    333, 333, 350, 556, 1000, 333, 1000, 500, 333, 944, 0, 500, 667, 278, 333, 556, 556, 556, 556, 260, 556, 333, 737,
    370, 556, 584, 333, 737, 552, 400, 549, 333, 333, 333, 576, 537, 333, 333, 333, 365, 556, 834, 834, 834, 611, 667,
    667, 667, 667, 667, 667, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778,
    584, 778, 722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556, 556, 889, 500, 556, 556, 556, 556, 278, 278,
    278, 278, 556, 556, 556, 556, 556, 556, 556, 549, 611, 556, 556, 556, 556, 500, 556, 500,
)

_BOLD = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
    556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
    722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611,
    556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389,
    280, 389, 584, 0, 556, 0, 278, 556, 500, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0, 0, 278, 278, 500,
    500, 350, 556, 1000, 333, 1000, 556, 333, 944, 0, 500, 667, 278, 333, 556, 556, 556, 556, 280, 556, 333, 737, 370,
    556, 584, 333, 737, 552, 400, 549, 333, 333, 333, 576, 556, 333, 333, 333, 365, 556, 834, 834, 834, 611, 722, 722,
    722, 722, 722, 722, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778, 584,
    778, 722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556, 556, 889, 556, 556, 556, 556, 556, 278, 278, 278,
    278, 611, 611, 611, 611, 611, 611, 611, 549, 611, 611, 611, 611, 611, 556, 611, 556,
)

FONT_NAMES = {False: "Helvetica", True: "Helvetica-Bold"}
_REPLACEMENT = "?"


def encode(text: str) -> tuple[str, list[str]]:
    """The text reduced to what the PDF font can show, and the characters that had to be replaced.
    Accented letters are kept when code page 1252 has them; control characters become spaces."""
    out: list[str] = []
    lost: list[str] = []
    for ch in unicodedata.normalize("NFC", text):
        if ch in "\t\r\n":
            out.append(" ")
        elif ord(ch) < 32 or ord(ch) == 127:
            continue
        else:
            try:
                code = ch.encode("cp1252")[0]
            except UnicodeEncodeError:
                code = 0
            if code >= 32 and _REGULAR[code - 32] > 0:
                out.append(ch)
            else:
                out.append(_REPLACEMENT)
                if ch not in lost:
                    lost.append(ch)
    return "".join(out), lost


def char_width(ch: str, bold: bool) -> int:
    try:
        code = ch.encode("cp1252")[0]
    except UnicodeEncodeError:
        return 0
    table = _BOLD if bold else _REGULAR
    return table[code - 32] if 32 <= code <= 255 else 0


def text_width(text: str, size: float, bold: bool = False) -> float:
    """Width of one line in points."""
    return sum(char_width(c, bold) for c in text) * size / 1000.0


def wrap(text: str, size: float, max_width: float, bold: bool = False) -> list[str]:
    """Break one paragraph into lines no wider than max_width points: at spaces, and inside a word only when the
    word alone is too wide. Empty text gives one empty line (a blank line in an address stays blank)."""
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = word if not cur else f"{cur} {word}"
        if text_width(trial, size, bold) <= max_width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while word and text_width(word, size, bold) > max_width:  # a very long word is split
            cut = 1
            while cut < len(word) and text_width(word[: cut + 1], size, bold) <= max_width:
                cut += 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    lines.append(cur)
    return lines


def pdf_escape(text: str) -> bytes:
    """A PDF string body in code page 1252, with the three characters that need a backslash."""
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
