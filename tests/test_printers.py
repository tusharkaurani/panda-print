import pytest

from app.core.db import Database
from app.core.printers import PrinterRegistry, ValidationError, clean_host, validate
from app.factory import create_app, seed_printers
from app.printer import discovery
from app.settings import Settings
from conftest import PID, csrf_headers


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("raw,expected", [
    ("192.168.1.18", "192.168.1.18"),
    ("  192.168.1.18 ", "192.168.1.18"),
    ("http://192.168.1.18/", "192.168.1.18"),          # pasted from the browser bar
    ("ipp://printer.lan:631/ipp/print", "printer.lan"),
    ("printer.local", "printer.local"),
    ("[fe80::1]", ""),                                  # link-local is refused
    ("169.254.169.254", ""),                            # cloud metadata address
    ("0.0.0.0", ""), ("224.0.0.251", ""),
    ("", ""), ("bad host!", ""), ("-bad.example", ""),
])
def test_clean_host(raw, expected):
    assert clean_host(raw) == expected


def test_validate_reports_each_bad_field():
    clean, errors = validate({"name": "", "ip": "nope!", "ipp_url": "ftp://x/", "snmp_port": "99999",
                              "counter_oid": "1.2", "cups_url": "javascript:1", "cups_queue": "bad queue",
                              "host_state_file": "relative/path"})
    assert set(errors) == {"name", "ip", "ipp_url", "snmp_port", "counter_oid", "cups_url", "cups_queue",
                           "host_state_file"}


def test_validate_fills_defaults_and_cleans():
    clean, errors = validate({"name": " Study ", "ip": "http://192.168.1.18/", "snmp_port": "161"})
    assert errors == {}
    assert clean["name"] == "Study" and clean["ip"] == "192.168.1.18" and clean["snmp_community"] == "public"
    assert clean["cups_url"] == "http://127.0.0.1:631" and clean["cups_queue"] == "" and clean["ipp_url"] == ""


def test_duplicate_names_are_refused_case_insensitively():
    _, errors = validate({"name": "STUDY", "ip": "192.168.1.5"}, taken_names=["Study"])
    assert "name" in errors


# ------------------------------------------------------------------ registry

@pytest.fixture
def registry():
    return PrinterRegistry(Database(":memory:"), clock=lambda: 100.0)


def test_add_get_list_and_unique_ids(registry):
    a = registry.add({"name": "Study Printer", "ip": "192.168.1.18", "cups_queue": "T730"})
    b = registry.add({"name": "Study printer!", "ip": "192.168.1.19"})  # same slug, different name
    assert (a.id, b.id) == ("study-printer", "study-printer-2")
    assert [p.id for p in registry.list()] == ["study-printer", "study-printer-2"]
    assert registry.get(a.id).cups_queue == "T730" and len(registry) == 2
    assert registry.get("nope") is None
    assert a.ipp_endpoint == "ipp://192.168.1.18/ipp/print"


def test_add_rejects_invalid_and_stores_nothing(registry):
    with pytest.raises(ValidationError) as e:
        registry.add({"name": "x", "ip": ""})
    assert "ip" in e.value.errors and len(registry) == 0


def test_update_keeps_id_and_validates(registry):
    p = registry.add({"name": "Study", "ip": "192.168.1.18"})
    registry.add({"name": "Hall", "ip": "192.168.1.19"})
    updated = registry.update(p.id, {"ip": "192.168.1.30", "cups_queue": "Q1"})
    assert updated.id == p.id and registry.get(p.id).ip == "192.168.1.30" and registry.get(p.id).name == "Study"
    with pytest.raises(ValidationError):
        registry.update(p.id, {"name": "hall"})
    with pytest.raises(KeyError):
        registry.update("ghost", {})


def test_delete_runs_hooks(registry):
    seen = []
    registry.on_delete(seen.append)
    p = registry.add({"name": "Study", "ip": "192.168.1.18"})
    assert registry.delete(p.id) is True and seen == [p.id] and len(registry) == 0
    assert registry.delete(p.id) is False


# ------------------------------------------------------------------ start-up seeding

def test_env_seed_adds_a_printer_once(tmp_path):
    s = Settings(printer_ip="192.168.1.18", cups_queue="T730", data_dir=tmp_path)
    r = PrinterRegistry(Database(":memory:"))
    seed_printers(s, r)
    seed_printers(s, r)
    assert [(p.ip, p.cups_queue) for p in r.list()] == [("192.168.1.18", "T730")]


