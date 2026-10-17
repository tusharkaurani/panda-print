"""The Print Utilities tool: its catalog file, the override file and the API."""
import re
import time
from pathlib import Path

import pytest

from app.tools.utilities import catalog as cat
from app.tools.utilities.catalog import Catalog, CatalogError

BUILTIN = Path(cat.__file__).with_name("catalog.yml")
UI = Path(cat.__file__).parent / "ui"


def test_the_built_in_list_is_clean_and_grouped():
    categories, warnings = cat.parse(BUILTIN.read_text())
    assert warnings == []
    assert len(categories) >= 5
    names = [c["name"] for c in categories]
    assert "Photos & IDs" in names and "Student worksheets" in names
    for c in categories:
        for t in c["tools"]:
            assert t["url"].startswith("https://") and t["name"] and t["host"] and not t["host"].startswith("www.")


def test_every_art_the_catalog_uses_is_drawn_by_the_ui():
    drawn = set(re.findall(r"^  (\w+): \(s", (UI / "art.js").read_text(), re.M))
    assert drawn == cat.ARTS  # the validator and the drawings can't drift apart
    used = {t["art"] for c in cat.parse(BUILTIN.read_text())[0] for t in c["tools"]}
    assert used <= drawn


def test_bad_entries_are_skipped_with_a_note_and_the_rest_load():
    text = """
categories:
  - name: Good
    tools:
      - {name: A, url: "https://a.example/x", art: math}
      - {name: B, url: "javascript:alert(1)"}
      - {name: C, url: "https://c.example", art: nonsense, image: "ftp://x"}
      - {url: "https://d.example"}
  - name: Empty
    tools: []
  - {tools: []}
"""
    categories, warnings = cat.parse(text)
    assert [c["name"] for c in categories] == ["Good"]
    assert [t["name"] for t in categories[0]["tools"]] == ["A", "C"]
    assert categories[0]["tools"][1]["art"] == "document" and categories[0]["tools"][1]["image"] is None
    joined = " ".join(warnings)
    assert "'B' skipped" in joined and "unknown art" in joined and "'image'" in joined and "Empty" in joined


def test_a_file_that_is_not_yaml_is_an_error_not_a_crash():
    with pytest.raises(CatalogError):
        cat.parse("categories: [unclosed")
    with pytest.raises(CatalogError):
        cat.parse("categories: nope")


def test_your_own_file_replaces_the_list_and_a_broken_one_falls_back(tmp_path):
    mine = tmp_path / "print-utilities.yml"
    c = Catalog(BUILTIN, mine)
    assert c.get()["source"] == "built-in"
    mine.write_text("categories:\n  - name: Mine\n    tools:\n      - {name: X, url: 'https://x.example'}\n")
    got = c.get()
    assert got["source"] == "custom" and [x["name"] for x in got["categories"]] == ["Mine"]
    assert c.get() is got  # unchanged file: not parsed again
    time.sleep(0.01)
    mine.write_text("categories: [broken")
    got = c.get()
    assert got["source"] == "built-in" and "syntax error" in got["error"] and len(got["categories"]) >= 5
    mine.unlink()
    assert c.get()["error"] is None


def test_the_api_serves_the_catalog_and_the_tool_has_its_own_tab(make_app):
    app, _ = make_app()
    client = app.test_client()
    d = client.get("/api/utilities/catalog").get_json()["data"]
    assert d["source"] == "built-in" and d["categories"][0]["tools"][0]["url"].startswith("https://")
    tools = client.get("/api/tools").get_json()
    ids = [t["id"] for t in (tools.get("data", tools).get("tools") if isinstance(tools.get("data", tools), dict) else tools)]
    assert "utilities" in ids


def test_cards_are_laid_out_four_to_a_row_at_most():
    css = (UI / "ui.css").read_text()
    assert "calc((100% - 3 * var(--gap)) / 4)" in css and "auto-fill" in css
