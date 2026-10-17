import pytest

from app.core.db import Database
from app.tools.maintenance import pages, store as store_mod
from app.tools.maintenance.pages import STANDARD, Choice, Library, PageError

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
JPG = b"\xff\xd8\xff\xe0" + b"1" * 20
PDF = b"%PDF-1.4\n1 0 obj<</Type/Page>>endobj\n2 0 obj<</Type/Page>>endobj\n"


def test_files_are_recognised_by_their_bytes_and_postscript_is_refused():
    assert pages.sniff(PNG) == "image/png" and pages.sniff(JPG) == "image/jpeg" and pages.sniff(PDF) == "application/pdf"
    assert pages.sniff(b"%!PS\n/Times findfont") is None  # PostScript can run code: not accepted
    assert pages.sniff(b"MZ\x90\x00 pretending to be a pdf") is None


def test_pdf_page_count():
    assert pages.count_pdf_pages(PDF) == 2
    assert pages.count_pdf_pages(b"%PDF-1.5 /Type /Pages /Count 7 ") == 7  # compressed files: the page tree's total
    assert pages.count_pdf_pages(b"%PDF-1.5 nothing useful") == 1


def test_names_are_cleaned():
    assert pages.clean_name("../../etc/Bunny colouring.PDF") == "Bunny colouring"
    assert pages.clean_name("a\x00b\n c.png") == "ab c"
    assert pages.clean_name("") == "Untitled page"
    assert len(pages.clean_name("x" * 500 + ".png")) == 80


def test_rotation_cycles_skips_removed_pages_and_falls_back():
    on = [STANDARD, "a", "b"]
    assert pages.choose(on, None, 0, 0) == Choice(STANDARD)  # first time: the first enabled one
    assert pages.choose(on, STANDARD, 1, 0).id == "a" and pages.choose(on, "b", 3, 0).id == STANDARD  # wraps round
    assert pages.choose(["a", "b"], "gone", 5, 0).id == "a"  # the last one was deleted or switched off
    assert pages.choose([], "a", 5, 0) == Choice(STANDARD)  # nothing enabled: the plain page


def test_every_nth_print_is_the_plain_page_on_top_of_the_rotation():
    picks = [pages.choose(["a", "b"], None, n, 3) for n in range(6)]
    assert [(c.id, c.plain) for c in picks] == [("a", False), ("a", False), (STANDARD, True),
                                                ("a", False), ("a", False), (STANDARD, True)]
    assert not any(pages.choose(["a"], None, n, 0).plain for n in range(10))  # 0 = never


def test_a_printers_order_and_switches():
    lib = ["p1", "p2", "p3"]
    assert pages.arrange(lib, []) == [(STANDARD, True), ("p1", True), ("p2", True), ("p3", True)]
    saved = [("p3", 0, True), (STANDARD, 1, False), ("p1", 2, True)]
    # saved order wins, the page it has never seen (p2) goes last, and a vanished id is ignored
    assert pages.arrange(lib, saved + [("gone", 9, True)]) == [("p3", True), (STANDARD, False), ("p1", True), ("p2", True)]


@pytest.fixture
def library(tmp_path):
    db = Database(":memory:")
    store_mod.migrate(db)
    return Library(db, tmp_path / "pages", max_file_bytes=1000, max_files=3, max_total_bytes=2000)


def test_library_stores_files_and_refuses_bad_ones(library, tmp_path):
    page = library.add(PNG, "Bunny.png")
    assert page["name"] == "Bunny" and page["mime"] == "image/png" and page["pages"] == 1
    assert (tmp_path / "pages" / f"{page['id']}.png").read_bytes() == PNG
    assert library.add(PDF, "book.pdf")["pages"] == 2
    for data, name, status in ((b"", "x.png", 400), (b"%!PS", "x.ps", 415), (PNG + b"0" * 2000, "big.png", 413)):
        with pytest.raises(PageError) as e:
            library.add(data, name)
        assert e.value.status == status
    with pytest.raises(PageError, match="already in the library"):
        library.add(PNG, "again.png")
    library.add(JPG, "c.jpg")
    with pytest.raises(PageError, match="full"):
        library.add(PNG + b"2", "d.png")


def test_deleting_removes_the_file_and_its_place_in_every_rotation(library, tmp_path):
    page = library.add(PNG, "Bunny.png")
    library.db.execute("INSERT INTO mt_rotation(printer_id,page_id,position,enabled) VALUES('p',?,0,1)", (page["id"],))
    assert library.delete(page["id"]) and not library.delete(page["id"])
    assert not list((tmp_path / "pages").glob("*.png"))
    assert library.db.one("SELECT COUNT(*) AS n FROM mt_rotation")["n"] == 0
    assert library.read(page["id"]) is None


def test_a_thumbnail_is_kept_only_if_it_really_is_a_small_png(library, tmp_path):
    thumb = PNG + b"t" * 50
    page = library.add(PNG, "A.png", thumb)
    assert page["thumb"] is True and library.get(page["id"])["thumb"] is True
    assert library.thumb_path(page).read_bytes() == thumb
    for bad in (b"not a png at all", PNG + b"x" * (pages.MAX_THUMB_BYTES + 1), b""):
        other = library.add(JPG + bad[:5], f"b{len(bad)}.jpg", bad)
        assert other["thumb"] is False and not library.thumb_path(other).exists()  # the page is added; the thumbnail is ignored
        library.delete(other["id"])


def test_deleting_a_page_removes_its_thumbnail_too(library, tmp_path):
    page = library.add(PNG, "A.png", PNG + b"t")
    library.delete(page["id"])
    assert not list((tmp_path / "pages").glob("*"))
