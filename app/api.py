"""REST API: monitors, historical metrics, alert rules and incidents."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.anomaly import CONTAMINATION, MIN_POINTS, fit_scores, flags_for
from app.db import utcnow
from app.incidents import acknowledge_incident, resolve_incident
from app.rules import RULE_TYPES
from app.schemas import (
    AnomaliesOut,
    AnomalyPoint,
    CheckOut,
    IncidentOut,
    MetricBucket,
    MetricsOut,
    MonitorCreate,
    MonitorOut,
    MonitorUpdate,
    RuleCreate,
    RuleOut,
    RuleUpdate,
    SummaryMonitor,
    SummaryOut,
)

router = APIRouter(prefix="/api")


def _db(request: Request):
    return request.app.state.db


def _scheduler(request: Request):
    return request.app.state.scheduler


def _notifier(request: Request):
    return request.app.state.notifier


def _monitor_out(row: dict) -> dict:
    row = dict(row)
    try:
        row["headers"] = json.loads(row.get("headers") or "{}")
    except (TypeError, ValueError):
        row["headers"] = {}
    row["enabled"] = bool(row["enabled"])
    row["last_ok"] = None if row.get("last_ok") is None else bool(row["last_ok"])
    return row


def _rule_out(row: dict) -> dict:
    row = dict(row)
    try:
        row["email_to"] = json.loads(row.get("email_to") or "[]")
    except (TypeError, ValueError):
        row["email_to"] = []
    row["enabled"] = bool(row["enabled"])
    return row


def _check_out(row: dict) -> dict:
    row = dict(row)
    row["ok"] = bool(row["ok"])
    return row


def _require_monitor(request: Request, monitor_id: int) -> dict:
    row = _db(request).query_one("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"monitor {monitor_id} not found")
    return dict(row)


# -- health -----------------------------------------------------------------
@router.get("/health")
def health(request: Request) -> dict:
    database = _db(request)
    scheduler = _scheduler(request)
    database.query_one("SELECT 1")
    return {
        "status": "ok",
        "database": database.path,
        "scheduler_running": scheduler.running,
        "checks_run": scheduler.checks_run,
        "last_tick_at": scheduler.last_tick_at,
        "time": utcnow(),
    }


# -- monitors ---------------------------------------------------------------
@router.get("/monitors", response_model=list[MonitorOut])
def list_monitors(request: Request) -> list[dict]:
    return [_monitor_out(dict(r)) for r in _db(request).query("SELECT * FROM monitors ORDER BY id")]


@router.post("/monitors", response_model=MonitorOut, status_code=201)
def create_monitor(request: Request, payload: MonitorCreate) -> dict:
    database = _db(request)
    now = utcnow()
    cursor = database.execute(
        """
        INSERT INTO monitors (name, url, method, headers, interval_seconds, timeout_seconds,
            expected_status_min, expected_status_max, unhealthy_threshold, healthy_threshold,
            enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.url,
            payload.method,
            json.dumps(payload.headers),
            payload.interval_seconds,
            payload.timeout_seconds,
            payload.expected_status_min,
            payload.expected_status_max,
            payload.unhealthy_threshold,
            payload.healthy_threshold,
            int(payload.enabled),
            now,
            now,
        ),
    )
    monitor = _require_monitor(request, cursor.lastrowid)
    # Arm the new monitor so its first check lands on the next scheduler tick.
    _scheduler(request).schedule_soon(monitor)
    return _monitor_out(monitor)


@router.get("/monitors/{monitor_id}", response_model=MonitorOut)
def get_monitor(request: Request, monitor_id: int) -> dict:
    return _monitor_out(_require_monitor(request, monitor_id))