def test_no_env_means_no_printer_and_bad_env_does_not_crash(tmp_path):
    r = PrinterRegistry(Database(":memory:"))
    seed_printers(Settings(data_dir=tmp_path), r)
    seed_printers(Settings(printer_ip="not valid!", data_dir=tmp_path), r)
    assert len(r) == 0


def test_starts_with_no_printers_until_one_is_added(make_app):
    app, _ = make_app(printer=False)
    assert app.test_client().get("/api/printers").get_json()["data"]["printers"] == []


# ------------------------------------------------------------------ API

def test_list_includes_form_defaults(client):
    data = client.get("/api/printers").get_json()["data"]
    assert [p["ip"] for p in data["printers"]] == ["192.168.1.18"]
    assert data["defaults"]["cups_url"] == "http://127.0.0.1:631" and data["defaults"]["snmp_port"] == 161


def test_changes_need_csrf(client):
    assert client.post("/api/printers", json={"name": "x", "ip": "192.168.1.5"}).status_code == 403
    assert client.put(f"/api/printers/{PID}", json={"name": "y"}).status_code == 403
    assert client.delete(f"/api/printers/{PID}").status_code == 403
    assert client.post("/api/printers/probe", json={"ip": "192.168.1.5"}).status_code == 403


def test_add_edit_delete_roundtrip(client):
    h = csrf_headers(client)
    bad = client.post("/api/printers", json={"name": "Hall", "ip": ""}, headers=h)
    assert bad.status_code == 400 and "ip" in bad.get_json()["error"]["fields"]

    ok = client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h)
    assert ok.status_code == 201 and ok.get_json()["data"]["id"] == "hall"
    dup = client.post("/api/printers", json={"name": "hall", "ip": "192.168.1.24"}, headers=h)
    assert dup.status_code == 400 and "name" in dup.get_json()["error"]["fields"]

    upd = client.put("/api/printers/hall", json={"name": "Hallway"}, headers=h)
    assert upd.get_json()["data"]["name"] == "Hallway" and upd.get_json()["data"]["ip"] == "192.168.1.23"
    assert client.put("/api/printers/ghost", json={"name": "x"}, headers=h).status_code == 404

    assert client.delete("/api/printers/hall", headers=h).status_code == 200
    assert client.delete("/api/printers/hall", headers=h).status_code == 404
    assert len(client.get("/api/printers").get_json()["data"]["printers"]) == 1


def test_discover_marks_printers_already_added(client):
    h = csrf_headers(client)
    data = client.post("/api/printers/discover", json={}, headers=h).get_json()["data"]
    by_ip = {c["ip"]: c for c in data["candidates"]}
    assert by_ip["192.168.1.18"]["already_added"] == PID and by_ip["192.168.1.18"]["queue"] == "T730"
    assert by_ip["192.168.1.23"]["already_added"] is None


def test_probe_good_and_unreachable_addresses(client):
    h = csrf_headers(client)
    good = client.post("/api/printers/probe", json={"ip": "192.168.1.18"}, headers=h).get_json()["data"]
    assert good["ipp"]["ok"] and good["snmp"]["value"] == 174 and good["cups"]["suggested"] == "T730"
    assert good["suggested_name"] == "Brother DCP-T730DW"
    bad = client.post("/api/printers/probe", json={"ip": "10.9.9.9"}, headers=h).get_json()["data"]
    assert not bad["ipp"]["ok"] and "did not answer" in bad["ipp"]["error"]["message"]
    assert client.post("/api/printers/probe", json={"ip": "???"}, headers=h).get_json()["data"]["errors"]["ip"]
    assert client.post(f"/api/printers/{PID}/test", json={}, headers=h).get_json()["data"]["ipp"]["ok"]


# ------------------------------------------------------------------ per-printer maintenance

def test_unknown_printer_is_a_404_for_every_card(client):
    for card in ("printer", "queue", "nozzle"):
        r = client.get(f"/api/maintenance/ghost/{card}")
        assert r.status_code == 404 and "isn't set up" in r.get_json()["error"]["message"]
    assert client.post("/api/maintenance/ghost/nozzle/print", headers=csrf_headers(client)).status_code == 404


