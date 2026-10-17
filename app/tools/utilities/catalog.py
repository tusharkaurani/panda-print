"""The list of print utilities: the built-in catalog, or the user's own print-utilities.yml.

Like links.yml, the user's file is re-read when it changes on disk. Problems never raise out of
`Catalog.get()`: a broken file is reported and the last good list (or the built-in one) keeps showing,
and a bad entry is skipped with a note while the others still load.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

import yaml

log = logging.getLogger(__name__)

ARTS = {"passport", "worksheet", "math", "handwriting", "colouring", "calendar", "graph", "lined", "dots",
        "pdf", "poster", "labels", "craft", "document"}
KNOWN_TOOL_KEYS = {"name", "url", "description", "art", "image"}
KNOWN_CATEGORY_KEYS = {"name", "description", "tools"}


class CatalogError(Exception):
    """The file as a whole is unusable. Written for humans."""


def _https(url) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def parse(text: str) -> tuple[list[dict], list[str]]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark else ""
        raise CatalogError(f"print-utilities.yml has a syntax error{where}: {getattr(e, 'problem', None) or 'invalid YAML'}.") from None
    entries = doc.get("categories") if isinstance(doc, dict) else doc
    if entries is None:
        return [], []
    if not isinstance(entries, list):
        raise CatalogError("print-utilities.yml must contain a list under a top-level 'categories:' key.")

    categories: list[dict] = []
    warnings: list[str] = []
    for i, raw in enumerate(entries, start=1):
        if not isinstance(raw, dict) or not _text(raw.get("name"), 80):
            warnings.append(f"category {i} skipped: it needs a 'name'.")
            continue
        name = _text(raw["name"], 80)
        for key in raw.keys() - KNOWN_CATEGORY_KEYS:
            warnings.append(f"category '{name}': unknown key {key!r} ignored.")
        tools = raw.get("tools")
        if not isinstance(tools, list):
            warnings.append(f"category '{name}' skipped: 'tools' must be a list.")
            continue
        items = []
        for j, t in enumerate(tools, start=1):
            label = f"'{name}' tool {j}"
            if not isinstance(t, dict) or not _text(t.get("name"), 80):
                warnings.append(f"{label} skipped: it needs a 'name'.")
                continue
            label = f"'{_text(t['name'], 80)}'"
            if not _https(t.get("url")):
                warnings.append(f"{label} skipped: 'url' must start with http:// or https://.")
                continue
            for key in t.keys() - KNOWN_TOOL_KEYS:
                warnings.append(f"{label}: unknown key {key!r} ignored.")
            art = t.get("art")
            if art is not None and art not in ARTS:
                warnings.append(f"{label}: unknown art {art!r} (available: {', '.join(sorted(ARTS))}); using a plain page.")
                art = None
            image = t.get("image")
            if image is not None and not _https(image):
                warnings.append(f"{label}: 'image' must be a http(s) URL; ignored.")
                image = None
            url = t["url"].strip()
            items.append({"name": _text(t["name"], 80), "url": url, "description": _text(t.get("description"), 200),
                          "art": art or "document", "image": image.strip() if image else None,
                          "host": re.sub(r"^www\.", "", urlsplit(url).netloc)})
        if not items:
            warnings.append(f"category '{name}' has no usable tools and is hidden.")
            continue
        categories.append({"id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or f"c{i}",
                           "name": name, "description": _text(raw.get("description"), 200), "tools": items})
    return categories, warnings


class Catalog:
    def __init__(self, builtin: Path, custom: Path):
        self.builtin, self.custom = builtin, custom
        self._lock = threading.Lock()
        self._stamp: tuple | None = None
        self._cached: dict | None = None

    def get(self) -> dict:
        with self._lock:
            try:
                stamp = (self.custom.stat().st_mtime_ns, self.custom.stat().st_size)
            except OSError:
                stamp = None
            if self._cached is not None and stamp == self._stamp:
                return self._cached
            self._stamp = stamp
            result = self._load(stamp is not None)
            self._cached = result
            return result

    def _load(self, has_custom: bool) -> dict:
        if has_custom:
            try:
                categories, warnings = parse(self.custom.read_text(encoding="utf-8"))
                return {"categories": categories, "warnings": warnings, "source": "custom", "error": None}
            except (CatalogError, OSError, UnicodeDecodeError) as e:
                message = str(e) if isinstance(e, CatalogError) else f"print-utilities.yml could not be read: {e}"
                log.warning("%s", message)
                built, warnings = parse(self.builtin.read_text(encoding="utf-8"))
                return {"categories": built, "warnings": warnings, "source": "built-in", "error": f"{message} Showing the built-in list."}
        categories, warnings = parse(self.builtin.read_text(encoding="utf-8"))
        return {"categories": categories, "warnings": warnings, "source": "built-in", "error": None}
