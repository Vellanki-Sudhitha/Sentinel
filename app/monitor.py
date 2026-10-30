"""Executing a single health check and recording its result."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from app.db import Database, utcnow


@dataclass
class RawCheck:
    """Outcome of one probe, before counters/status are updated."""

    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None
    checked_at: str


async def probe(monitor: dict, client: httpx.AsyncClient) -> RawCheck:
    """Call the monitored URL and measure wall-clock latency."""
    checked_at = utcnow()
    started = time.perf_counter()
    headers = monitor.get("headers") or None
    if isinstance(headers, str):
        try:
            headers = json.loads(headers) or None
        except ValueError:
            headers = None

    def elapsed() -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    try:
        response = await client.request(
            monitor["method"],
            monitor["url"],
            headers=headers,
            timeout=monitor["timeout_seconds"],
            follow_redirects=True,
        )
    except httpx.TimeoutException:
        return RawCheck(False, None, elapsed(), "request timed out", checked_at)
    except httpx.HTTPError as exc:
        return RawCheck(False, None, elapsed(), f"{type(exc).__name__}: {exc}", checked_at)

    latency = elapsed()
    in_range = monitor["expected_status_min"] <= response.status_code <= monitor["expected_status_max"]
    if in_range:
        return RawCheck(True, response.status_code, latency, None, checked_at)
    return RawCheck(
        False,
        response.status_code,
        latency,
        f"unexpected status {response.status_code} (expected "
        f"{monitor['expected_status_min']}-{monitor['expected_status_max']})",
        checked_at,
    )


def record(db: Database, monitor: dict, raw: RawCheck) -> tuple[dict, dict]:
    """Persist a probe result and return (check_row, updated_monitor)."""
    with db.transaction():
        db.execute(
            "INSERT INTO checks (monitor_id, checked_at, ok, status_code, latency_ms, error)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (monitor["id"], raw.checked_at, int(raw.ok), raw.status_code, raw.latency_ms, raw.error),
        )
        if raw.ok:
            failures = 0
            successes = monitor["consecutive_successes"] + 1
            status = "up"  # a passing check proves the target is reachable
        else:
            failures = monitor["consecutive_failures"] + 1
            successes = 0
            # Keep the previous status until the unhealthy threshold is crossed,
            # so a single blip does not flap the dashboard.
            status = "down" if failures >= monitor["unhealthy_threshold"] else monitor["status"]
        db.execute(
            """
            UPDATE monitors SET
                status = ?, consecutive_failures = ?, consecutive_successes = ?,
                last_checked_at = ?, last_ok = ?, last_status_code = ?, last_latency_ms = ?,
                last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                failures,
                successes,
                raw.checked_at,
                int(raw.ok),
                raw.status_code,
                raw.latency_ms,
                raw.error,
                raw.checked_at,
                monitor["id"],
            ),
        )
        check_row = db.query_one(
            "SELECT * FROM checks WHERE monitor_id = ? ORDER BY id DESC LIMIT 1",
            (monitor["id"],),
        )
        updated = db.query_one("SELECT * FROM monitors WHERE id = ?", (monitor["id"],))

    check = dict(check_row)
    check["ok"] = bool(check["ok"])
    updated = dict(updated)
    updated["last_ok"] = None if updated["last_ok"] is None else bool(updated["last_ok"])
    return check, updated


async def run_check(
    db: Database, monitor: dict, client: httpx.AsyncClient
) -> tuple[dict, dict]:
    """Probe a monitor and persist the result: (check_row, updated_monitor)."""
    raw = await probe(monitor, client)
    return record(db, monitor, raw)
