"""Write a photo sheet as a PDF: one page, one embedded JPEG drawn once per cell.

No imaging library: the browser hands over a JPEG, which goes into the PDF untouched (DCTDecode), so the photo
keeps every pixel. The page is exactly the paper size, and nothing is scaled, so a printer set to "actual size"
prints each photo at exactly the size you asked for.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from app.tools.photosheet.layout import Layout, MM_PER_INCH

MAX_PIXELS = 120_000_000  # a guard against absurd images (a viewer would run out of memory)
PT_PER_MM = 72.0 / MM_PER_INCH


class ImageError(ValueError):
    """The picture can't be used; the message is written for the person who uploaded it."""


@dataclass(frozen=True)
class Jpeg:
    width: int
    height: int
    components: int  # 1 grey, 3 colour


def jpeg_info(data: bytes) -> Jpeg:
    """Read the size and colour model from a JPEG's start-of-frame marker. Refuses anything else."""
    if data[:3] != b"\xff\xd8\xff":
        raise ImageError("That isn't a JPEG picture.")
    pos, n = 2, len(data)
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in (0x01, *range(0xD0, 0xD9)):  # markers without a length
            pos += 2
            continue
        length = struct.unpack_from(">H", data, pos + 2)[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 10 > n:
                break
            precision, height, width, comps = struct.unpack_from(">BHHB", data, pos + 4)
            if precision != 8:
                raise ImageError("Only ordinary 8-bit pictures are supported.")
            if comps not in (1, 3):
                raise ImageError("Only colour or grey pictures are supported (not CMYK).")
            if not width or not height or width * height > MAX_PIXELS:
                raise ImageError("That picture's size isn't usable.")
            return Jpeg(width, height, comps)
        pos += 2 + length
    raise ImageError("That JPEG is damaged.")


def _num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s or "0"


def effective_dpi(info: Jpeg, cell_w: float, cell_h: float, fit: str) -> float:
    """Pixels per inch the picture gets in a photo cell (the lower of the two directions when it is cropped)."""
    scale = _scale(info, cell_w, cell_h, fit)  # mm per pixel
    return MM_PER_INCH / scale


def _scale(info: Jpeg, cw: float, ch: float, fit: str) -> float:
    s_w, s_h = cw / info.width, ch / info.height
    return max(s_w, s_h) if fit == "fill" else min(s_w, s_h)


def build_pdf(layout: Layout, jpeg: bytes, info: Jpeg, **kw) -> bytes:
    """A sheet of one photo (see build_sheet)."""
    return build_sheet(layout, [(jpeg, info)], None, **kw)


def build_sheet(layout: Layout, images: list[tuple[bytes, Jpeg]], assign: list[int] | None, *, fit: str = "fill",
                crop_x: float = 0.5, crop_y: float = 0.5, marks: bool = True, outline: bool = False,
                for_media: bool = False, title: str = "Photo sheet") -> bytes:
    """The PDF bytes. `images` are the photos (JPEG bytes and their size); `assign[i]` is the photo in place i
    (default: the first photo everywhere). `fit`: "fill" crops the picture to the photo size, "fit" shows all of it on white.
    `for_media`: a landscape layout is rotated onto a portrait page, the way the paper is fed into the printer
    (so the printer needs no rotating of its own). The picture is never rotated relative to the sheet."""
    lw, lh = layout.page_w, layout.page_h
    rotate = for_media and lw > lh
    pw_mm, ph_mm = (lh, lw) if rotate else (lw, lh)

    def pt(v: float) -> str:
        return _num(v * PT_PER_MM)

    out: list[str] = []
    if rotate:  # (x, y) -> (page height - y, x): a quarter turn, with the sheet's top-left now at the page's top-right
        out.append(f"0 1 -1 0 {pt(lh)} 0 cm")

    def ypdf(y_top: float, h: float = 0.0) -> float:
        return lh - y_top - h  # layout y grows downwards, PDF y grows upwards

    if outline or marks:
        out.append("0.25 w 0.2 G")
    for n, cell in enumerate(layout.cells):
        which = assign[n] if assign and n < len(assign) else 0
        info = images[which][1]
        x, w, h = cell["x"], cell["w"], cell["h"]
        y = ypdf(cell["y"], h)
        scale = _scale(info, w, h, fit)
        dw, dh = info.width * scale, info.height * scale
        # where the picture sits inside the cell: centred, or shifted when it is cropped
        ix = x + (w - dw) * (crop_x if dw > w else 0.5)
        iy = y + (h - dh) * ((1 - crop_y) if dh > h else 0.5)
        out.append("q")
        out.append(f"{pt(x)} {pt(y)} {pt(w)} {pt(h)} re W n")
        out.append(f"{pt(dw)} 0 0 {pt(dh)} {pt(ix)} {pt(iy)} cm /Im{which} Do")
        out.append("Q")
        if outline:
            out.append(f"{pt(x)} {pt(y)} {pt(w)} {pt(h)} re S")
    if marks and layout.cells:
        g, tick, off = layout.grid, 3.0, 1.0
        xs = sorted({c["x"] for c in layout.cells} | {c["x"] + c["w"] for c in layout.cells})
        ys = sorted({c["y"] for c in layout.cells} | {c["y"] + c["h"] for c in layout.cells})
        top, bottom = g["y"], g["y"] + g["h"]
        left, right = g["x"], g["x"] + g["w"]

        def line(x1, y1, x2, y2):  # layout coordinates, y down
            out.append(f"{pt(x1)} {pt(ypdf(y1))} m {pt(x2)} {pt(ypdf(y2))} l S")
        for x in xs:  # vertical cut marks above and below the grid, kept on the page
            if top - off - tick >= 0:
                line(x, top - off - tick, x, top - off)
            if bottom + off + tick <= lh:
                line(x, bottom + off, x, bottom + off + tick)
        for y in ys:
            if left - off - tick >= 0:
                line(left - off - tick, y, left - off, y)
            if right + off + tick <= lw:
                line(right + off, y, right + off + tick, y)

    content = ("\n".join(out) + "\n").encode()
    objs: list[bytes] = []
    first_image = 5
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    media = f"[0 0 {pt(pw_mm)} {pt(ph_mm)}]"
    xobjects = " ".join(f"/Im{i} {first_image + i} 0 R" for i in range(len(images)))
    objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox {media} /Contents 4 0 R "
                f"/Resources << /XObject << {xobjects} >> >> >>".encode())
    objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream")
    for jpeg, info in images:
        cs = "/DeviceRGB" if info.components == 3 else "/DeviceGray"
        objs.append((f"<< /Type /XObject /Subtype /Image /Width {info.width} /Height {info.height} /ColorSpace {cs} "
                     f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n").encode() + jpeg + b"\nendstream")
    safe = "".join(c for c in title if c.isalnum() or c in " -_.,")[:80] or "Photo sheet"
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
