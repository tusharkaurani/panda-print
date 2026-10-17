"""Pages the app can print instead of the plain nozzle-check test page.

A shared library of uploaded files (PDF, PNG, JPEG), and per printer an order, an on/off per page, and a
position in the rotation. The plain test page is always available as the built-in item "standard".

The rules at the top are pure (no I/O) so they can be tested exhaustively; `Library` does the storage.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.db import Database

MAX_THUMB_BYTES = 200 * 1024  # a thumbnail is a small picture; anything bigger is not one
STANDARD = "standard"  # the built-in nozzle-check test page (four colour bars)
STANDARD_NAME = "Standard test page"
KINDS = {  # what we accept, by what the file really is (not by its name)
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


class PageError(Exception):
    """A user-facing reason an upload or a change was refused."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ what a file is

def sniff(data: bytes) -> str | None:
    """The mime type from the file's own bytes: PDF, PNG or JPEG, else None. PostScript is refused on purpose."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b"%PDF-" in data[:1024]:
        return "application/pdf"
    return None


def count_pdf_pages(data: bytes) -> int:
    """Best-effort page count without a PDF library: page objects, else the page tree's /Count. At least 1."""
    objects = len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", data))
    counts = [int(n) for n in re.findall(rb"/Count\s+(\d+)", data)]
    return max(1, objects, max(counts, default=0))


