import pytest

from conftest import PID, csrf_headers

DAY = 86400


def test_health(client):
    assert client.get("/api/health").get_json()["ok"] is True


def test_index_and_tool_registry(client):
    assert b"panda-print" in client.get("/").data
    tools = client.get("/api/tools").get_json()["tools"]
    assert [t["id"] for t in tools] == ["maintenance", "photosheet", "utilities"]
    assert all(tab["description"] for tab in tools[0]["tabs"])  # each tab says what it holds on hover
    by_id = {t["id"]: t for t in tools}
    assert [t["id"] for t in tools if not t["parent"]] == ["maintenance", "utilities"]  # three tabs at the top: Overview, Nozzle Care, Print Utilities
    assert by_id["photosheet"]["parent"] == "utilities"  # the photo tool lives inside the Print Utilities page
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
    assert ids == sorted(ids, reverse=True) and len(ids) == 8  # all of the fake's jobs, under the limit of 10
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
    app, _ = make_app(fail=("printer",))
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
    printer.fail = ("snmp",)  # the printer's SNMP goes away
    clock.advance(120)
    d = c.get(f"/api/maintenance/{PID}/printer").get_json()["data"]
    assert d["counter"]["value"] == 174 and "SNMP" in d["counter"]["error"]
    nz = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert nz["tracking"]["counter_error"]


def test_cups_failure_shows_in_queue_card_only(make_app):
    app, _ = make_app(fail=("cups",))
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
    app, _ = make_app(fail=("cups",))
    c = app.test_client()
    r = c.post(f"/api/maintenance/{PID}/nozzle/print", headers=csrf_headers(c))
    assert r.status_code == 502 and "CUPS" in r.get_json()["error"]["message"]
    assert c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_manual"]["state"] == "failed"


def test_own_print_is_attributed_to_the_nozzle_check_not_to_a_person(client, clock):
    before = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    client.post(f"/api/maintenance/{PID}/nozzle/print", headers=csrf_headers(client))
    clock.advance(60)  # the fake printer finishes the page after 8s and the counter moves
    after = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert client.get(f"/api/maintenance/{PID}/printer").get_json()["data"]["counter"]["value"] == 175
    assert after["last_print"]["kind"] == "nozzle"
    assert after["last_external_print"] == before["last_external_print"]  # nobody else printed
    assert after["days_since_last_print"] < 0.01  # a nozzle check restarts the idle clock


def test_print_straight_to_the_printer_is_shown_as_direct(make_app, clock):
    app, printer = make_app()
    c = app.test_client()
    first = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_external_print"]
    printer.counter += 3  # a phone prints three pages directly: CUPS never hears about it
    clock.advance(120)
    d = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["last_external_print"] > first and d["last_print"]["kind"] == "direct"
    assert d["days_since_last_print"] < 0.01
    q = c.get(f"/api/maintenance/{PID}/queue").get_json()["data"]
    assert [x["pages"] for x in q["direct"]] == [3]
    assert q["jobs"][0]["id"] == 38  # the CUPS list itself is unchanged


def test_print_that_went_through_cups_is_not_called_direct(make_app, clock):
    app, printer = make_app()
    c = app.test_client()
    c.get(f"/api/maintenance/{PID}/nozzle")
    now = clock()
    printer.jobs.append(printer._job(60, "Invoice.pdf", "alex", now + 5, now + 30, 2))
    printer.counter += 2
    clock.advance(120)
    d = c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]
    assert d["last_print"]["kind"] == "external"
    assert c.get(f"/api/maintenance/{PID}/queue").get_json()["data"]["direct"] == []


def test_when_cups_cannot_be_asked_pages_are_not_called_direct(make_app, clock):
    app, printer = make_app()
    c = app.test_client()
    c.get(f"/api/maintenance/{PID}/nozzle")
    printer.fail = ("cups",)  # CUPS is down, so we can't tell where the pages came from
    printer.counter += 2
    clock.advance(120)
    assert c.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_print"]["kind"] == "external"


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


