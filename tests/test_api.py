import dataclasses

import pytest

from conftest import PID, csrf_headers

DAY = 86400


def test_health(client):
    assert client.get("/api/health").get_json()["ok"] is True


def test_index_and_tool_registry(client):
    assert b"panda-print" in client.get("/").data
    tools = client.get("/api/tools").get_json()["tools"]
    assert [t["id"] for t in tools] == ["maintenance"]
    assert client.get(tools[0]["entry"]).status_code == 200
    assert client.get("/tools/maintenance/../../settings.py").status_code == 404
    assert client.get("/tools/nope/ui.js").status_code == 404


def test_printer_status_orders_inks_and_reports_counter(client):
    body = client.get(f"/api/maintenance/{PID}/printer").get_json()
    assert body["ok"] is True
    d = body["data"]
    # the printer reports M,C,Y,BK; the UI wants C,M,Y,K
    assert [(i["key"], i["percent"]) for i in d["inks"]] == [("C", 95), ("M", 94), ("Y", 94), ("K", 95)]
    assert d["state"]["label"] == "Idle" and d["state"]["health"] == "ok"
    assert d["counter"]["value"] == 174 and d["counter"]["error"] is None


def test_queue_status_lists_recent_jobs_newest_first(client):
    d = client.get(f"/api/maintenance/{PID}/queue").get_json()["data"]
    ids = [j["id"] for j in d["jobs"]]
    assert ids == sorted(ids, reverse=True) and len(ids) == 8  # all of the mock's jobs, under the limit of 10
    assert d["name"] == "T730" and d["state"]["accepting"] is True
    assert any(j["nozzle_check"] for j in d["jobs"]) and not all(j["nozzle_check"] for j in d["jobs"])


def test_nozzle_status_from_seeded_history(client):
    d = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["last_print"]["kind"] == "external"
    assert d["days_since_last_print"] == pytest.approx(2.6, abs=0.05)
    assert d["next_check"]["days_until"] == pytest.approx(4.4, abs=0.05) and not d["next_check"]["overdue"]
    assert d["last_auto"]["source"] == "host"  # imported from the CUPS job list
    assert d["last_manual"]["source"] == "manual"
    assert d["last_external_print"] == d["last_print"]["ts"]
    assert d["print"]["allowed"] is True
    assert len(d["history"]) == 3


def test_overdue_when_idle_longer_than_threshold(client, clock):
    client.get(f"/api/maintenance/{PID}/nozzle")
    clock.advance(5 * DAY)  # 2.6 + 5 days idle > 7
    d = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["next_check"]["overdue"] is True and d["next_check"]["days_until"] < 0


def test_one_unreachable_source_does_not_break_the_other_cards(make_app):
    app, _ = make_app(mock_fail=("printer",))
    c = app.test_client()
    printer = c.get(f"/api/maintenance/{PID}/printer")
    assert printer.status_code == 200
    body = printer.get_json()
    assert body["ok"] is False and "did not answer" in body["error"]["message"]
    assert c.get(f"/api/maintenance/{PID}/queue").get_json()["ok"] is True
    assert c.get(f"/api/maintenance/{PID}/nozzle").get_json()["ok"] is True


def test_counter_failure_keeps_last_known_value_and_reports_it(make_app, clock):
    app, printer = make_app()
    c = app.test_client()
    assert c.get(f"/api/maintenance/{PID}/printer").get_json()["data"]["counter"]["value"] == 174
    printer.s = dataclasses.replace(printer.s, mock_fail=("snmp",))  # the printer's SNMP goes away
    clock.advance(120)
    d = c.get(f"/api/maintenance/{PID}/printer").get_json()["data"]
    assert d["counter"]["value"] == 174 and "SNMP" in d["counter"]["error"]
    nz = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert nz["tracking"]["counter_error"]


def test_cups_failure_shows_in_queue_card_only(make_app):
    app, _ = make_app(mock_fail=("cups",))
    c = app.test_client()
    assert c.get(f"/api/maintenance/{PID}/queue").get_json()["ok"] is False
    assert c.get(f"/api/maintenance/{PID}/printer").get_json()["ok"] is True


# ------------------------------------------------------------------ CSRF + printing

def test_post_without_csrf_token_is_rejected(client):
    r = client.post(f"/api/maintenance/{PID}/nozzle/print")
    assert r.status_code == 403 and "CSRF" in r.get_json()["error"]["message"]


def test_post_with_forged_token_is_rejected(client):
    client.get("/api/csrf")  # sets the cookie
    r = client.post(f"/api/maintenance/{PID}/nozzle/print", headers={"X-CSRF-Token": "abc.def"})
    assert r.status_code == 403
    # a matching cookie/header pair that the server never signed must fail too
    client.set_cookie("panda_csrf", "evil.token")
    assert client.post(f"/api/maintenance/{PID}/nozzle/print", headers={"X-CSRF-Token": "evil.token"}).status_code == 403


