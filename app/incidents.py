"""Incident lifecycle: open on rule breach, resolve on confirmed recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.db import Database, utcnow
from app.rules import MAX_HISTORY, build_context, evaluate, rules_for_monitor


@dataclass
class IncidentEvent:
    """Something the outside world should be told about."""

    kind: str  # "opened" | "resolved"
    incident: dict
    monitor: dict
    rule: dict | None = None


def load_rules(db: Database) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM alert_rules ORDER BY id")]


def recent_checks(db: Database, monitor_id: int, limit: int = MAX_HISTORY) -> list[dict]:
    rows = db.query(
        "SELECT * FROM checks WHERE monitor_id = ? ORDER BY id DESC LIMIT ?",
        (monitor_id, limit),
    )
    return [dict(r) for r in rows]


def get_open_incident(db: Database, monitor_id: int) -> dict | None:
    row = db.query_one(
        "SELECT * FROM incidents WHERE monitor_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (monitor_id,),
    )
    return dict(row) if row else None


def sync_incidents(db: Database, monitor: dict, now: str | None = None) -> list[IncidentEvent]:
    """Reconcile stored incidents with the current rule evaluations.

    Returns the events that should be delivered to alert channels. Called after
    every recorded check, so it is idempotent: a still-firing rule on an
    already-open incident only refreshes the reason, it never notifies twice.
    """
    now = now or utcnow()
    rules = rules_for_monitor(load_rules(db), monitor)
    checks = recent_checks(db, monitor["id"])
    ctx = build_context(monitor, checks, now=_maybe_dt(now))

    fired = []
    for rule in rules:
        result = evaluate(rule, ctx)
        if result.fired:
            fired.append((rule, result.reason))

    events: list[IncidentEvent] = []
    current = get_open_incident(db, monitor["id"])

    if fired:
        rule, reason = fired[0]
        if current is None:
            cursor = db.execute(
                """
                INSERT INTO incidents (monitor_id, rule_id, status, reason, opened_at, updated_at)
                VALUES (?, ?, 'open', ?, ?, ?)
                """,
                (monitor["id"], rule["id"], reason, now, now),
            )
            incident = dict(db.query_one("SELECT * FROM incidents WHERE id = ?", (cursor.lastrowid,)))
            events.append(IncidentEvent("opened", incident, monitor, rule))
        else:
            # Already alerting: keep the incident's reason current, stay silent.
            detail = reason if current["rule_id"] == rule["id"] else f"{current['reason']} | {reason}"
            db.execute(
                "UPDATE incidents SET reason = ?, updated_at = ? WHERE id = ?",
                (detail, now, current["id"]),
            )
    elif current is not None:
        # Rule no longer firing: resolve once recovery is confirmed by
        # `healthy_threshold` consecutive successful checks.
        if monitor["consecutive_successes"] >= monitor["healthy_threshold"]:
            db.execute(
                "UPDATE incidents SET status = 'resolved', closed_at = ?, updated_at = ? WHERE id = ?",
                (now, now, current["id"]),
            )
            incident = dict(db.query_one("SELECT * FROM incidents WHERE id = ?", (current["id"],)))
            rule = None
            if current.get("rule_id"):
                row = db.query_one("SELECT * FROM alert_rules WHERE id = ?", (current["rule_id"],))
                rule = dict(row) if row else None
            events.append(IncidentEvent("resolved", incident, monitor, rule))

    return events


def resolve_incident(db: Database, incident_id: int, now: str | None = None) -> IncidentEvent | None:
    """Manually close an incident (operator-driven recovery)."""
    now = now or utcnow()
    row = db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    if row is None:
        return None
    incident = dict(row)
    if incident["status"] == "resolved":
        return None
    db.execute(
        "UPDATE incidents SET status = 'resolved', closed_at = ?, updated_at = ? WHERE id = ?",
        (now, now, incident_id),
    )
    incident = dict(db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,)))
    monitor = dict(db.query_one("SELECT * FROM monitors WHERE id = ?", (incident["monitor_id"],)))
    rule = None
    if incident.get("rule_id"):
        found = db.query_one("SELECT * FROM alert_rules WHERE id = ?", (incident["rule_id"],))
        rule = dict(found) if found else None
    return IncidentEvent("resolved", incident, monitor, rule)


def acknowledge_incident(db: Database, incident_id: int, now: str | None = None) -> dict | None:
    now = now or utcnow()
    row = db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    if row is None or row["status"] != "open":
        return None
    db.execute(
        "UPDATE incidents SET acknowledged_at = COALESCE(acknowledged_at, ?), updated_at = ? WHERE id = ?",
        (now, now, incident_id),
    )
    return dict(db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,)))


def _maybe_dt(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def incident_stats(db: Database) -> dict:
    row = db.query_one(
        "SELECT"
        " COALESCE(SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), 0) AS open_count,"
        " COUNT(*) AS total FROM incidents"
    )
    return {"open": row["open_count"], "total": row["total"]}
