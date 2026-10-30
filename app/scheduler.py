"""Background jobs: scheduled health checks plus periodic metric pruning."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx

from app.config import Settings
from app.db import Database, utcnow
from app.incidents import sync_incidents
from app.monitor import run_check

log = logging.getLogger("sentinel.scheduler")


class Scheduler:
    """Interval-based scheduler that runs checks and drives incident state.

    Scheduling is fixed-rate: a monitor's next run is booked when the check is
    dispatched, so a slow probe never stacks up concurrent runs for the same
    target. All state lives in memory and is rebuilt from the database on start.
    """

    def __init__(
        self,
        db: Database,
        settings: Settings,
        notifier,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.notifier = notifier
        self.poll_seconds = 1.0
        self._client = httpx.AsyncClient(transport=transport)
        self._semaphore = asyncio.Semaphore(max(1, settings.check_concurrency))
        self._next_due: dict[int, float] = {}
        self._tasks: list[asyncio.Task] = []
        self.running = False
        self.checks_run = 0
        self.last_tick_at: str | None = None

    # -- scheduling ---------------------------------------------------------
    def load_monitors(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM monitors ORDER BY id")]

    def reschedule(self, monitor: dict, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if monitor.get("enabled", 1):
            self._next_due[monitor["id"]] = now + int(monitor["interval_seconds"])
        else:
            self._next_due.pop(monitor["id"], None)

    def schedule_soon(self, monitor: dict, now: float | None = None) -> None:
        """Arm a monitor to run on the next tick (new or just-edited target)."""
        now = time.time() if now is None else now
        if monitor.get("enabled", 1):
            self._next_due[monitor["id"]] = now
        else:
            self._next_due.pop(monitor["id"], None)

    def forget(self, monitor_id: int) -> None:
        self._next_due.pop(monitor_id, None)

    def due_monitors(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        due = []
        for monitor in self.load_monitors():
            if not monitor.get("enabled", 1):
                continue
            if self._next_due.get(monitor["id"], 0) <= now:
                due.append(monitor)
        return due

    # -- execution ----------------------------------------------------------
    async def run_monitor(self, monitor: dict) -> dict:
        async with self._semaphore:
            check, updated = await run_check(self.db, monitor, self._client)
        self.checks_run += 1

        events = sync_incidents(self.db, updated)
        for event in events:
            try:
                await self.notifier.notify(event)
            except Exception:  # delivery failures must not kill the scheduler
                log.exception("alert delivery failed for incident %s", event.incident["id"])
        return {"monitor": updated, "check": check, "events": events}

    async def tick(self, now: float | None = None) -> list[dict]:
        """Run every due monitor once. Returns one result per monitor checked."""
        now = time.time() if now is None else now
        due = self.due_monitors(now)
        for monitor in due:
            self.reschedule(monitor, now)
        self.last_tick_at = utcnow()
        if not due:
            return []
        return list(await asyncio.gather(*(self.run_monitor(monitor) for monitor in due)))

    async def check_now(self, monitor_id: int) -> dict | None:
        """Operator-triggered immediate check (also re-arms the schedule)."""
        row = self.db.query_one("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        if row is None:
            return None
        monitor = dict(row)
        result = await self.run_monitor(monitor)
        if monitor.get("enabled", 1):
            self.reschedule(monitor)
        return result

    # -- background loops ---------------------------------------------------
    async def start(self) -> None:
        if self.running or not self.settings.scheduler_enabled:
            return
        self.running = True
        now = time.time()
        for monitor in self.load_monitors():
            if monitor.get("enabled", 1):
                # First check runs promptly after boot so status fills in fast.
                self._next_due[monitor["id"]] = now
        self._tasks = [
            asyncio.create_task(self._check_loop(), name="sentinel-checks"),
            asyncio.create_task(self._maintenance_loop(), name="sentinel-maintenance"),
        ]
        log.info("scheduler started (concurrency=%s)", self.settings.check_concurrency)

    async def stop(self) -> None:
        self.running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        await self._client.aclose()

    async def _check_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.poll_seconds)

    async def _maintenance_loop(self) -> None:
        interval = max(30, self.settings.maintenance_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            try:
                pruned = await asyncio.to_thread(self.prune_metrics)
                if pruned:
                    log.info("pruned %s expired metric rows", pruned)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("metric pruning failed")

    # -- jobs ---------------------------------------------------------------
    def prune_metrics(self, now: datetime | None = None) -> int:
        """Drop check history older than the retention window."""
        now = now or datetime.now(UTC)
        cutoff = (now - timedelta(days=self.settings.metric_retention_days)).isoformat(
            timespec="milliseconds"
        )
        cursor = self.db.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))
        return cursor.rowcount or 0
