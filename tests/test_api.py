"""REST API behaviour: CRUD, validation, metrics, incidents and the summary."""

from __future__ import annotations

import json
from datetime import UTC

import httpx

from app.db import Database
from tests.factories import insert_check, insert_rule

OK_HANDLER_STATE = {"broken": False}


def dashboard_handler(request: httpx.Request) -> httpx.Response:
    """Fake upstream: `api.test` flips between healthy and failing."""
    if request.url.host == "api.test":
        return httpx.Response(500 if OK_HANDLER_STATE["broken"] else 200, text="hi")
    return httpx.Response(404)


def create(client, **overrides) -> dict:
    payload = {"name": "payments", "url": "https://api.test/health", **overrides}
    response = client.post("/api/monitors", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# -- monitors ---------------------------------------------------------------
def test_monitor_crud_round_trip(client):
    monitor = create(client, interval_seconds=30, headers={"X-Token": "abc"})
    assert monitor["status"] == "unknown"
    assert monitor["method"] == "GET"
    assert monitor["headers"] == {"X-Token": "abc"}
    assert monitor["enabled"] is True

    listed = client.get("/api/monitors").json()
    assert [m["id"] for m in listed] == [monitor["id"]]

    fetched = client.get(f"/api/monitors/{monitor['id']}").json()
    assert fetched["url"] == "https://api.test/health"

    updated = client.put(
        f"/api/monitors/{monitor['id']}",
        json={"interval_seconds": 120, "enabled": False, "name": "payments-v2"},
    ).json()
    assert updated["interval_seconds"] == 120
    assert updated["enabled"] is False
    assert updated["name"] == "payments-v2"
    assert updated["url"] == "https://api.test/health"  # untouched by partial update

    assert client.delete(f"/api/monitors/{monitor['id']}").status_code == 204
    assert client.get(f"/api/monitors/{monitor['id']}").status_code == 404


def test_monitor_validation_errors(client):
    assert client.post("/api/monitors", json={"name": "x", "url": "ftp://nope"}).status_code == 422
    assert client.post("/api/monitors", json={"name": "x", "url": "https://a.b", "method": "YEET"}).status_code == 422
    assert (
        client.post(
            "/api/monitors",
            json={"name": "x", "url": "https://a.b", "expected_status_min": 400, "expected_status_max": 200},
        ).status_code
        == 422
    )
    assert (
        client.post("/api/monitors", json={"name": "x", "url": "https://a.b", "interval_seconds": 3}).status_code
        == 422
    )
    assert client.post("/api/monitors", json={"url": "https://a.b"}).status_code == 422


def test_update_unknown_monitor_is_404(client):
    assert client.put("/api/monitors/42", json={"name": "ghost"}).status_code == 404
    assert client.delete("/api/monitors/42").status_code == 404


# -- manual checks & history ------------------------------------------------
def test_manual_check_records_result(client):
    # The shared transport answers 200 for webhook.test hosts, 404 elsewhere.
    monitor = create(client, url="https://webhook.test/health")
    response = client.post(f"/api/monitors/{monitor['id']}/check")
    assert response.status_code == 200
    body = response.json()
    assert body["check"]["ok"] is True
    assert body["monitor"]["status"] == "up"
    assert body["monitor"]["last_status_code"] == 200

    checks = client.get(f"/api/monitors/{monitor['id']}/checks").json()
    assert len(checks) == 1 and checks[0]["status_code"] == 200


def test_checks_listing_and_limits(client, db: Database):
    monitor = create(client)
    for index in range(5):
        insert_check(db, monitor["id"], id=index + 1, ok=0, status_code=500)
    listed = client.get(f"/api/monitors/{monitor['id']}/checks?limit=3").json()
    assert len(listed) == 3
    assert listed[0]["id"] == 5  # newest first


# -- metrics ----------------------------------------------------------------
def test_metrics_aggregation(client, db: Database):
    monitor = create(client)
    # 8 checks: 2 failures, latencies 10..80ms.
    for index in range(8):
        insert_check(
            db,
            monitor["id"],
            id=index + 1,
            ok=0 if index < 2 else 1,
            latency_ms=10.0 * (index + 1),
        )

    metrics = client.get(f"/api/monitors/{monitor['id']}/metrics?hours=24&buckets=4").json()
    assert metrics["total"] == 8
    assert metrics["failures"] == 2
    assert metrics["uptime"] == 0.75
    assert metrics["avg_latency_ms"] == 45.0
    assert metrics["p95_latency_ms"] == 80.0
    assert metrics["max_latency_ms"] == 80.0
    assert len(metrics["buckets"]) == 4
    assert sum(b["total"] for b in metrics["buckets"]) == 8
    assert all("start" in bucket and "end" in bucket for bucket in metrics["buckets"])


def test_metrics_empty_monitor(client):
    monitor = create(client)
    metrics = client.get(f"/api/monitors/{monitor['id']}/metrics").json()
    assert metrics["total"] == 0 and metrics["uptime"] == 0.0
    assert metrics["avg_latency_ms"] is None


def test_metrics_window_filter(client, db: Database):
    from datetime import datetime, timedelta

    monitor = create(client)
    fresh = datetime.now(UTC).isoformat(timespec="milliseconds")
    stale = (datetime.now(UTC) - timedelta(days=3)).isoformat(timespec="milliseconds")
    insert_check(db, monitor["id"], id=1, checked_at=fresh)
    insert_check(db, monitor["id"], id=2, checked_at=stale, ok=0, status_code=500)

    day = client.get(f"/api/monitors/{monitor['id']}/metrics?hours=24").json()
    assert day["total"] == 1 and day["uptime"] == 1.0
    week = client.get(f"/api/monitors/{monitor['id']}/metrics?hours=168").json()
    assert week["total"] == 2 and week["uptime"] == 0.5


# -- rules ------------------------------------------------------------------
def test_rule_crud(client):
    created = client.post(
        "/api/rules",
        json={
            "name": "trip failures",
            "type": "failure_streak",
            "threshold": 3,
            "webhook_url": "https://hooks.test/x",
            "email_to": ["a@example.com", "b@example.com"],
        },
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["email_to"] == ["a@example.com", "b@example.com"]
    assert rule["monitor_id"] is None  # global rule

    updated = client.put(f"/api/rules/{rule['id']}", json={"threshold": 5, "enabled": False}).json()
    assert updated["threshold"] == 5 and updated["enabled"] is False

    assert client.get("/api/rules").json()[0]["id"] == rule["id"]
    assert client.delete(f"/api/rules/{rule['id']}").status_code == 204
    assert client.get(f"/api/rules/{rule['id']}").status_code == 404


def test_rule_validation(client):
    bad_type = client.post("/api/rules", json={"name": "x", "type": "vibes", "threshold": 1})
    assert bad_type.status_code == 422

    bad_rate = client.post(
        "/api/rules", json={"name": "x", "type": "error_rate", "threshold": 1.5}
    )
    assert bad_rate.status_code == 422

    bad_monitor = client.post(
        "/api/rules", json={"name": "x", "type": "latency", "threshold": 100, "monitor_id": 77}
    )
    assert bad_monitor.status_code == 404


def test_anomaly_rule_validation(client):
    """Anomaly rules take a contamination ratio and respect the detector's floor."""
    ok = client.post(
        "/api/rules",
        json={"name": "latency outliers", "type": "anomaly", "threshold": 0.05, "min_samples": 50},
    )
    assert ok.status_code == 201, ok.text
    rule = ok.json()
    assert rule["type"] == "anomaly" and rule["threshold"] == 0.05
    assert rule["min_samples"] == 50

    # /api/rule-types is the single source the UI reads: it must list anomaly now.
    types = client.get("/api/rule-types").json()["types"]
    assert "anomaly" in types and len(types) == 4

    too_wild = client.post(
        "/api/rules", json={"name": "x", "type": "anomaly", "threshold": 0.8, "min_samples": 50}
    )
    assert too_wild.status_code == 422

    too_little_history = client.post(
        "/api/rules", json={"name": "x", "type": "anomaly", "threshold": 0.05, "min_samples": 5}
    )
    assert too_little_history.status_code == 422


def test_rule_scoped_list(client):
    monitor = create(client)
    client.post("/api/rules", json={"name": "global", "type": "failure_streak", "threshold": 3})
    client.post(
        "/api/rules",
        json={"name": "mine", "type": "latency", "threshold": 300, "monitor_id": monitor["id"]},
    )
    scoped = client.get(f"/api/rules?monitor_id={monitor['id']}").json()
    assert sorted(r["name"] for r in scoped) == ["global", "mine"]


# -- incidents --------------------------------------------------------------
def test_incident_open_ack_and_recovery_flow(make_client):
    """Full lifecycle through the API: fail -> alert -> acknowledge -> recover."""
    OK_HANDLER_STATE["broken"] = True
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hooks.test":
            payloads.append(json.loads(request.content))
            return httpx.Response(200)
        return dashboard_handler(request)

    client = make_client(handler)
    monitor = client.post(
        "/api/monitors",
        json={
            "name": "payments",
            "url": "https://api.test/health",
            "unhealthy_threshold": 1,
            "healthy_threshold": 1,
        },
    ).json()
    rule = client.post(
        "/api/rules",
        json={
            "name": "failures",
            "type": "failure_streak",
            "threshold": 1,
            "webhook_url": "https://hooks.test/alert",
        },
    ).json()
    assert rule["type"] == "failure_streak"

    body = client.post(f"/api/monitors/{monitor['id']}/check").json()
    assert body["check"]["ok"] is False
    assert [event["kind"] for event in body["incidents"]] == ["opened"]
    assert [p["event"] for p in payloads] == ["incident.opened"]

    open_incidents = client.get("/api/incidents?status=open").json()
    assert len(open_incidents) == 1
    assert open_incidents[0]["monitor_name"] == "payments"
    incident_id = open_incidents[0]["id"]

    acknowledged = client.post(f"/api/incidents/{incident_id}/acknowledge").json()
    assert acknowledged["acknowledged_at"] is not None

    # Target comes back healthy: one good check confirms recovery (threshold=1).
    OK_HANDLER_STATE["broken"] = False
    body = client.post(f"/api/monitors/{monitor['id']}/check").json()
    assert body["incidents"][0]["kind"] == "resolved"
    assert [p["event"] for p in payloads] == ["incident.opened", "incident.resolved"]
    assert client.get("/api/incidents?status=open").json() == []
    assert len(client.get("/api/incidents?status=resolved").json()) == 1

    # Second resolve attempt is rejected.
    assert client.post(f"/api/incidents/{incident_id}/resolve").status_code == 409


def test_manual_resolve_sends_recovery_alert(make_client):
    OK_HANDLER_STATE["broken"] = True
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hooks.test":
            payloads.append(json.loads(request.content))
            return httpx.Response(200)
        return dashboard_handler(request)

    client = make_client(handler)
    monitor = client.post(
        "/api/monitors",
        json={"name": "db", "url": "https://api.test/health", "unhealthy_threshold": 1, "healthy_threshold": 3},
    ).json()
    client.post(
        "/api/rules",
        json={"name": "f", "type": "failure_streak", "threshold": 1, "webhook_url": "https://hooks.test/a"},
    )
    client.post(f"/api/monitors/{monitor['id']}/check")

    incident_id = client.get("/api/incidents?status=open").json()[0]["id"]
    resolved = client.post(f"/api/incidents/{incident_id}/resolve").json()
    assert resolved["status"] == "resolved" and resolved["closed_at"] is not None
    assert payloads[-1]["event"] == "incident.resolved"


def test_monitor_incidents_listing(client, db: Database):
    monitor = create(client)
    insert_rule(db, id=1, monitor_id=monitor["id"], type="failure_streak", threshold=1)
    db.execute(
        "INSERT INTO incidents (monitor_id, rule_id, status, reason, opened_at, updated_at)"
        " VALUES (?, 1, 'open', 'because', '2026-10-16T12:00:00.000+00:00', '2026-10-16T12:00:00.000+00:00')",
        (monitor["id"],),
    )
    listed = client.get(f"/api/monitors/{monitor['id']}/incidents").json()
    assert len(listed) == 1 and listed[0]["monitor_name"] == "payments"


# -- summary & health -------------------------------------------------------
def test_summary_shape(client, db: Database):
    healthy = create(client, name="healthy")
    failing = create(client, name="failing", url="https://broken.test/health")
    for index in range(4):
        insert_check(db, healthy["id"], id=index + 1, latency_ms=50.0)
    insert_check(db, failing["id"], id=10, ok=0, status_code=500, error="down")

    summary = client.get("/api/summary").json()
    assert summary["total_monitors"] == 2
    assert summary["checks_24h"] == 5 and summary["failures_24h"] == 1
    by_name = {m["name"]: m for m in summary["monitors"]}
    assert by_name["healthy"]["uptime_24h"] == 1.0
    assert by_name["healthy"]["avg_latency_24h"] == 50.0
    assert by_name["failing"]["uptime_24h"] == 0.0


def test_health_endpoint(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["scheduler_running"] is False  # scheduler disabled in tests


def test_unknown_api_path_is_json_404(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


def test_delete_monitor_cascades(client, db: Database):
    monitor = create(client)
    insert_rule(db, id=1, monitor_id=monitor["id"], type="failure_streak", threshold=1)
    insert_check(db, monitor["id"], id=1)
    client.delete(f"/api/monitors/{monitor['id']}")
    assert db.query_one("SELECT COUNT(*) AS n FROM checks")["n"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM alert_rules")["n"] == 0


# -- anomaly detection ------------------------------------------------------
def test_anomalies_insufficient_data(client):
    monitor = create(client)
    # only 3 checks: below MIN_POINTS, the endpoint must degrade gracefully
    for i in range(3):
        client.post(f"/api/monitors/{monitor['id']}/check")
    response = client.get(f"/api/monitors/{monitor['id']}/anomalies")
    assert response.status_code == 200
    body = response.json()
    assert body["sufficient_data"] is False
    assert body["anomalies"] == []
    assert body["points_analyzed"] < 30


def test_anomalies_flags_spike(client):
    from tests.factories import BASE_TIME, insert_check

    monitor = create(client)
    mid = monitor["id"]
    from datetime import timedelta

    # 100 flat checks + one massive spike + 40 flat tail: detector must flag the spike
    offsets = list(range(0, 100)) + [100] + list(range(101, 141))
    spike_positions = {100}
    for pos, _ in enumerate(offsets):
        latency = 900.0 if pos in spike_positions else 40.0
        insert_check(
            client.app.state.db,
            mid,
            id=None,
            checked_at=(BASE_TIME + timedelta(seconds=pos * 30)).isoformat(),
            latency_ms=latency,
        )
    response = client.get(f"/api/monitors/{mid}/anomalies?hours=2160")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sufficient_data"] is True
    assert body["points_analyzed"] == 141
    flagged_values = [a["value_ms"] for a in body["anomalies"]]
    assert 900.0 in flagged_values, f"spike must be flagged, got {body['anomalies']}"
    assert len(body["anomalies"]) <= 5, "contamination budget must cap the output"


def test_anomalies_unknown_monitor_404(client):
    assert client.get("/api/monitors/999/anomalies").status_code == 404


def test_anomalies_rejects_bad_params(client):
    monitor = create(client)
    assert client.get(f"/api/monitors/{monitor['id']}/anomalies?hours=0").status_code == 422
    assert client.get(f"/api/monitors/{monitor['id']}/anomalies?contamination=2").status_code == 422
