"""Probe execution and result recording, including a real HTTP round trip."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.db import Database
from app.monitor import RawCheck, probe, record, run_check
from tests.factories import default_settings, insert_monitor, monitor_row


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_probe_success_measures_latency():
    monitor = monitor_row(headers='{"Authorization": "Bearer x"}')

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer x"
        return httpx.Response(204)

    async with client_for(handler) as client:
        result = await probe(monitor, client)

    assert result.ok is True
    assert result.status_code == 204
    assert result.error is None
    assert result.latency_ms is not None and result.latency_ms >= 0


async def test_probe_flags_unexpected_status():
    monitor = monitor_row(expected_status_min=200, expected_status_max=299)
    async with client_for(lambda request: httpx.Response(503)) as client:
        result = await probe(monitor, client)

    assert result.ok is False
    assert result.status_code == 503
    assert "unexpected status 503" in result.error


async def test_probe_timeout_is_a_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("deadline exceeded", request=request)

    async with client_for(handler) as client:
        result = await probe(monitor_row(), client)

    assert result.ok is False
    assert result.status_code is None
    assert "timed out" in result.error


async def test_probe_connection_error_is_a_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with client_for(handler) as client:
        result = await probe(monitor_row(), client)

    assert result.ok is False
    assert "ConnectError" in result.error


def test_record_transitions(tmp_path):
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, unhealthy_threshold=3, healthy_threshold=2)
    failure = RawCheck(False, 500, 10.0, "unexpected status 500", "2026-10-16T12:00:00.000+00:00")

    # Below the unhealthy threshold the status must not flap to "down".
    for _ in range(2):
        _, monitor = record(db, monitor, failure)
    assert monitor["consecutive_failures"] == 2
    assert monitor["status"] == "unknown"

    _, monitor = record(db, monitor, RawCheck(False, 500, 10.0, "unexpected status 500", "2026-10-16T12:01:00.000+00:00"))
    assert monitor["status"] == "down"
    assert monitor["consecutive_failures"] == 3

    _, monitor = record(db, monitor, RawCheck(True, 200, 12.5, None, "2026-10-16T12:02:00.000+00:00"))
    assert monitor["status"] == "up"
    assert monitor["consecutive_failures"] == 0
    assert monitor["consecutive_successes"] == 1
    assert monitor["last_latency_ms"] == 12.5

    stored = db.query_one("SELECT COUNT(*) AS n FROM checks")
    assert stored["n"] == 4
    db.close()


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal real HTTP endpoint: 200 by default, 500 when `.broken` is set."""

    broken = False
    delay_ms = 0

    def do_GET(self):  # noqa: N802 (stdlib API)
        import time

        if self.delay_ms:
            time.sleep(self.delay_ms / 1000)
        status = 500 if self.broken else 200
        body = b"ok" if status == 200 else b"boom"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def live_server():
    _HealthHandler.broken = False
    _HealthHandler.delay_ms = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/health"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


async def test_run_check_against_a_real_http_server(tmp_path, live_server):
    """End-to-end probe: real socket, real latency measurement, real persistence."""
    db = Database(default_settings(tmp_path).database_path)
    monitor = insert_monitor(db, url=live_server, expected_status_min=200, expected_status_max=299)

    async with httpx.AsyncClient() as client:
        check, updated = await run_check(db, monitor, client)
        assert check["ok"] is True
        assert check["status_code"] == 200
        assert check["latency_ms"] > 0
        assert updated["status"] == "up"

        _HealthHandler.broken = True
        check, updated = await run_check(db, monitor, client)
        assert check["ok"] is False
        assert "unexpected status 500" in check["error"]

    assert db.query_one("SELECT COUNT(*) AS n FROM checks")["n"] == 2
    db.close()
