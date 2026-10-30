"""Alert rule evaluation: streaks, sustained latency and windowed error rates."""

from __future__ import annotations

from datetime import UTC

import numpy as np

from app.rules import build_context, checks_needed_for_duration, evaluate, rules_for_monitor
from tests.factories import check_row, monitor_row


def rule(**overrides) -> dict:
    base = {
        "id": 1,
        "name": "rule",
        "monitor_id": None,
        "type": "failure_streak",
        "threshold": 3,
        "window_seconds": 300,
        "duration_seconds": 0,
        "min_samples": 5,
        "webhook_url": "",
        "email_to": "[]",
        "enabled": 1,
    }
    base.update(overrides)
    return base


def history(spec: list[dict]) -> list[dict]:
    """Build a newest-first check history from (ok, latency, age_seconds) tuples."""
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    rows = []
    for index, item in enumerate(spec):
        ok, latency, age = item
        rows.append(
            check_row(
                id=len(spec) - index,
                ok=int(ok),
                latency_ms=latency,
                checked_at=(now - timedelta(seconds=age)).isoformat(timespec="milliseconds"),
            )
        )
    return rows


def test_failure_streak_fires_only_at_threshold():
    monitor = monitor_row(unhealthy_threshold=3)

    ctx = build_context({**monitor, "consecutive_failures": 2}, history([(False, 10, 0)] * 2))
    assert evaluate(rule(threshold=3), ctx).fired is False

    ctx = build_context({**monitor, "consecutive_failures": 3}, history([(False, 10, 0)] * 3))
    result = evaluate(rule(threshold=3), ctx)
    assert result.fired is True
    assert "3 consecutive failed checks" in result.reason


def test_latency_rule_respects_duration():
    monitor = monitor_row(interval_seconds=60)
    # duration 120s at 60s interval -> two consecutive violating checks required.
    assert checks_needed_for_duration(rule(type="latency", threshold=500, duration_seconds=120), monitor) == 2

    latency_rule = rule(type="latency", threshold=500, duration_seconds=120)
    only_one = history([(True, 900, 0), (True, 100, 60)])
    assert evaluate(latency_rule, build_context(monitor, only_one)).fired is False

    two_slow = history([(True, 900, 0), (True, 1100, 60), (True, 100, 120)])
    result = evaluate(latency_rule, build_context(monitor, two_slow))
    assert result.fired is True
    assert "1100ms" in result.reason


def test_latency_rule_without_duration_fires_on_last_check():
    monitor = monitor_row()
    fast = history([(True, 80, 0), (True, 900, 60)])
    assert evaluate(rule(type="latency", threshold=500), build_context(monitor, fast)).fired is False

    slow = history([(True, 900, 0), (True, 80, 60)])
    assert evaluate(rule(type="latency", threshold=500), build_context(monitor, slow)).fired is True


def test_latency_rule_ignores_failed_checks_without_latency():
    monitor = monitor_row()
    # A timeout records no latency: it must not count as a latency violation.
    checks = history([(False, None, 0), (False, None, 50), (True, 50, 100)])
    result = evaluate(rule(type="latency", threshold=500), build_context(monitor, checks))
    assert result.fired is False


def test_error_rate_needs_min_samples_and_honours_window():
    monitor = monitor_row()
    error_rule = rule(type="error_rate", threshold=0.5, window_seconds=300, min_samples=4)

    # Only 3 samples in the window: below min_samples.
    checks = history([(False, 10, 0), (False, 10, 30), (False, 10, 60)])
    assert evaluate(error_rule, build_context(monitor, checks)).fired is False

    # 4 samples, 3 failures -> 75% >= 50%.
    checks = history([(False, 10, 0), (False, 10, 30), (False, 10, 60), (True, 10, 90)])
    result = evaluate(error_rule, build_context(monitor, checks))
    assert result.fired is True
    assert "75%" in result.reason

    # Same failures but outside the window: ignored.
    checks = history(
        [(False, 10, 400), (False, 10, 430), (False, 10, 460), (True, 10, 490), (True, 10, 0)]
    )
    assert evaluate(error_rule, build_context(monitor, checks)).fired is False


