import pytest

from app.core.links import LinksConfig, LinksError, parse_links

GOOD = """
links:
  - name: CUPS Dashboard
    url: http://192.168.1.10:631/
    description: Queues
    logo: builtin:server
  - name: Router
    url: https://router.lan/
    new_tab: false
"""


def test_parses_entries_and_defaults(tmp_path):
    links, warnings = parse_links(GOOD, tmp_path)
    assert warnings == []
    assert [l["name"] for l in links] == ["CUPS Dashboard", "Router"]
    assert links[0]["logo"] == "/static/icons/server.svg" and links[0]["new_tab"] is True
    assert links[1]["new_tab"] is False and links[1]["logo"] is None and links[1]["letter"] == "R"


def test_bad_entries_are_skipped_with_warnings(tmp_path):
    text = """
links:
  - name: No url
  - url: http://x/
  - name: Script
    url: "javascript:alert(1)"
  - just a string
  - name: Fine
    url: http://ok/
    colour: red
"""
    links, warnings = parse_links(text, tmp_path)
    assert [l["name"] for l in links] == ["Fine"]
    assert len(warnings) == 5
    assert any("unknown key" in w for w in warnings)


def test_yaml_syntax_error_mentions_a_line(tmp_path):
    with pytest.raises(LinksError, match=r"near line \d+"):
        parse_links("links:\n  - name: A\n    url: [oops\n", tmp_path)


def test_wrong_shape_is_a_friendly_error(tmp_path):
    with pytest.raises(LinksError, match="list"):
        parse_links("links: nope", tmp_path)


def test_empty_file_is_fine(tmp_path):
    assert parse_links("", tmp_path) == ([], [])
    assert parse_links("links:\n", tmp_path) == ([], [])


def test_logo_resolution(tmp_path):
    (tmp_path / "cups.svg").write_text("<svg/>")
    text = """
links:
  - {name: A, url: "http://a/", logo: cups.svg}
  - {name: B, url: "http://b/", logo: missing.png}
  - {name: C, url: "http://c/", logo: "../secret.png"}
  - {name: D, url: "http://d/", logo: "https://cdn.example/d.png"}
  - {name: E, url: "http://e/", logo: "builtin:nope"}
  - {name: F, url: "http://f/", logo: "notes.txt"}
"""
    links, warnings = parse_links(text, tmp_path)
    logos = {l["name"]: l["logo"] for l in links}
    assert logos["A"].startswith("/logos/cups.svg?v=")
    assert logos["D"] == "https://cdn.example/d.png"
    assert logos["B"] is logos["C"] is logos["E"] is logos["F"] is None  # letter tiles
    assert len(warnings) == 4


def test_reload_keeps_last_good_config_when_file_breaks(tmp_path):
    f = tmp_path / "links.yml"
    f.write_text(GOOD)
    cfg = LinksConfig(f, tmp_path / "logos")
    assert len(cfg.get()["links"]) == 2 and cfg.get()["error"] is None

    f.write_text("links:\n  - name: A\n    url: [broken\n")
    broken = cfg.get()
    assert broken["error"] and "last working" in broken["error"]
    assert len(broken["links"]) == 2  # still serving the previous tiles

    f.write_text("links:\n  - {name: Only, url: 'http://only/'}\n")
    fixed = cfg.get()
    assert fixed["error"] is None and [l["name"] for l in fixed["links"]] == ["Only"]


def test_missing_file_is_reported_not_raised(tmp_path):
    result = LinksConfig(tmp_path / "nope.yml", tmp_path / "logos").get()
    assert result["links"] == [] and "No links.yml" in result["error"]


def test_falls_back_to_bundled_default(tmp_path):
    default = tmp_path / "default.yml"
    default.write_text("links:\n  - {name: Bundled, url: 'http://b/'}\n")
    result = LinksConfig(tmp_path / "nope.yml", tmp_path / "logos", default).get()
    assert result["links"][0]["name"] == "Bundled" and result["error"] is None