def test_printers_keep_separate_histories_and_limits(client):
    h = csrf_headers(client)
    client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23", "cups_queue": "HP_ENVY"}, headers=h)
    assert client.post(f"/api/maintenance/{PID}/nozzle/print", headers=h).status_code == 202
    # the other printer was not rate-limited by it, and its history is its own
    hall = client.get("/api/maintenance/hall/nozzle").get_json()["data"]
    assert hall["print"]["allowed"] is True
    assert client.post("/api/maintenance/hall/nozzle/print", headers=h).status_code == 202
    assert client.get("/api/maintenance/hall/printer").get_json()["data"]["name"] == "HP ENVY 6000 series"


def test_removing_a_printer_deletes_its_history(client):
    h = csrf_headers(client)
    ctx = client.application.extensions["panda"]["ctx"]
    client.get(f"/api/maintenance/{PID}/nozzle")  # builds history
    assert ctx.db.one("SELECT COUNT(*) AS n FROM mt_activity")["n"] > 0
    client.post("/api/printers", json={"name": "Hall", "ip": "192.168.1.23"}, headers=h)
    client.delete(f"/api/printers/{PID}", headers=h)
    assert ctx.db.one("SELECT COUNT(*) AS n FROM mt_activity WHERE printer_id=?", (PID,))["n"] == 0
    assert ctx.db.one("SELECT COUNT(*) AS n FROM mt_checks WHERE printer_id=?", (PID,))["n"] == 0
    assert ctx.db.one("SELECT COUNT(*) AS n FROM kv WHERE key LIKE ?", (f"maintenance.{PID}.%",))["n"] == 0
    assert client.get(f"/api/maintenance/{PID}/nozzle").status_code == 404


def test_editing_a_printer_rebuilds_its_runtime(client):
    h = csrf_headers(client)
    client.put(f"/api/printers/{PID}", json={"cups_queue": ""}, headers=h)
    q = client.get(f"/api/maintenance/{PID}/queue").get_json()
    assert q["ok"] is False and "No CUPS queue" in q["error"]["message"]
    client.put(f"/api/printers/{PID}", json={"cups_queue": "T730"}, headers=h)
    assert client.get(f"/api/maintenance/{PID}/queue").get_json()["ok"] is True


# ------------------------------------------------------------------ live discovery (against the fake CUPS)

def test_discovery_reads_queues_and_skips_usb(cups):
    s = Settings(net_timeout=2)
    found, note = discovery.cups_candidates(s, f"http://127.0.0.1:{cups}")
    assert note is None
    assert [(c["queue"], c["ip"]) for c in found] == [("T730", "127.0.0.1")]  # the usb:// queue has no address


def test_discovery_reports_unreachable_cups_as_a_note():
    found, note = discovery.cups_candidates(Settings(net_timeout=1), "http://127.0.0.1:1")
    assert found == [] and "CUPS" in note


def test_probe_against_a_fake_printer_and_cups(cups):
    s = Settings(net_timeout=2)
    out = discovery.probe(s, {"ip": "127.0.0.1", "ipp_url": f"ipp://127.0.0.1:{cups}/ipp/print",
                              "cups_url": f"http://127.0.0.1:{cups}", "snmp_port": 1})
    assert out["ipp"]["ok"] and out["ipp"]["model"] == "Brother DCP-T730DW" and out["ipp"]["inks"] == 4
    assert out["suggested_name"] == "Brother DCP-T730DW"
    assert not out["snmp"]["ok"]  # nothing answers SNMP here, and that doesn't spoil the rest
    assert out["cups"]["ok"] and out["cups"]["suggested"] == "T730"
    named = discovery.probe(s, {"ip": "127.0.0.1", "ipp_url": f"ipp://127.0.0.1:{cups}/ipp/print",
                                "cups_url": f"http://127.0.0.1:{cups}", "cups_queue": "Nope", "snmp_port": 1})
    assert not named["cups"]["ok"] and "Nope" in named["cups"]["error"]["message"]


def test_probe_of_a_dead_address_fails_fast_and_cleanly():
    out = discovery.probe(Settings(net_timeout=1), {"ip": "127.0.0.1", "ipp_url": "ipp://127.0.0.1:1/ipp/print",
                                                     "cups_url": "http://127.0.0.1:1", "snmp_port": 1})
    assert not out["ipp"]["ok"] and not out["cups"]["ok"] and out["ipp"]["error"]["message"]


def test_generic_cups_driver_text_is_not_used_as_a_model():
    assert discovery._generic("Printer - IPP Everywhere") and discovery._generic("Generic PostScript Printer")
    assert discovery._generic("") and not discovery._generic("Brother DCP-T730DW")
