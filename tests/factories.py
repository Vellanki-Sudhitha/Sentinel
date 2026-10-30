"""Deterministic row/settings factories used across the test suite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.db import utcnow

BASE_TIME = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)


def default_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        database_path=str(tmp_path / "test.db"),
        scheduler_enabled=False,  # tests drive ticks explicitly
        check_concurrency=4,
        metric_retention_days=30,
        maintenance_interval_seconds=3600,
        http_timeout_seconds=5.0,
        default_webhook_url="",
        webhook_retries=3,
        webhook_timeout_seconds=5.0,
        email_host="",
        email_port=587,
        email_user="",
        email_password="",
        email_use_tls=True,
        email_from="sentinel@localhost",
        default_alert_emails=[],
        notify_on_recovery=True,
    )
    values.update(overrides)
    return Settings(**values)


def monitor_row(**overrides) -> dict:
    """A monitor dict shaped exactly like a `monitors` table row."""
    now = utcnow()
    row = {
        "id": 1,
        "name": "payments-api",
        "url": "https://api.example.com/health",
        "method": "GET",
        "headers": "{}",
        "interval_seconds": 60,
        "timeout_seconds": 5.0,
        "expected_status_min": 200,
        "expected_status_max": 399,
        "unhealthy_threshold": 3,
        "healthy_threshold": 2,
        "enabled": 1,
        "status": "unknown",
        "consecutive_failures": 0,
        "consecutive_successes": 0,
        "last_checked_at": None,
        "last_ok": None,
        "last_status_code": None,
        "last_latency_ms": None,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def check_row(**overrides) -> dict:
    row = {
        "id": 1,
        "monitor_id": 1,
        "checked_at": utcnow(),
        "ok": 1,
        "status_code": 200,
        "latency_ms": 42.0,
        "error": None,
    }
    row.update(overrides)
    return row


def iso(offset_seconds: float = 0, base: datetime | None = None) -> str:
    base = base or datetime.now(UTC)
    return (base + timedelta(seconds=offset_seconds)).isoformat(timespec="milliseconds")


def insert_monitor(db, **overrides) -> dict:
    row = monitor_row(**overrides)
    db.execute(
        """
        INSERT INTO monitors (id, name, url, method, headers, interval_seconds, timeout_seconds,
            expected_status_min, expected_status_max, unhealthy_threshold, healthy_threshold,
            enabled, status, consecutive_failures, consecutive_successes, last_checked_at, last_ok,
            last_status_code, last_latency_ms, last_error, created_at, updated_at)
        VALUES (:id, :name, :url, :method, :headers, :interval_seconds, :timeout_seconds,
            :expected_status_min, :expected_status_max, :unhealthy_threshold, :healthy_threshold,
            :enabled, :status, :consecutive_failures, :consecutive_successes, :last_checked_at,
            :last_ok, :last_status_code, :last_latency_ms, :last_error, :created_at, :updated_at)
        """,
        row,
    )
    return row


def insert_check(db, monitor_id: int, **overrides) -> dict:
    row = check_row(monitor_id=monitor_id, **overrides)
    db.execute(
        "INSERT INTO checks (id, monitor_id, checked_at, ok, status_code, latency_ms, error)"
        " VALUES (:id, :monitor_id, :checked_at, :ok, :status_code, :latency_ms, :error)",
        row,
    )
    return row


def insert_rule(db, **overrides) -> dict:
    row = {
        "id": 1,
        "name": "default failure rule",
        "monitor_id": None,
        "type": "failure_streak",
        "threshold": 3,
        "window_seconds": 300,
        "duration_seconds": 0,
        "min_samples": 5,
        "webhook_url": "",
        "email_to": "[]",
        "enabled": 1,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    row.update(overrides)
    db.execute(
        """
        INSERT INTO alert_rules (id, name, monitor_id, type, threshold, window_seconds,
            duration_seconds, min_samples, webhook_url, email_to, enabled, created_at, updated_at)
        VALUES (:id, :name, :monitor_id, :type, :threshold, :window_seconds, :duration_seconds,
            :min_samples, :webhook_url, :email_to, :enabled, :created_at, :updated_at)
        """,
        row,
    )
    return row
