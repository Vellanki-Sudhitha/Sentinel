"""Incident lifecycle against a real database."""

from __future__ import annotations

from app.db import Database
from app.incidents import (
    acknowledge_incident,
    get_open_incident,
    resolve_incident,
    sync_incidents,
)
from tests.factories import default_settings, insert_check, insert_monitor, insert_rule, monitor_row


def advance(monitor: dict, *, ok: bool) -> dict:
    """Fold one check outcome into the monitor's counters (as monitor.record does)."""
    if ok:
        return {**monitor, "consecutive_failures": 0, "consecutive_successes": monitor["consecutive_successes"] + 1}
    return {**monitor, "consecutive_failures": monitor["consecutive_failures"] + 1, "consecutive_successes": 0}


def test_opens_incident_when_streak_threshold_reached(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, unhealthy_threshold=3, healthy_threshold=2)
    insert_rule(db, type="failure_streak", threshold=3, webhook_url="https://webhook.test/h")

    monitor = advance(monitor, ok=False)
    insert_check(db, 1, ok=0, status_code=500, id=1)
    monitor = advance(monitor, ok=False)
    insert_check(db, 1, ok=0, status_code=500, id=2)
    assert sync_incidents(db, monitor) == []  # only 2 failures: nothing to report

    monitor = advance(monitor, ok=False)
    insert_check(db, 1, ok=0, status_code=500, id=3)
    events = sync_incidents(db, monitor)
    assert len(events) == 1
    assert events[0].kind == "opened"
    assert "3 consecutive failed checks" in events[0].incident["reason"]
    assert get_open_incident(db, 1) is not None
    db.close()


def test_still_firing_does_not_open_a_second_incident(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, unhealthy_threshold=2, consecutive_failures=2)
    insert_rule(db, type="failure_streak", threshold=2)

    events = sync_incidents(db, monitor)
    assert [e.kind for e in events] == ["opened"]

    monitor = advance(monitor, ok=False)
    insert_check(db, 1, ok=0, id=1)
    assert sync_incidents(db, monitor) == []  # incident already open -> silent

    incident = get_open_incident(db, 1)
    assert "3 consecutive failed checks" in incident["reason"]  # reason kept current
    assert db.query_one("SELECT COUNT(*) AS n FROM incidents")["n"] == 1
    db.close()


def test_anomaly_rule_opens_and_resolves_incident(tmp_path):
    """The ML detector drives the full incident lifecycle, not just the chart."""
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, unhealthy_threshold=3, healthy_threshold=2)
    insert_rule(
        db,
        type="anomaly",
        threshold=0.02,
        min_samples=30,
        window_seconds=3600,
        webhook_url="https://webhook.test/ml",
    )

    # 40 one-minute baseline checks: a steady ~100ms service.
    for index in range(40):
        insert_check(db, 1, id=index + 1, latency_ms=100.0 + (index % 5) * 2.0)
    assert sync_incidents(db, monitor) == []  # nothing anomalous yet

    # The check just recorded is a 900ms outlier -> incident opens.
    insert_check(db, 1, id=41, latency_ms=900.0)
    events = sync_incidents(db, monitor)
    assert [e.kind for e in events] == ["opened"]
    assert "Isolation Forest" in events[0].incident["reason"]
    assert "900ms" in events[0].incident["reason"]
    assert get_open_incident(db, 1) is not None

    # Two more normal checks: the anomaly is no longer current, the monitor is
    # healthy again -> the incident resolves instead of lingering.
    monitor = advance(monitor, ok=True)
    insert_check(db, 1, id=42, latency_ms=98.0)
    monitor = advance(monitor, ok=True)
    insert_check(db, 1, id=43, latency_ms=101.0)
    events = sync_incidents(db, monitor)
    assert [e.kind for e in events] == ["resolved"]
    assert get_open_incident(db, 1) is None
    db.close()


def test_recovery_resolves_incident_after_healthy_threshold(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, unhealthy_threshold=2, healthy_threshold=2, consecutive_failures=2)
    insert_rule(db, type="failure_streak", threshold=2)

    sync_incidents(db, monitor)

    monitor = advance(monitor, ok=True)
    insert_check(db, 1, ok=1, id=1)
    assert sync_incidents(db, monitor) == []  # one success is not confirmation

    monitor = advance(monitor, ok=True)
    insert_check(db, 1, ok=1, id=2)
    events = sync_incidents(db, monitor)
    assert [e.kind for e in events] == ["resolved"]
    assert events[0].incident["closed_at"] is not None
    assert get_open_incident(db, 1) is None
    db.close()


def test_incident_stays_open_while_rule_still_firing(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, unhealthy_threshold=1, healthy_threshold=1, consecutive_failures=1)
    insert_rule(db, type="failure_streak", threshold=1)
    sync_incidents(db, monitor)

    monitor = advance(monitor, ok=False)
    assert sync_incidents(db, monitor) == []
    assert get_open_incident(db, 1) is not None
    db.close()


def test_manual_resolve_and_acknowledge(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, unhealthy_threshold=1, healthy_threshold=1, consecutive_failures=1)
    insert_rule(db, type="failure_streak", threshold=1)
    sync_incidents(db, monitor)

    incident = get_open_incident(db, 1)
    acknowledged = acknowledge_incident(db, incident["id"])
    assert acknowledged["acknowledged_at"] is not None

    event = resolve_incident(db, incident["id"])
    assert event is not None and event.kind == "resolved"
    assert resolve_incident(db, incident["id"]) is None  # already resolved
    assert acknowledge_incident(db, incident["id"]) is None  # resolved -> no-op
    db.close()


def test_latency_rule_opens_incident(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, interval_seconds=60)
    insert_rule(db, type="latency", threshold=500)
    insert_check(db, 1, ok=1, latency_ms=1200, id=1)

    events = sync_incidents(db, monitor)
    assert len(events) == 1
    assert "1200ms" in events[0].incident["reason"]
    db.close()


def test_monitor_scoped_rule_ignores_other_monitors(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, id=2, name="other", consecutive_failures=5, unhealthy_threshold=2)
    insert_monitor(db, id=99, name="elsewhere", consecutive_failures=0)
    insert_rule(db, monitor_id=99, type="failure_streak", threshold=2)  # belongs elsewhere

    assert sync_incidents(db, monitor) == []
    assert get_open_incident(db, 2) is None
    db.close()


def test_global_rule_applies_to_every_monitor(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, id=2, name="other", consecutive_failures=5, unhealthy_threshold=2)
    insert_rule(db, monitor_id=None, type="failure_streak", threshold=2)

    events = sync_incidents(db, monitor)
    assert [e.kind for e in events] == ["opened"]
    db.close()


def test_monitor_row_factory_shape():
    row = monitor_row()
    assert row["consecutive_failures"] == 0 and row["enabled"] == 1