def test_build_id_is_exposed_so_open_pages_can_spot_a_restart(make_app):
    app, _ = make_app()
    c = app.test_client()
    build = c.get("/api/health").get_json()["build"]
    assert build and c.get("/api/tools").get_json()["build"] == build  # the same id everywhere within one run


def test_css_never_draws_a_closed_dialog(client):
    """A `display:` on a bare `dialog` selector overrides the browser's rule that hides closed dialogs (this
    once left the Manage pages dialog visible at the bottom of the page)."""
    import re
    css = client.get("/static/app.css").get_data(as_text=True)
    for selectors, body in re.findall(r"([^{}]*)\{([^}]*)\}", css):
        if not re.search(r"(^|;)\s*display\s*:", body):
            continue
        for selector in selectors.split(","):
            target = selector.strip().split()[-1] if selector.strip() else ""  # what the rule styles: the last part
            if target.startswith("dialog") and "[open]" not in target and ":not(" not in target:
                assert False, f"{selector.strip()!r} sets display without [open]"


def test_every_button_in_the_ui_says_what_it_does_on_hover():
    """Each button is built on one line; it must carry a title (its hover text). Add one when you add a button."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    missing = []
    for path in [*root.glob("static/**/*.js"), *root.glob("tools/*/ui/*.js"), root / "static" / "index.html"]:
        if "vendor" in path.parts:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for m in re.finditer(r"h\('button'|<button\b|h\('a', \{ class: '(?:btn|icon-mini)", line):
                if "title" not in line[m.start():] and "title" not in line[:m.start()].split("h('")[-1]:
                    missing.append(f"{path.relative_to(root)}:{n}")
    assert not missing, f"buttons without a title: {missing}"


def test_the_repository_has_what_an_open_source_project_needs():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("LICENSE", "THIRD-PARTY.md", "SECURITY.md", "CONTRIBUTING.md", "README.md", "app/static/vendor/pdfjs/LICENSE"):
        assert (root / name).is_file() and (root / name).stat().st_size > 200, name
    assert "MIT License" in (root / "LICENSE").read_text()
    assert "pdf.js" in (root / "THIRD-PARTY.md").read_text()
    for secret in (".env", "secret.key"):
        assert secret in (root / ".gitignore").read_text()


def test_the_readme_is_a_short_landing_page_whose_links_and_screenshots_exist():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text()
    assert len(readme.splitlines()) <= 120, "keep the README a landing page; put detail in docs/"
    shots = re.findall(r"!\[[^\]]*\]\((docs/screenshots/[^)]+)\)", readme)
    assert [Path(s).name.split("-")[0] for s in shots] == ["1", "2", "3", "4"]  # shown in sequence
    problems = []
    for f in [root / "README.md", root / "CONTRIBUTING.md", *sorted((root / "docs").glob("*.md"))]:
        for m in re.finditer(r"\]\((?!https?://|#|mailto:)([^)#]+)(#[^)]*)?\)", f.read_text()):
            if not (f.parent / m.group(1)).resolve().exists():
                problems.append(f"{f.name}: {m.group(1)}")
    assert not problems, problems


def test_a_job_we_sent_keeps_its_name_in_the_queue_even_if_cups_lists_it_without_one(client):
    h = csrf_headers(client)
    job_id = client.post(f"/api/maintenance/{PID}/nozzle/print", headers=h).get_json()["data"]["job_id"]
    ctx = client.application.extensions["panda"]["ctx"]
    backend = ctx.backend_for(ctx.printers.get(PID))
    for j in backend.jobs:
        if j["job-id"] == [job_id] or j.get("job-id") == job_id:
            j.pop("job-name", None); j.pop("document-name-supplied", None)
    jobs = client.get(f"/api/maintenance/{PID}/queue").get_json()["data"]["jobs"]
    mine = next(j for j in jobs if j["id"] == job_id)
    nz = client.get(f"/api/maintenance/{PID}/nozzle").get_json()["data"]["last_manual"]
    assert nz["page_name"] and mine["name"] == nz["page_name"] != "(unnamed)"
