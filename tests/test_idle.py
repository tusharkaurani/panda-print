from app.core.ratelimit import RateRule, check
from app.tools.maintenance import idle

DAY = 86400


def test_parse_host_state():
    assert idle.parse_host_state("174 1799900000.5\n") == idle.HostState(174, 1799900000.5)
    assert idle.parse_host_state("garbage") is None
    assert idle.parse_host_state("") is None
    assert idle.parse_host_state("1 2 3") is None


def test_idle_days_and_next_check():
    now = 1_000_000.0
    assert idle.idle_days(now - 2.5 * DAY, now) == 2.5
    assert idle.idle_days(now + 100, now) == 0  # clock skew never goes negative
    nxt = idle.next_check(now - 2.5 * DAY, now, 7)
    assert round(nxt.days_until, 3) == 4.5 and not nxt.overdue
    assert nxt.due_at == now - 2.5 * DAY + 7 * DAY
    late = idle.next_check(now - 8 * DAY, now, 7)
    assert late.overdue and round(late.days_until, 3) == -1.0


def test_decide_matches_the_host_script():
    now = 10 * DAY
    # first run: record, never print
    assert idle.decide(None, 174, now, 7).action == "record"
    # counter changed: record the new state at 'now'
    d = idle.decide((170, 0), 174, now, 7)
    assert d.action == "record" and d.state == (174, now)
    # unchanged but idle < 7 days: wait
    d = idle.decide((174, now - 6.9 * DAY), 174, now, 7)
    assert d.action == "wait" and round(d.idle_days, 1) == 6.9
    # unchanged for exactly 7 days: print
    assert idle.decide((174, now - 7 * DAY), 174, now, 7).action == "print"
    assert idle.decide((174, now - 30 * DAY), 174, now, 7).action == "print"


def test_rate_rule():
    rule = RateRule(min_interval=120, max_events=3, window=3600)
    now = 10_000.0
    assert check(rule, [], now).allowed
    v = check(rule, [now - 30], now)
    assert not v.allowed and 89 <= v.retry_after <= 91
    assert check(rule, [now - 121], now).allowed
    burst = [now - 3000, now - 2000, now - 1000]
    v = check(rule, burst, now)
    assert not v.allowed and v.retry_after == 601  # oldest event leaves the window in 600s
    assert check(rule, burst, now + 700).allowed
