"""Write label sheets as a PDF: one page per sheet, text in the standard Helvetica fonts, nothing scaled.

The page is exactly the paper size, so a printer set to "actual size" puts every label's text where the layout says."""
from __future__ import annotations

from app.tools.labels import fonts
from app.tools.labels.sheet import Result

PT_PER_MM = 72.0 / 25.4


def _n(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s or "0"


def _rounded(x: float, y: float, w: float, h: float, r: float) -> str:
    """A rectangle path with rounded corners (points, PDF coordinates); plain when r is 0."""
    if r <= 0.01:
        return f"{_n(x)} {_n(y)} {_n(w)} {_n(h)} re"
    k = r * 0.5523
    p = [f"{_n(x + r)} {_n(y)} m", f"{_n(x + w - r)} {_n(y)} l",
         f"{_n(x + w - r + k)} {_n(y)} {_n(x + w)} {_n(y + r - k)} {_n(x + w)} {_n(y + r)} c", f"{_n(x + w)} {_n(y + h - r)} l",
         f"{_n(x + w)} {_n(y + h - r + k)} {_n(x + w - r + k)} {_n(y + h)} {_n(x + w - r)} {_n(y + h)} c", f"{_n(x + r)} {_n(y + h)} l",
         f"{_n(x + r - k)} {_n(y + h)} {_n(x)} {_n(y + h - r + k)} {_n(x)} {_n(y + h - r)} c", f"{_n(x)} {_n(y + r)} l",
         f"{_n(x)} {_n(y + r - k)} {_n(x + r - k)} {_n(y)} {_n(x + r)} {_n(y)} c h"]
    return " ".join(p)


def build(result: Result, *, outline: bool = False, title: str = "Labels") -> bytes:
    sheet = result.sheet
    pw, ph = sheet.paper_w * PT_PER_MM, sheet.paper_h * PT_PER_MM
    size = result.size
    contents: list[bytes] = []
    for page in result.pages:
        ops: list[bytes] = []
        if outline:
            ops.append(b"0.25 w 0.55 G")
            for cell in page:
                x, w, h = cell["x"] * PT_PER_MM, cell["w"] * PT_PER_MM, cell["h"] * PT_PER_MM
                y = ph - (cell["y"] + cell["h"]) * PT_PER_MM
                ops.append((_rounded(x, y, w, h, sheet.radius * PT_PER_MM) + " S").encode())
        ops.append(b"0 g")
        for cell in page:
            for ln in cell["lines"]:
                if not ln["t"]:
                    continue
                f = "F2" if ln["bold"] else "F1"
                ops.append(f"BT /{f} {_n(size)} Tf {_n(ln['x'] * PT_PER_MM)} {_n(ph - ln['y'] * PT_PER_MM)} Td (".encode()
                           + fonts.pdf_escape(ln["t"]) + b") Tj ET")
        contents.append(b"\n".join(ops) + b"\n")

    objs: list[bytes] = []
    n_pages = len(contents)
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for name in ("Helvetica", "Helvetica-Bold"):
        objs.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode())
    for i, content in enumerate(contents):
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_n(pw)} {_n(ph)}] /Contents {6 + 2 * i} 0 R "
                     f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>").encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream")
    safe = "".join(c for c in title if c.isalnum() or c in " -_.,")[:80] or "Labels"
    objs.append(f"<< /Title ({safe}) /Producer (panda-print) >>".encode())
    info_obj = len(objs)

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {info_obj} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(buf)
