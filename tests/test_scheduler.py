"""Scheduler behaviour: due-set computation, ticks, check_now and pruning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from app.alerts import Notifier
from app.db import Database
from app.scheduler import Scheduler
from tests.factories import default_settings, insert_check, insert_monitor, insert_rule


def build_scheduler(db, settings, handler=lambda request: httpx.Response(200)) -> Scheduler:
    notifier = Notifier(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0
    )
    return Scheduler(db, settings, notifier, transport=httpx.MockTransport(handler))


def test_due_monitors_respect_interval_and_enabled_flag(tmp_path):
    settings = default_settings(tmp_path)
    db = Database(settings.database_path)
    scheduler = build_scheduler(db, settings)
    insert_monitor(db, id=1, interval_seconds=60)
    insert_monitor(db, id=2, interval_seconds=300)
    insert_monitor(db, id=3, enabled=0, interval_seconds=60)

    now = 1_000_000.0
    due = {m["id"] for m in scheduler.due_monitors(now)}
    assert due == {1, 2}  # unknown schedule -> due immediately

    scheduler.reschedule(dict(db.query_one("SELECT * FROM monitors WHERE id = 1")), now)
    scheduler.reschedule(dict(db.query_one("SELECT * FROM monitors WHERE id = 2")), now)
    # Fixed-rate booking: next run lands exactly one interval after dispatch.
    assert scheduler.due_monitors(now + 59) == []
    assert {m["id"] for m in scheduler.due_monitors(now + 60)} == {1}
    assert {m["id"] for m in scheduler.due_monitors(now + 300)} == {1, 2}

    # Disabled monitors are never due, even if they were scheduled before.
    scheduler.reschedule(dict(db.query_one("SELECT * FROM monitors WHERE id = 1")), now)
    assert all(m["id"] != 3 for m in scheduler.due_monitors(now))
    db.close()


async def test_tick_runs_due_monitor_and_books_next_run(tmp_path):
    settings = default_settings(tmp_path)
    db = Database(settings.database_path)
    scheduler = build_scheduler(db, settings)
    insert_monitor(db, id=1, interval_seconds=60, url="https://api.test/health")

    now = 5_000.0
    results = await scheduler.tick(now)
    assert len(results) == 1
    assert results[0]["check"]["status_code"] == 200
    assert results[0]["monitor"]["status"] == "up"
    assert scheduler.checks_run == 1

    # Next run is one interval away; an immediate re-tick must not double-check.
    assert await scheduler.tick(now + 10) == []
    assert scheduler.checks_run == 1
    assert len(await scheduler.tick(now + 60)) == 1
    db.close()


async def test_tick_opens_incident_and_notifies(tmp_path):
    settings = default_settings(tmp_path, default_webhook_url="https://webhook.test/alerts")
    db = Database(settings.database_path)
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "webhook.test":
            import json

            payloads.append(json.loads(request.content))
            return httpx.Response(200)
        return httpx.Response(500)

    scheduler = build_scheduler(db, settings, handler=handler)
    insert_monitor(db, id=1, interval_seconds=60, unhealthy_threshold=1, url="https://api.test/health")
    insert_rule(db, monitor_id=None, type="failure_streak", threshold=1)

    await scheduler.tick(100.0)
    assert [p["event"] for p in payloads] == ["incident.opened"]
    incident = db.query_one("SELECT * FROM incidents")
    assert incident["status"] == "open"
    db.close()


async def test_check_now_runs_immediately_and_handles_missing(tmp_path):
    settings = default_settings(tmp_path)
    db = Database(settings.database_path)
    scheduler = build_scheduler(db, settings)
    insert_monitor(db, id=1, interval_seconds=600)

    assert await scheduler.check_now(999) is None
    result = await scheduler.check_now(1)
    assert result["check"]["ok"] is True
    db.close()


async def test_concurrent_checks_are_counted(tmp_path):
    settings = default_settings(tmp_path, check_concurrency=2)
    db = Database(settings.database_path)
    scheduler = build_scheduler(db, settings)
    insert_monitor(db, id=1, interval_seconds=60, url="https://a.test/")
    insert_monitor(db, id=2, interval_seconds=60, url="https://b.test/")
    insert_monitor(db, id=3, interval_seconds=60, url="https://c.test/")

    results = await scheduler.tick(42.0)
    assert len(results) == 3
    assert scheduler.checks_run == 3
    assert db.query_one("SELECT COUNT(*) AS n FROM checks")["n"] == 3
    db.close()


def test_prune_metrics_removes_expired_history_only(tmp_path):
    settings = default_settings(tmp_path, metric_retention_days=7)
    db = Database(settings.database_path)
    scheduler = build_scheduler(db, settings)
    insert_monitor(db, id=1)

    old = (datetime.now(UTC) - timedelta(days=10)).isoformat(timespec="milliseconds")
    fresh = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="milliseconds")
    insert_check(db, 1, id=1, checked_at=old)
    insert_check(db, 1, id=2, checked_at=fresh)

    pruned = scheduler.prune_metrics()
    assert pruned == 1
    remaining = db.query_one("SELECT id FROM checks")
    assert remaining["id"] == 2
    db.close()


async def test_start_and_stop_manage_background_tasks(tmp_path):
    settings = default_settings(tmp_path, scheduler_enabled=True)
    db = Database(settings.database_path)
    scheduler = build_scheduler(db, settings)
    insert_monitor(db, id=1, interval_seconds=60)

    await scheduler.start()
    assert scheduler.running is True
    assert len(scheduler._tasks) == 2

    await scheduler.stop()
    assert scheduler.running is False
    assert scheduler._tasks == []
    db.close()


async def test_start_is_a_no_op_when_disabled(tmp_path):
    settings = default_settings(tmp_path, scheduler_enabled=False)
    db = Database(settings.database_path)
    scheduler = build_scheduler(db, settings)

    await scheduler.start()
    assert scheduler.running is False
    assert scheduler._tasks == []
    db.close()
