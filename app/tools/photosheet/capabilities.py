"""What a CUPS queue offers for a photo job, turned into the choices the form shows. Pure functions."""
from __future__ import annotations

from app.tools.photosheet.layout import PAPERS, parse_media

_COMMON = [(w, h) for _label, w, h in PAPERS.values()]  # the sizes people print photos and documents on

MEDIA_TYPE_LABELS = {
    "stationery": "Plain paper",
    "stationery-inkjet": "Inkjet paper",
    "stationery-coated": "Coated paper",
    "stationery-heavyweight": "Heavy paper",
    "stationery-lightweight": "Light paper",
    "photographic": "Photo paper",
    "photographic-glossy": "Glossy photo paper",
    "photographic-high-gloss": "High-gloss photo paper",
    "photographic-semi-gloss": "Semi-gloss photo paper",
    "photographic-satin": "Satin photo paper",
    "photographic-matte": "Matte photo paper",
    "photographic-film": "Photo film",
    "cardstock": "Card stock",
    "envelope": "Envelope",
    "labels": "Labels",
    "transparency": "Transparency",
}
QUALITY_LABELS = {3: "Draft", 4: "Normal", 5: "High"}
DEFAULT_MIN_MARGIN_MM = 3.0


def media_type_label(value: str) -> str:
    if value in MEDIA_TYPE_LABELS:
        return MEDIA_TYPE_LABELS[value]
    text = value.split(".", 1)[-1].replace("-", " ").replace("_", " ").strip()  # "com.brother-bp-71" -> "brother bp 71"
    return text[:1].upper() + text[1:] if text else value


def _first(attrs: dict, name: str):
    v = attrs.get(name)
    return v[0] if v else None


def fallback() -> dict:
    """Paper sizes to offer when the printer can't be asked (no CUPS): layout and download still work."""
    return {"media": [{"keyword": None, "label": label, "width": w, "height": h, "borderless": False, "key": key}
                      for key, (label, w, h) in PAPERS.items()],
            "media_types": [], "qualities": [], "resolutions": [], "default_media": None, "default_media_type": None,
            "default_quality": None, "default_resolution": None, "min_margin": DEFAULT_MIN_MARGIN_MM,
            "actual_size": True, "pdf": True}


def from_attrs(attrs: dict) -> dict:
    """The choices for one CUPS queue from its Get-Printer-Attributes answer."""
    default = _first(attrs, "media-default")
    seen, media = set(), []
    for k in attrs.get("media-supported", []):
        if str(k).startswith(("custom_min_", "custom_max_")):  # the ends of the custom-size range, not papers
            continue
        info = parse_media(str(k))
        if info and k not in seen:
            seen.add(k)
            media.append(info)
    common = lambda m: any(abs(m["width"] - w) < 0.6 and abs(m["height"] - h) < 0.6 for w, h in _COMMON)
    # the printer's default, then everyday sizes, then the rest (envelopes and so on); borderless after plain in each
    media.sort(key=lambda m: (m["keyword"] != default, m["borderless"], not common(m), m["width"] * m["height"]))

    types = [{"value": str(t), "label": media_type_label(str(t))} for t in attrs.get("media-type-supported", [])]
    qualities = [{"value": int(q), "label": QUALITY_LABELS.get(int(q), str(q))} for q in attrs.get("print-quality-supported", [])
                 if int(q) in QUALITY_LABELS]
    resolutions = []
    for r in attrs.get("printer-resolution-supported", []):
        x, y = int(r[0]), int(r[1])
        unit = r[2] if len(r) > 2 else "dpi"
        label = f"{x} dpi" if x == y else f"{x} × {y} dpi"
        resolutions.append({"value": [x, y], "label": label if unit == "dpi" else f"{x} × {y} dots per {unit}"})
    margins = [int(v) for name in ("media-left-margin-supported", "media-right-margin-supported", "media-top-margin-supported",
                                   "media-bottom-margin-supported") for v in attrs.get(name, []) if int(v) > 0]
    scaling = attrs.get("print-scaling-supported")
    formats = attrs.get("document-format-supported")
    d_res = _first(attrs, "printer-resolution-default")
    return {
        "media": media, "media_types": types, "qualities": qualities, "resolutions": resolutions,
        "default_media": default,
        "default_media_type": types[0]["value"] if types else None,
        "default_quality": int(_first(attrs, "print-quality-default") or 4) if qualities else None,
        "default_resolution": [int(d_res[0]), int(d_res[1])] if d_res else None,
        "min_margin": round(min(margins) / 100.0, 1) if margins else DEFAULT_MIN_MARGIN_MM,  # unprintable edge, in mm
        "actual_size": True if scaling is None else "none" in scaling,  # can the queue print at exactly 100%
        "pdf": True if formats is None else "application/pdf" in formats,
    }