def clean_name(filename: str) -> str:
    """A display name from an upload's file name: no folders, no extension, plain characters, short."""
    stem = Path(str(filename or "").replace("\\", "/")).stem
    stem = re.sub(r"[\x00-\x1f\x7f]", "", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem[:80] or "Untitled page"


# ------------------------------------------------------------------ rotation (pure)

@dataclass(frozen=True)
class Choice:
    id: str
    plain: bool = False  # the standard page, because it was this print's turn to be plain (every Nth)


def choose(enabled: list[str], last_id: str | None, count: int, plain_every: int) -> Choice:
    """Which page the next rotation print uses.

    `enabled` is the printer's enabled pages in order (the standard page may be among them), `last_id` the one
    printed last time, `count` how many rotation prints there have been, `plain_every` N > 0 makes every Nth
    print the standard page on top of the rotation (so every colour gets exercised now and then).
    Deleted or disabled pages are skipped; with nothing enabled the standard page is used.
    """
    if plain_every > 0 and (count + 1) % plain_every == 0:
        return Choice(STANDARD, plain=True)
    if not enabled:
        return Choice(STANDARD)
    if last_id in enabled:
        return Choice(enabled[(enabled.index(last_id) + 1) % len(enabled)])
    return Choice(enabled[0])  # first time, or the last one was removed/disabled


def arrange(library_ids: list[str], saved: list[tuple[str, int, bool]]) -> list[tuple[str, bool]]:
    """A printer's full ordered list [(id, enabled)] from the library (oldest first) and its saved
    (id, position, enabled) rows. Pages it has never ordered go last, enabled; the standard page defaults to first."""
    known = [STANDARD, *library_ids]
    saved_by_id = {pid: (pos, en) for pid, pos, en in saved if pid in known}
    ordered = sorted((pid for pid in known if pid in saved_by_id), key=lambda pid: saved_by_id[pid][0])
    fresh = [pid for pid in known if pid not in saved_by_id]
    if STANDARD in fresh:  # never ordered: it leads the list
        fresh.remove(STANDARD)
        ordered.insert(0, STANDARD)
    return [(pid, saved_by_id[pid][1] if pid in saved_by_id else True) for pid in ordered + fresh]


# ------------------------------------------------------------------ the library on disk

MIGRATION = """
    CREATE TABLE IF NOT EXISTS mt_pages (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        pages INTEGER NOT NULL DEFAULT 1,
        sha256 TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS mt_pages_sha ON mt_pages(sha256);
    CREATE TABLE IF NOT EXISTS mt_rotation (
        printer_id TEXT NOT NULL,
        page_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (printer_id, page_id)
    );
"""


class Library:
    """Uploaded pages shared by every printer. Files live in <data dir>/pages under random ids."""

    def __init__(self, db: Database, directory: Path, max_file_bytes: int, max_files: int, max_total_bytes: int,
                 clock=time.time):
        self.db, self.dir, self.clock = db, Path(directory).resolve(), clock  # absolute: send_file would read a relative path from the app folder
        self.max_file, self.max_files, self.max_total = max_file_bytes, max_files, max_total_bytes

    @classmethod
    def from_settings(cls, db: Database, settings, clock=time.time) -> "Library":
        return cls(db, settings.data_dir / "pages", max_file_bytes=settings.max_page_mb * 2**20,
                   max_files=settings.max_pages, max_total_bytes=settings.max_library_mb * 2**20, clock=clock)

    def limits(self) -> dict:
        return {"max_file_mb": self.max_file // 2**20, "max_files": self.max_files,
                "max_total_mb": self.max_total // 2**20}

    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "name": r["name"], "mime": r["mime"], "size": r["size"], "pages": r["pages"],
                "created_at": r["created_at"], "thumb": bool(r["has_thumb"])}

    def list(self) -> list[dict]:
        return [self._row(r) for r in self.db.query("SELECT * FROM mt_pages ORDER BY created_at, rowid")]

    def get(self, page_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM mt_pages WHERE id=?", (page_id,))
        return self._row(r) if r else None

    def path(self, page: dict) -> Path:
        return self.dir / f"{page['id']}{KINDS[page['mime']]}"

    def thumb_path(self, page: dict) -> Path:
        return self.dir / f"{page['id']}.thumb.png"

    def read(self, page_id: str) -> tuple[dict, bytes] | None:
        page = self.get(page_id)
        if not page:
            return None
        try:
            return page, self.path(page).read_bytes()
        except OSError:
            return None  # the file was removed behind our back

    def add(self, data: bytes, filename: str, thumb: bytes | None = None) -> dict:
        if not data:
            raise PageError("That file is empty.")
        if len(data) > self.max_file:
            raise PageError(f"That file is larger than {self.max_file // 2**20} MB.", 413)
        mime = sniff(data)
        if mime is None:
            raise PageError("Only PDF, PNG and JPEG files can be added.", 415)
        used = self.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS total FROM mt_pages")
        if used["n"] >= self.max_files:
            raise PageError(f"The library is full ({self.max_files} pages). Delete one first.", 409)
        if used["total"] + len(data) > self.max_total:
            raise PageError(f"The library would pass {self.max_total // 2**20} MB. Delete a page first.", 409)
        digest = hashlib.sha256(data).hexdigest()
        same = self.db.one("SELECT name FROM mt_pages WHERE sha256=?", (digest,))
        if same:
            raise PageError(f"That file is already in the library as “{same['name']}”.", 409)
        page = {"id": secrets.token_hex(8), "name": clean_name(filename), "mime": mime, "size": len(data),
                "pages": count_pdf_pages(data) if mime == "application/pdf" else 1, "created_at": self.clock()}
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.path(page)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, target)  # never leave a half-written file under its real name
        # a small picture of the page, drawn by the browser that uploaded it; optional, and ignored unless it really is a PNG
        has_thumb = bool(thumb) and thumb[:8] == b"\x89PNG\r\n\x1a\n" and len(thumb) <= MAX_THUMB_BYTES
        if has_thumb:
            self.thumb_path(page).write_bytes(thumb)
        self.db.execute("INSERT INTO mt_pages(id,name,mime,size,pages,sha256,created_at,has_thumb) VALUES(?,?,?,?,?,?,?,?)",
                        (page["id"], page["name"], page["mime"], page["size"], page["pages"], digest, page["created_at"],
                         int(has_thumb)))
        return {**page, "thumb": has_thumb}

    def rename(self, page_id: str, name: str) -> dict:
        name = clean_name(name)
        if not self.get(page_id):
            raise PageError("That page doesn't exist (any more).", 404)
        self.db.execute("UPDATE mt_pages SET name=? WHERE id=?", (name, page_id))
        return self.get(page_id)  # type: ignore[return-value]

    def delete(self, page_id: str) -> bool:
        page = self.get(page_id)
        if not page:
            return False
        with self.db.tx() as c:
            c.execute("DELETE FROM mt_pages WHERE id=?", (page_id,))
            c.execute("DELETE FROM mt_rotation WHERE page_id=?", (page_id,))
        for path in (self.path(page), self.thumb_path(page)):
            try:
                path.unlink()
            except OSError:
                pass
        return True
