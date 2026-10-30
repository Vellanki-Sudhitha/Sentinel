"""Shared fixtures: temp database, app with a mocked HTTP transport, helpers."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app
from tests.factories import default_settings


class Recorder:
    """Records webhook posts so tests can assert on alert delivery."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if request.url.host in {"webhook.test", "hooks.test"}:
            # Only alert deliveries are recorded; health probes (GET) just pass.
            if request.method == "POST" and request.content:
                self.payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    @property
    def events(self) -> list[str]:
        return [payload["event"] for payload in self.payloads]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return default_settings(tmp_path)


@pytest.fixture
def db(settings) -> Database:
    database = Database(settings.database_path)
    yield database
    database.close()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def app(settings, db, recorder):
    application = create_app(settings, db=db, transport=httpx.MockTransport(recorder.handler))
    return application


@pytest.fixture
def client(app):
    # Context manager runs the lifespan (scheduler start/stop) around the test.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_client(tmp_path):
    """Build an isolated app+client pair with a custom transport handler."""
    created: list[tuple[Database, object]] = []

    def factory(handler, **settings_overrides) -> TestClient:
        runtime_settings = default_settings(tmp_path, **settings_overrides)
        database = Database(runtime_settings.database_path)
        transport = httpx.MockTransport(handler)
        application = create_app(runtime_settings, db=database, transport=transport)
        test_client = TestClient(application)
        test_client.__enter__()
        created.append((database, test_client))
        return test_client

    yield factory
    for database, test_client in created:
        test_client.__exit__(None, None, None)
        database.close()