@router.put("/monitors/{monitor_id}", response_model=MonitorOut)
def update_monitor(request: Request, monitor_id: int, payload: MonitorUpdate) -> dict:
    _require_monitor(request, monitor_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields supplied")
    if "headers" in fields:
        fields["headers"] = json.dumps(fields["headers"])
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    fields["updated_at"] = utcnow()

    assignments = ", ".join(f"{name} = ?" for name in fields)
    values = list(fields.values()) + [monitor_id]
    _db(request).execute(f"UPDATE monitors SET {assignments} WHERE id = ?", values)

    monitor = _require_monitor(request, monitor_id)
    if "enabled" in fields or "interval_seconds" in fields:
        scheduler = _scheduler(request)
        if monitor["enabled"]:
            scheduler.schedule_soon(monitor)  # changed schedule -> re-arm immediately
        else:
            scheduler.forget(monitor_id)
    return _monitor_out(monitor)


@router.delete("/monitors/{monitor_id}", status_code=204, response_class=Response)
def delete_monitor(request: Request, monitor_id: int) -> Response:
    _require_monitor(request, monitor_id)
    _db(request).execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
    _scheduler(request).forget(monitor_id)
    return Response(status_code=204)


@router.post("/monitors/{monitor_id}/check")
async def check_monitor(request: Request, monitor_id: int) -> dict:
    _require_monitor(request, monitor_id)
    result = await _scheduler(request).check_now(monitor_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"monitor {monitor_id} not found")
    return {
        "check": _check_out(result["check"]),
        "monitor": _monitor_out(result["monitor"]),
        "incidents": [
            {"kind": event.kind, "incident_id": event.incident["id"], "reason": event.incident["reason"]}
            for event in result["events"]
        ],
    }


# -- history & metrics ------------------------------------------------------
@router.get("/monitors/{monitor_id}/checks", response_model=list[CheckOut])
def list_checks(
    request: Request,
    monitor_id: int,
    limit: int = Query(default=100, ge=1, le=5000),
) -> list[dict]:
    _require_monitor(request, monitor_id)
    rows = _db(request).query(
        "SELECT * FROM checks WHERE monitor_id = ? ORDER BY id DESC LIMIT ?",
        (monitor_id, limit),
    )
    return [_check_out(dict(r)) for r in rows]


@router.get("/monitors/{monitor_id}/metrics", response_model=MetricsOut)
def monitor_metrics(
    request: Request,
    monitor_id: int,
    hours: float = Query(default=24.0, gt=0, le=24 * 90),
    buckets: int = Query(default=60, ge=1, le=500),
) -> dict:
    _require_monitor(request, monitor_id)
    now = datetime.now(UTC)
    start = now - timedelta(hours=hours)
    rows = _db(request).query(
        "SELECT checked_at, ok, latency_ms FROM checks"
        " WHERE monitor_id = ? AND checked_at >= ? ORDER BY checked_at ASC",
        (monitor_id, start.isoformat(timespec="milliseconds")),
    )
    checks = [dict(r) for r in rows]
    span = (now - start).total_seconds()
    bucket_seconds = max(1.0, span / buckets)
    bucket_map: dict[int, list[dict]] = {}

    for check in checks:
        when = datetime.fromisoformat(check["checked_at"])
        index = min(buckets - 1, max(0, int((when - start).total_seconds() // bucket_seconds)))
        bucket_map.setdefault(index, []).append(check)

    def stats(items: list[dict]) -> dict:
        total = len(items)
        failures = sum(1 for c in items if not c["ok"])
        latencies = sorted(c["latency_ms"] for c in items if c["latency_ms"] is not None)
        return {
            "total": total,
            "failures": failures,
            "uptime": round(1 - failures / total, 4) if total else 0.0,
            "avg": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "p95": round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 2)
            if latencies
            else None,
            "max": round(max(latencies), 2) if latencies else None,
        }

    overall = stats(checks)
    series: list[MetricBucket] = []
    for index in range(buckets):
        bucket_start = start + timedelta(seconds=index * bucket_seconds)
        bucket_end = bucket_start + timedelta(seconds=bucket_seconds)
        items = bucket_map.get(index, [])
        info = stats(items)
        series.append(
            MetricBucket(
                start=bucket_start.isoformat(timespec="seconds"),
                end=bucket_end.isoformat(timespec="seconds"),
                total=info["total"],
                failures=info["failures"],
                uptime=info["uptime"],
                avg_latency_ms=info["avg"],
                p95_latency_ms=info["p95"],
                max_latency_ms=info["max"],
            )
        )

    return MetricsOut(
        monitor_id=monitor_id,
        window_hours=hours,
        total=overall["total"],
        failures=overall["failures"],
        uptime=overall["uptime"],
        avg_latency_ms=overall["avg"],
        p95_latency_ms=overall["p95"],
        max_latency_ms=overall["max"],
        buckets=series,
    )


@router.get("/monitors/{monitor_id}/anomalies", response_model=AnomaliesOut)
def monitor_anomalies(
    request: Request,
    monitor_id: int,
    hours: float = Query(default=24.0, gt=0, le=24 * 90),
    contamination: float = Query(default=CONTAMINATION, gt=0, le=0.5),
) -> AnomaliesOut:
    """Run the Isolation Forest detector over the monitor's latency history.

    Unsupervised: scores come from the engineered feature matrix in
    ``app.anomaly``; fewer than MIN_POINTS samples returns an empty result
    instead of an error so the dashboard can render a graceful hint.
    """
    _require_monitor(request, monitor_id)
    start = datetime.now(UTC) - timedelta(hours=hours)
    rows = _db(request).query(
        "SELECT checked_at, latency_ms FROM checks"
        " WHERE monitor_id = ? AND checked_at >= ? AND latency_ms IS NOT NULL"
        " ORDER BY checked_at ASC",
        (monitor_id, start.isoformat(timespec="milliseconds")),
    )
    values = [float(r["latency_ms"]) for r in rows]
    if len(values) < MIN_POINTS:
        return AnomaliesOut(
            monitor_id=monitor_id,
            window_hours=hours,
            contamination=contamination,
            points_analyzed=len(values),
            sufficient_data=False,
            anomalies=[],
        )
    scores = fit_scores(values)
    mask = flags_for(scores, contamination)
    anomalies = [
        AnomalyPoint(
            index=i,
            timestamp=rows[i]["checked_at"],
            value_ms=values[i],
            score=round(float(scores[i]), 6),
        )
        for i in np.flatnonzero(mask)
    ]
    return AnomaliesOut(
        monitor_id=monitor_id,
        window_hours=hours,
        contamination=contamination,
        points_analyzed=len(values),
        sufficient_data=True,
        anomalies=anomalies,
    )


@router.get("/monitors/{monitor_id}/incidents", response_model=list[IncidentOut])
def monitor_incidents(request: Request, monitor_id: int) -> list[dict]:
    monitor = _require_monitor(request, monitor_id)
    rows = _db(request).query(
        "SELECT * FROM incidents WHERE monitor_id = ? ORDER BY id DESC LIMIT 200",
        (monitor_id,),
    )
    return [{**dict(r), "monitor_name": monitor["name"]} for r in rows]


# -- alert rules ------------------------------------------------------------
@router.get("/rules", response_model=list[RuleOut])
def list_rules(request: Request, monitor_id: int | None = None) -> list[dict]:
    if monitor_id is None:
        rows = _db(request).query("SELECT * FROM alert_rules ORDER BY id")
    else:
        rows = _db(request).query(
            "SELECT * FROM alert_rules WHERE monitor_id IS NULL OR monitor_id = ? ORDER BY id",
            (monitor_id,),
        )
    return [_rule_out(dict(r)) for r in rows]


@router.post("/rules", response_model=RuleOut, status_code=201)
def create_rule(request: Request, payload: RuleCreate) -> dict:
    database = _db(request)
    if payload.monitor_id is not None:
        _require_monitor(request, payload.monitor_id)
    now = utcnow()
    cursor = database.execute(
        """
        INSERT INTO alert_rules (name, monitor_id, type, threshold, window_seconds,
            duration_seconds, min_samples, webhook_url, email_to, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.monitor_id,
            payload.type,
            payload.threshold,
            payload.window_seconds,
            payload.duration_seconds,
            payload.min_samples,
            payload.webhook_url,
            json.dumps(payload.email_to),
            int(payload.enabled),
            now,
            now,
        ),
    )
    row = database.query_one("SELECT * FROM alert_rules WHERE id = ?", (cursor.lastrowid,))
    return _rule_out(dict(row))


@router.get("/rules/{rule_id}", response_model=RuleOut)
def get_rule(request: Request, rule_id: int) -> dict:
    row = _db(request).query_one("SELECT * FROM alert_rules WHERE id = ?", (rule_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"rule {rule_id} not found")
    return _rule_out(dict(row))


@router.put("/rules/{rule_id}", response_model=RuleOut)
def update_rule(request: Request, rule_id: int, payload: RuleUpdate) -> dict:
    database = _db(request)
    existing = database.query_one("SELECT * FROM alert_rules WHERE id = ?", (rule_id,))
    if existing is None:
        raise HTTPException(status_code=404, detail=f"rule {rule_id} not found")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields supplied")
    if "monitor_id" in fields and fields["monitor_id"] is not None:
        _require_monitor(request, fields["monitor_id"])
    if "email_to" in fields:
        fields["email_to"] = json.dumps(fields["email_to"])
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    fields["updated_at"] = utcnow()

    assignments = ", ".join(f"{name} = ?" for name in fields)
    values = list(fields.values()) + [rule_id]
    database.execute(f"UPDATE alert_rules SET {assignments} WHERE id = ?", values)
    return _rule_out(dict(database.query_one("SELECT * FROM alert_rules WHERE id = ?", (rule_id,))))


@router.delete("/rules/{rule_id}", status_code=204, response_class=Response)
def delete_rule(request: Request, rule_id: int) -> Response:
    cursor = _db(request).execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"rule {rule_id} not found")
    return Response(status_code=204)


# -- incidents --------------------------------------------------------------
@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(
    request: Request,
    status: str | None = Query(default=None, pattern="^(open|resolved)$"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    sql = "SELECT * FROM incidents"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = _db(request).query(sql, params)
    names = {r["id"]: r["name"] for r in _db(request).query("SELECT id, name FROM monitors")}
    return [
        {**dict(r), "monitor_name": names.get(r["monitor_id"])} for r in rows
    ]


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentOut)
def acknowledge(request: Request, incident_id: int) -> dict:
    row = acknowledge_incident(_db(request), incident_id)
    if row is None:
        raise HTTPException(
            status_code=404 if _db(request).query_one("SELECT id FROM incidents WHERE id = ?", (incident_id,)) is None else 409,
            detail="incident not found or already resolved",
        )
    return row


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentOut)
async def resolve(request: Request, incident_id: int) -> dict:
    event = resolve_incident(_db(request), incident_id)
    if event is None:
        exists = _db(request).query_one("SELECT id FROM incidents WHERE id = ?", (incident_id,))
        raise HTTPException(
            status_code=404 if exists is None else 409,
            detail="incident not found or already resolved",
        )
    await _notifier(request).notify(event, rules=[event.rule] if event.rule else [])
    return event.incident


# -- dashboard summary ------------------------------------------------------
@router.get("/summary", response_model=SummaryOut)
def summary(request: Request) -> dict:
    database = _db(request)
    monitors = [_monitor_out(dict(r)) for r in database.query("SELECT * FROM monitors ORDER BY id")]
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat(timespec="milliseconds")

    check_stats = {
        row["monitor_id"]: dict(row)
        for row in database.query(
            "SELECT monitor_id, COUNT(*) AS total,"
            " COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), 0) AS failures,"
            " AVG(latency_ms) AS avg_latency"
            " FROM checks WHERE checked_at >= ? GROUP BY monitor_id",
            (since,),
        )
    }
    incident_counts = {
        row["monitor_id"]: row["open_count"]
        for row in database.query(
            "SELECT monitor_id, COUNT(*) AS open_count FROM incidents"
            " WHERE status = 'open' GROUP BY monitor_id"
        )
    }
    totals = database.query_one(
        "SELECT COUNT(*) AS total,"
        " COALESCE(SUM(CASE WHEN checked_at >= ? AND ok = 0 THEN 1 ELSE 0 END), 0) AS failures,"
        " COALESCE(SUM(CASE WHEN checked_at >= ? THEN 1 ELSE 0 END), 0) AS checks"
        " FROM checks",
        (since, since),
    )

    entries = []
    for monitor in monitors:
        stats = check_stats.get(monitor["id"])
        entries.append(
            SummaryMonitor(
                id=monitor["id"],
                name=monitor["name"],
                url=monitor["url"],
                status=monitor["status"],
                enabled=monitor["enabled"],
                last_checked_at=monitor["last_checked_at"],
                last_latency_ms=monitor["last_latency_ms"],
                last_status_code=monitor["last_status_code"],
                last_error=monitor["last_error"],
                uptime_24h=round(1 - stats["failures"] / stats["total"], 4)
                if stats and stats["total"]
                else None,
                avg_latency_24h=round(stats["avg_latency"], 2) if stats and stats["avg_latency"] else None,
                open_incidents=incident_counts.get(monitor["id"], 0),
            )
        )

    enabled = [m for m in monitors if m["enabled"]]
    statuses = [m["status"] for m in enabled]
    return SummaryOut(
        total_monitors=len(monitors),
        up=statuses.count("up"),
        down=statuses.count("down"),
        unknown=statuses.count("unknown"),
        disabled=len(monitors) - len(enabled),
        open_incidents=sum(incident_counts.values()),
        checks_24h=totals["checks"] or 0,
        failures_24h=totals["failures"] or 0,
        monitors=entries,
    )


@router.get("/rule-types")
def rule_types() -> dict:
    return {"types": list(RULE_TYPES)}
