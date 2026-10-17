"""Launcher links: load and validate links.yml.

The file is re-read whenever it changes on disk, so edits show up without a
restart or rebuild. Problems never raise out of `LinksConfig.get()`:

* a broken file (bad YAML, wrong shape) yields an error message and keeps
  serving the last good config, if there is one;
* a bad entry is skipped with a warning while the others still load;
* a missing logo file just means the UI shows a letter tile.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from urllib.parse import urlsplit

import yaml

log = logging.getLogger(__name__)

BUILTIN_ICONS = {"link", "printer", "server", "globe"}
LOGO_EXTENSIONS = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".avif"}
KNOWN_KEYS = {"name", "url", "description", "logo", "new_tab"}


class LinksError(Exception):
    """The file as a whole is unusable. The message is written for humans."""


def _valid_url(url: str) -> bool:
    if url.startswith("/") and not url.startswith("//"):
        return True  # path on the same host, e.g. behind the reverse proxy
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def resolve_logo(logo, logos_dir: Path) -> tuple[str | None, str | None]:
    """Return (src, warning). src is None when the UI should draw a letter tile."""
    if logo in (None, ""):
        return None, None
    if not isinstance(logo, str):
        return None, "logo must be text (a file name, a URL, or builtin:<name>)"
    logo = logo.strip()
    if logo.startswith(("http://", "https://")):
        return (logo, None) if _valid_url(logo) else (None, f"logo URL {logo!r} is not valid")
    if logo.startswith("builtin:"):
        name = logo[len("builtin:"):]
        if name in BUILTIN_ICONS:
            return f"/static/icons/{name}.svg", None
        return None, f"unknown builtin icon {name!r} (available: {', '.join(sorted(BUILTIN_ICONS))})"
    path = Path(logo)
    if path.is_absolute() or ".." in path.parts:
        return None, f"logo {logo!r} must be a file inside the logos folder"
    if path.suffix.lower() not in LOGO_EXTENSIONS:
        return None, f"logo {logo!r} is not an image file ({', '.join(sorted(LOGO_EXTENSIONS))})"
    full = logos_dir / path
    try:
        mtime = int(full.stat().st_mtime)
    except OSError:
        return None, f"logo file {logo!r} not found in the logos folder; showing a letter tile"
    return f"/logos/{path.as_posix()}?v={mtime}", None


def parse_links(text: str, logos_dir: Path) -> tuple[list[dict], list[str]]:
    """Parse links.yml text. Raises LinksError for file-level problems."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark else ""
        problem = getattr(e, "problem", None) or "invalid YAML"
        raise LinksError(f"links.yml has a syntax error{where}: {problem}.") from None

    if doc is None:
        return [], []
    entries = doc.get("links") if isinstance(doc, dict) else doc
    if entries is None:
        return [], []
    if not isinstance(entries, list):
        raise LinksError("links.yml must contain a list under a top-level 'links:' key.")

    links: list[dict] = []
    warnings: list[str] = []
    for i, raw in enumerate(entries, start=1):
        label = f"entry {i}"
        if not isinstance(raw, dict):
            warnings.append(f"{label} skipped: each entry must be a set of 'key: value' lines.")
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"{label} skipped: 'name' is required.")
            continue
        name = name.strip()
        label = f"'{name}'"
        url = raw.get("url")
        if url is None:
            warnings.append(f"{label} skipped: 'url' is required.")
            continue
        if not isinstance(url, str) or not _valid_url(url.strip()):
            warnings.append(f"{label} skipped: 'url' must start with http:// or https:// (got {url!r}).")
            continue
        for key in raw.keys() - KNOWN_KEYS:
            warnings.append(f"{label}: unknown key {key!r} ignored.")
        desc = raw.get("description")
        if desc is not None and not isinstance(desc, str):
            warnings.append(f"{label}: 'description' should be text; ignored.")
            desc = None
        new_tab = raw.get("new_tab", True)
        if not isinstance(new_tab, bool):
            warnings.append(f"{label}: 'new_tab' should be true or false; using true.")
            new_tab = True
        logo, logo_warning = resolve_logo(raw.get("logo"), logos_dir)
        if logo_warning:
            warnings.append(f"{label}: {logo_warning}.")
        links.append({
            "name": name,
            "url": url.strip(),
            "description": (desc or "").strip(),
            "logo": logo,
            "new_tab": new_tab,
            "letter": next((ch.upper() for ch in name if ch.isalnum()), "?"),
            "hue": sum(map(ord, name)) % 360,
        })
    return links, warnings


class LinksConfig:
    def __init__(self, path: Path, logos_dir: Path, fallback: Path | None = None):
        self.path, self.logos_dir, self.fallback = path, logos_dir, fallback
        self._lock = threading.Lock()
        self._sig: tuple | None = None
        self._good: tuple[list[dict], list[str]] | None = None
        self._result: dict = {"links": [], "warnings": [], "error": None, "source": None}

    def _source(self) -> Path | None:
        for p in (self.path, self.fallback):
            if p and p.is_file():
                return p
        return None

    def _signature(self, src: Path | None) -> tuple:
        # Logo files matter too: dropping one in should fix a "not found" warning.
        try:
            logos = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in self.logos_dir.rglob("*") if p.is_file()))
        except OSError:
            logos = ()
        try:
            st = src.stat() if src else None
        except OSError:
            st = None
        return (str(src), st and (st.st_mtime_ns, st.st_size), logos)

    def get(self) -> dict:
        with self._lock:
            src = self._source()
            sig = self._signature(src)
            if sig == self._sig:
                return self._result
            self._sig = sig
            self._result = self._load(src)
            return self._result

    def _load(self, src: Path | None) -> dict:
        if src is None:
            return {"links": [], "warnings": [], "source": None,
                    "error": f"No links.yml found at {self.path}. Create it to add launcher tiles."}
        try:
            links, warnings = parse_links(src.read_text(encoding="utf-8"), self.logos_dir)
            self._good = (links, warnings)
            return {"links": links, "warnings": warnings, "error": None, "source": str(src)}
        except (LinksError, OSError, UnicodeDecodeError) as e:
            msg = str(e) if isinstance(e, LinksError) else f"Could not read {src.name}: {e}"
            log.warning("links config problem: %s", msg)
            if self._good:
                links, warnings = self._good
                return {"links": links, "warnings": warnings, "source": str(src), "stale": True,
                        "error": f"{msg} Showing the last working version until it is fixed."}
            return {"links": [], "warnings": [], "source": str(src), "error": msg}
