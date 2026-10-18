"""Write label sheets as a PDF: one page per sheet, text in the standard PDF fonts, nothing scaled.

The page is exactly the paper size, so a printer set to "actual size" puts every label where the layout says.
Shapes, colours and the optional logo (a JPEG, embedded untouched) are drawn with ordinary PDF operators."""
from __future__ import annotations

from app.tools.labels import fonts
from app.tools.labels.sheet import Result
from app.tools.photosheet.pdfsheet import Jpeg

PT_PER_MM = 72.0 / 25.4
KAPPA = 0.5523


def _n(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s or "0"


def _rgb(hex_color: str) -> str:
    return " ".join(_n(int(hex_color[i:i + 2], 16) / 255) for i in (1, 3, 5))


def _path(shape: str, x: float, y: float, w: float, h: float, r: float) -> str:
    """The outline of a label as PDF path operators (points, y upwards); a rectangle, rounded rectangle or ellipse."""
    if shape == "ellipse":
        rx, ry, cx, cy = w / 2, h / 2, x + w / 2, y + h / 2
        kx, ky = rx * KAPPA, ry * KAPPA
        return (f"{_n(cx + rx)} {_n(cy)} m "
                f"{_n(cx + rx)} {_n(cy + ky)} {_n(cx + kx)} {_n(cy + ry)} {_n(cx)} {_n(cy + ry)} c "
                f"{_n(cx - kx)} {_n(cy + ry)} {_n(cx - rx)} {_n(cy + ky)} {_n(cx - rx)} {_n(cy)} c "
                f"{_n(cx - rx)} {_n(cy - ky)} {_n(cx - kx)} {_n(cy - ry)} {_n(cx)} {_n(cy - ry)} c "
                f"{_n(cx + kx)} {_n(cy - ry)} {_n(cx + rx)} {_n(cy - ky)} {_n(cx + rx)} {_n(cy)} c h")
    r = min(r, w / 2, h / 2) if shape == "rounded" else 0.0
    if r <= 0.01:
        return f"{_n(x)} {_n(y)} {_n(w)} {_n(h)} re"
    k = r * KAPPA
    return " ".join([
        f"{_n(x + r)} {_n(y)} m", f"{_n(x + w - r)} {_n(y)} l",
        f"{_n(x + w - r + k)} {_n(y)} {_n(x + w)} {_n(y + r - k)} {_n(x + w)} {_n(y + r)} c", f"{_n(x + w)} {_n(y + h - r)} l",
        f"{_n(x + w)} {_n(y + h - r + k)} {_n(x + w - r + k)} {_n(y + h)} {_n(x + w - r)} {_n(y + h)} c", f"{_n(x + r)} {_n(y + h)} l",
        f"{_n(x + r - k)} {_n(y + h)} {_n(x)} {_n(y + h - r + k)} {_n(x)} {_n(y + h - r)} c", f"{_n(x)} {_n(y + r)} l",
        f"{_n(x)} {_n(y + r - k)} {_n(x + r - k)} {_n(y)} {_n(x + r)} {_n(y)} c h"])


def build(result: Result, *, outline: bool = False, title: str = "Labels", logo: tuple[bytes, Jpeg] | None = None, for_media: bool = False) -> bytes:
    """The PDF bytes. `outline` draws a thin guide around every place (to test on plain paper). `logo` is the picture for
    every label. `for_media`: a landscape layout is turned onto a portrait page, the way paper is fed into the printer."""
    sheet, st = result.sheet, result.style
    lw, lh = sheet.paper_w, sheet.paper_h
    rotate = for_media and lw > lh
    pw, ph = (lh, lw) if rotate else (lw, lh)
    radius = min(st.radius, sheet.width / 2, sheet.height / 2)
    names: dict[str, str] = {}
    for page in result.pages:
        for cell in page:
            for ln in cell["lines"]:
                names.setdefault(ln["font"], f"F{len(names) + 1}")
    names.setdefault("Helvetica", f"F{len(names) + 1}")

    def pt(v: float) -> float:
        return v * PT_PER_MM

    def rect(c: dict) -> tuple[float, float, float, float]:
        return pt(c["x"]), pt(lh - c["y"] - c["h"]), pt(c["w"]), pt(c["h"])

    contents: list[bytes] = []
    for page in result.pages:
        ops: list[bytes] = []
        add = lambda text: ops.append(text.encode("latin-1"))
        if rotate:
            add(f"0 1 -1 0 {_n(pt(lh))} 0 cm")  # a quarter turn: the sheet's top-left lands at the page's top-right
        if outline:
            add("q 0.25 w 0.6 G")
            for c in page:
                add(_path("rect", *rect(c), 0) + " S")
            add("Q")
        for c in page:
            if not c.get("used"):
                continue
            x, y, w, h = rect(c)
            path = _path(st.shape, x, y, w, h, pt(radius))
            if st.fill:
                add(f"q {_rgb(st.fill)} rg {path} f Q")
            if logo and c.get("logo"):
                lg = c["logo"]
                add(f"q {path} W n {_n(pt(lg['w']))} 0 0 {_n(pt(lg['h']))} {_n(pt(lg['x']))} {_n(pt(lh - lg['y'] - lg['h']))} cm /Im0 Do Q")
            if st.border and st.border_width > 0:
                add(f"q {_rgb(st.border)} RG {_n(pt(st.border_width))} w {path} S Q")
            for ln in c["lines"]:
                if ln["t"]:
                    ops.append(f"{_rgb(ln['color'])} rg BT /{names[ln['font']]} {_n(ln['size'])} Tf {_n(pt(ln['x']))} {_n(pt(lh - ln['y']))} Td (".encode("latin-1")
                               + fonts.pdf_escape(ln["t"]) + b") Tj ET")
        contents.append(b"\n".join(ops) + b"\n")

    objs: list[bytes] = [b"", b""]  # catalog and pages, filled in below
    font_ids = {}
    for name in names:
        objs.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode())
        font_ids[name] = len(objs)
    resources = "/Font << " + " ".join(f"/{names[n]} {font_ids[n]} 0 R" for n in names) + " >>"
    if logo:
        data, info = logo
        cs = "/DeviceRGB" if info.components == 3 else "/DeviceGray"
        objs.append((f"<< /Type /XObject /Subtype /Image /Width {info.width} /Height {info.height} /ColorSpace {cs} "
                     f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n").encode() + data + b"\nendstream")
        resources += f" /XObject << /Im0 {len(objs)} 0 R >>"
    first_page = len(objs) + 1
    for i, content in enumerate(contents):
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_n(pt(pw))} {_n(pt(ph))}] /Contents {first_page + 2 * i + 1} 0 R "
                     f"/Resources << {resources} >> >>").encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream")
    kids = " ".join(f"{first_page + 2 * i} 0 R" for i in range(len(contents)))
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(contents)} >>".encode()
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
