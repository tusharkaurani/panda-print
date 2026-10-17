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


# ------------------------------------------------------------------ per-printer links (placeholders)

from app.core.links import public_cups_url, render_entry  # noqa: E402

PRINTER = {"ip": "192.168.1.18", "name": "Study printer", "id": "study-printer", "queue": "T730",
           "cups_url": "http://192.168.1.10:631"}


def entry(text, tmp_path):
    links, warnings = parse_links(text, tmp_path)
    assert warnings == [], warnings
    return links[0]


def test_placeholders_are_recorded_and_unknown_ones_are_skipped(tmp_path):
    links, warnings = parse_links("""
links:
  - {name: "Panel", url: "http://{ip}/"}
  - {name: "Plain", url: "http://router/"}
  - {name: "CUPS", url: "{cups_url}/printers/{queue}"}
  - {name: "Typo", url: "http://{address}/"}
""", tmp_path)
    assert [(l["name"], l["needs"]) for l in links] == [("Panel", ["ip"]), ("Plain", []), ("CUPS", ["cups_url", "queue"])]
    assert len(warnings) == 1 and "{address}" in warnings[0] and "available" in warnings[0]


def test_printer_links_are_filled_in_for_the_selected_printer(tmp_path):
    panel = entry('links: [{name: "{name} panel", url: "http://{ip}/", description: "for {name}"}]', tmp_path)
    r = render_entry(panel, PRINTER)
    assert (r["name"], r["url"], r["description"]) == ("Study printer panel", "http://192.168.1.18/", "for Study printer")
    assert r["letter"] == "S"
    # a different printer gives a different URL: nothing is hard-coded to one brand or address
    other = render_entry(panel, {**PRINTER, "ip": "10.0.0.7", "name": "Hall"})
    assert other["url"] == "http://10.0.0.7/" and other["name"] == "Hall panel"


def test_global_and_printer_links_with_and_without_a_printer(tmp_path):
    glob = entry('links: [{name: Router, url: "http://192.168.1.1/"}]', tmp_path)
    mine = entry('links: [{name: Panel, url: "http://{ip}/"}]', tmp_path)
    assert render_entry(glob, None)["url"] == "http://192.168.1.1/" and render_entry(glob, PRINTER)
    assert render_entry(mine, None) is None  # nothing to fill in without a printer


def test_link_is_hidden_when_the_printer_lacks_a_value(tmp_path):
    cups = entry('links: [{name: CUPS, url: "{cups_url}/printers/{queue}"}]', tmp_path)
    assert render_entry(cups, PRINTER)["url"] == "http://192.168.1.10:631/printers/T730"
    assert render_entry(cups, {**PRINTER, "queue": ""}) is None


def test_values_are_escaped_in_urls_but_not_in_text(tmp_path):
    e = entry('links: [{name: "{name}", url: "http://x/?q={name}"}]', tmp_path)
    r = render_entry(e, {**PRINTER, "name": "A&B printer"})
    assert r["name"] == "A&B printer" and r["url"] == "http://x/?q=A%26B%20printer"


def test_ipv6_address_is_bracketed(tmp_path):
    from app.core.links import printer_vars
    from app.core.printers import Printer
    v = printer_vars(Printer(id="p", name="P", ip="2001:db8::5"), "http://c:631")
    assert render_entry(entry('links: [{name: Panel, url: "http://{ip}/"}]', tmp_path), v)["url"] == "http://[2001:db8::5]/"


def test_printers_filter_matches_name_or_id_case_insensitively(tmp_path):
    e = entry('links: [{name: Inks, url: "http://shop/", printers: [STUDY PRINTER, hall]}]', tmp_path)
    assert render_entry(e, PRINTER) and render_entry(e, {**PRINTER, "name": "Other", "id": "hall"})
    assert render_entry(e, {**PRINTER, "name": "Other", "id": "other"}) is None
    assert render_entry(e, None) is None


def test_loopback_cups_is_shown_as_the_host_the_viewer_used():
    assert public_cups_url("http://127.0.0.1:631", "pi.local") == "http://pi.local:631"
    assert public_cups_url("http://localhost:631/", "192.168.1.10") == "http://192.168.1.10:631"
    assert public_cups_url("https://127.0.0.1", "pi.local") == "https://pi.local"
    assert public_cups_url("http://192.168.1.10:631", "laptop.local") == "http://192.168.1.10:631"  # already reachable