def test_cross_origin_post_is_rejected_even_with_a_token(client):
    h = csrf_headers(client)
    r = client.post(f"/api/maintenance/{PID}/nozzle/print", headers={**h, "Origin": "https://evil.example"})
    assert r.status_code == 403 and "Cross-origin" in r.get_json()["error"]["message"]


def test_same_origin_and_proxied_origin_are_accepted(make_app, clock):
    app, _ = make_app(print_cooldown=0)
    c = app.test_client()
    h = csrf_headers(c)
    assert c.post(f"/api/maintenance/{PID}/nozzle/print", headers={**h, "Origin": "http://localhost"}).status_code == 202
    proxied = {**h, "Origin": "https://print.home.lan", "X-Forwarded-Host": "print.home.lan"}
    assert c.post(f"/api/maintenance/{PID}/nozzle/print", headers=proxied).status_code == 202


def test_csrf_cookie_is_httponly_and_samesite_strict(client):
    cookie = client.get("/api/csrf").headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_print_records_a_manual_check_and_is_rate_limited(client, clock):
    h = csrf_headers(client)
    r = client.post(f"/api/maintenance/{PID}/nozzle/print", headers=h)
    assert r.status_code == 202 and r.get_json()["data"]["job_id"] == 39

    again = client.post(f"/api/maintenance/{PID}/nozzle/print", headers=h)
    assert again.status_code == 429 and again.headers["Retry-After"] == "121"
    assert again.get_json()["error"]["retry_after"] == 121

    nz = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert nz["print"]["allowed"] is False and nz["last_manual"]["job_id"] == 39

    clock.advance(121)
    assert client.post(f"/api/maintenance/{PID}/nozzle/print", headers=h).status_code == 202


def test_hourly_cap(make_app, clock):
    app, _ = make_app(print_cooldown=0, print_max_per_hour=2)
    c = app.test_client()
    h = csrf_headers(c)
    assert [c.post(f"/api/maintenance/{PID}/nozzle/print", headers=h).status_code for _ in range(3)] == [202, 202, 429]
    clock.advance(3601)
    assert c.post(f"/api/maintenance/{PID}/nozzle/print", headers=h).status_code == 202


def test_failed_print_is_reported_and_recorded(make_app):
    app, _ = make_app(mock_fail=("cups",))
    c = app.test_client()
    r = c.post(f"/api/maintenance/{PID}/nozzle/print", headers=csrf_headers(c))
    assert r.status_code == 502 and "CUPS" in r.get_json()["error"]["message"]
    assert c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_manual"]["state"] == "failed"


def test_own_print_is_attributed_to_the_nozzle_check_not_to_a_person(client, clock):
    before = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    client.post(f"/api/maintenance/{PID}/nozzle/print", headers=csrf_headers(client))
    clock.advance(60)  # the mock printer finishes the page after 8s and the counter moves
    after = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert client.get(f"/api/maintenance/{PID}/printer").get_json()["data"]["counter"]["value"] == 175
    assert after["last_print"]["kind"] == "nozzle"
    assert after["last_external_print"] == before["last_external_print"]  # nobody else printed
    assert after["days_since_last_print"] < 0.01  # a nozzle check restarts the idle clock


def test_someone_elses_print_moves_last_external(make_app, clock):
    app, printer = make_app()
    c = app.test_client()
    first = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_external_print"]
    printer.counter += 3  # a phone prints three pages
    clock.advance(120)
    d = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["last_external_print"] > first and d["last_print"]["kind"] == "external"
    assert d["days_since_last_print"] < 0.01


# ------------------------------------------------------------------ launcher links

def test_links_endpoint_and_error_handling(make_app, tmp_path):
    app, _ = make_app()
    c = app.test_client()
    cfg = tmp_path / "config"
    (cfg / "links.yml").write_text("links:\n  - {name: CUPS, url: 'http://c/', logo: builtin:server}\n")
    assert [l["name"] for l in c.get("/api/links").get_json()["links"]] == ["CUPS"]

    (cfg / "links.yml").write_text("links: [unclosed\n")
    broken = c.get("/api/links")
    assert broken.status_code == 200  # a bad file never turns into a server error
    body = broken.get_json()
    assert body["error"] and [l["name"] for l in body["links"]] == ["CUPS"]


def test_logo_files_are_served_safely(make_app, tmp_path):
    app, _ = make_app()
    c = app.test_client()
    logos = tmp_path / "config" / "logos"
    logos.mkdir()
    (logos / "a.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (tmp_path / "config" / "links.yml").write_text("links: []")
    r = c.get("/logos/a.svg")
    assert r.status_code == 200 and "sandbox" in r.headers["Content-Security-Policy"]
    assert c.get("/logos/../links.yml").status_code == 404
    assert c.get("/logos/missing.png").status_code == 404


def test_security_headers(client):
    r = client.get("/")
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