def test_disabled_rule_never_fires():
    monitor = monitor_row()
    ctx = build_context({**monitor, "consecutive_failures": 10}, history([(False, 10, 0)] * 5))
    assert evaluate(rule(enabled=0), ctx).fired is False


def test_rules_for_monitor_scopes_correctly():
    global_rule = rule(id=1, monitor_id=None)
    own = rule(id=2, monitor_id=7)
    other = rule(id=3, monitor_id=8)
    selected = rules_for_monitor([global_rule, own, other], monitor_row(id=7))
    assert [r["id"] for r in selected] == [1, 2]


# -- anomaly (Isolation Forest) ----------------------------------------------
def anomaly_rule(**overrides) -> dict:
    """Anomaly rule with production-safe defaults: 2% contamination, 1h window."""
    base = dict(type="anomaly", threshold=0.02, window_seconds=3600, min_samples=30)
    base.update(overrides)
    return rule(**base)


def latency_series(values, *, age_step: int = 60) -> list[dict]:
    """Newest-first history from latencies given oldest -> newest, one row per minute."""
    spec = [(True, float(v), index * age_step) for index, v in enumerate(reversed(values))]
    return history(spec)


def noisy_baseline(n: int, seed: int = 7) -> list[float]:
    rng = np.random.default_rng(seed)
    return [float(v) for v in 100.0 + rng.normal(0.0, 3.0, n)]


def test_anomaly_rule_fires_on_trailing_spike():
    values = noisy_baseline(60)
    values[-1] = 420.0  # the check just recorded is the anomalous one
    result = evaluate(anomaly_rule(), build_context(monitor_row(), latency_series(values)))
    assert result.fired is True
    assert "Isolation Forest" in result.reason
    assert "420ms" in result.reason


def test_anomaly_rule_stays_silent_on_steady_history():
    values = noisy_baseline(60)
    ctx = build_context(monitor_row(), latency_series(values))
    assert evaluate(anomaly_rule(), ctx).fired is False


def test_anomaly_rule_needs_detector_minimum_samples():
    values = noisy_baseline(20)  # below MIN_POINTS (30)
    values[-1] = 420.0
    ctx = build_context(monitor_row(), latency_series(values))
    assert evaluate(anomaly_rule(min_samples=1), ctx).fired is False


def test_anomaly_rule_window_must_hold_enough_samples():
    values = noisy_baseline(60)
    values[-1] = 420.0
    checks = latency_series(values)

    # A 300s window only covers ~6 of the 60 one-minute samples -> nothing to fit.
    narrow = anomaly_rule(window_seconds=300)
    assert evaluate(narrow, build_context(monitor_row(), checks)).fired is False

    # The same data with an hour-long window has all 60 samples.
    wide = anomaly_rule(window_seconds=3600)
    assert evaluate(wide, build_context(monitor_row(), checks)).fired is True


def test_anomaly_rule_does_not_refire_on_a_stale_anomaly():
    values = noisy_baseline(60)
    values[30] = 420.0  # anomalous, but 30 checks ago; the latest sample is normal
    ctx = build_context(monitor_row(), latency_series(values))
    assert evaluate(anomaly_rule(), ctx).fired is False


def test_anomaly_rule_skips_timeout_samples():
    """A timeout records no latency: it must not be read as a 0ms outlier."""
    values = noisy_baseline(50)
    values[-1] = 420.0
    spec = [(True, v, index * 60) for index, v in enumerate(reversed(values))]
    for offset in range(30, 50):  # the 20 oldest checks timed out
        ok, latency, age = spec[offset]
        spec[offset] = (False, None, age)
    checks = history(spec)
    usable = [c for c in checks if c["latency_ms"] is not None]
    assert len(usable) == 30  # exactly the detector's floor

    result = evaluate(anomaly_rule(), build_context(monitor_row(), checks))
    assert result.fired is True


def test_anomaly_rule_gives_no_verdict_below_the_floor():
    # Only 10 usable samples: below the detector's floor, so never firing.
    values = noisy_baseline(10)
    values.append(420.0)
    spec = [(True, v, index * 60) for index, v in enumerate(reversed(values))]
    for offset in range(11, 61):
        spec.append((False, None, offset * 60))
    ctx = build_context(monitor_row(), history(spec))
    assert evaluate(anomaly_rule(), ctx).fired is False
