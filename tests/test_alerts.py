"""Webhook and email delivery: payloads, retries, channel resolution."""

from __future__ import annotations

import httpx

from app.alerts import Notifier, build_email, build_payload, email_targets
from app.incidents import IncidentEvent
from tests.factories import default_settings, monitor_row


def make_event(kind: str = "opened") -> IncidentEvent:
    monitor = monitor_row(status="down", last_error="unexpected status 500")
    incident = {
        "id": 42,
        "monitor_id": 1,
        "rule_id": 1,
        "status": "resolved" if kind == "resolved" else "open",
        "reason": "3 consecutive failed checks",
        "opened_at": "2026-10-16T12:00:00.000+00:00",
        "updated_at": "2026-10-16T12:05:00.000+00:00",
        "closed_at": "2026-10-16T12:05:00.000+00:00" if kind == "resolved" else None,
        "acknowledged_at": None,
    }
    rule = {
        "id": 1,
        "name": "failures",
        "monitor_id": 1,
        "type": "failure_streak",
        "threshold": 3,
        "window_seconds": 300,
        "duration_seconds": 0,
        "min_samples": 5,
        "webhook_url": "https://webhook.test/rule",
        "email_to": '["oncall@example.com"]',
        "enabled": 1,
        "created_at": "2026-10-16T00:00:00.000+00:00",
        "updated_at": "2026-10-16T00:00:00.000+00:00",
    }
    return IncidentEvent(kind=kind, incident=incident, monitor=monitor, rule=rule)


def test_payload_shape_for_opened_and_resolved():
    opened = build_payload(make_event("opened"))
    assert opened["event"] == "incident.opened"
    assert opened["severity"] == "critical"
    assert opened["monitor"]["name"] == "payments-api"
    assert opened["incident"]["reason"] == "3 consecutive failed checks"
    assert opened["occurred_at"] == "2026-10-16T12:00:00.000+00:00"

    resolved = build_payload(make_event("resolved"))
    assert resolved["event"] == "incident.resolved"
    assert resolved["severity"] == "resolved"
    assert resolved["occurred_at"] == "2026-10-16T12:05:00.000+00:00"


def test_email_content():
    subject, body = build_email(make_event("opened"), default_settings(_tmp()))
    assert "[SENTINEL][DOWN]" in subject
    assert "payments-api" in subject
    assert "3 consecutive failed checks" in body
    assert "unexpected status 500" in body


def _tmp():
    import pathlib
    import tempfile

    return pathlib.Path(tempfile.mkdtemp())


async def test_webhooks_go_to_default_and_rule_targets_without_duplicates():
    settings = default_settings(_tmp(), default_webhook_url="https://webhook.test/default")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    notifier = Notifier(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0
    )
    rule = {"webhook_url": "https://webhook.test/default", "email_to": []}
    deliveries = await notifier.notify(make_event("opened"), rules=[rule])

    assert seen == ["https://webhook.test/default"]  # deduped
    assert len(deliveries) == 1 and deliveries[0].ok is True


async def test_webhook_retries_then_succeeds():
    settings = default_settings(_tmp(), webhook_retries=3)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500 if len(attempts) < 3 else 200)

    notifier = Notifier(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0
    )
    event = make_event("opened")
    event.rule = {"webhook_url": "https://webhook.test/x", "email_to": []}
    deliveries = await notifier.notify(event, rules=[event.rule])

    assert len(attempts) == 3
    assert deliveries[0].ok is True and "attempt 3" in deliveries[0].detail


async def test_webhook_gives_up_after_max_retries():
    settings = default_settings(_tmp(), webhook_retries=2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    notifier = Notifier(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0
    )
    event = make_event("opened")
    event.rule = {"webhook_url": "https://webhook.test/x", "email_to": []}
    deliveries = await notifier.notify(event, rules=[event.rule])

    assert deliveries[0].ok is False
    assert "HTTP 503" in deliveries[0].detail


async def test_email_is_sent_through_injected_sender():
    settings = default_settings(_tmp(), email_host="smtp.example.com")
    sent: list[dict] = []

    def fake_sender(config, subject, body, recipients):
        sent.append({"subject": subject, "body": body, "recipients": recipients})

    notifier = Notifier(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        email_sender=fake_sender,
        retry_backoff=0,
    )
    event = make_event("opened")
    event.rule = {"webhook_url": "", "email_to": ["oncall@example.com"]}
    deliveries = await notifier.notify(event, rules=[event.rule])

    assert sent and sent[0]["recipients"] == ["oncall@example.com"]
    assert any(d.channel == "email" and d.ok for d in deliveries)


async def test_email_without_smtp_config_reports_failure():
    settings = default_settings(_tmp(), email_host="")
    notifier = Notifier(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        retry_backoff=0,
    )
    event = make_event("opened")
    event.rule = {"webhook_url": "", "email_to": ["oncall@example.com"]}
    deliveries = await notifier.notify(event, rules=[event.rule])

    assert len(deliveries) == 1
    assert deliveries[0].channel == "email" and deliveries[0].ok is False
    assert "SMTP is not configured" in deliveries[0].detail


async def test_recovery_notifications_can_be_disabled():
    settings = default_settings(_tmp(), notify_on_recovery=False)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    notifier = Notifier(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0
    )
    event = make_event("resolved")
    event.rule = {"webhook_url": "https://webhook.test/x", "email_to": []}
    deliveries = await notifier.notify(event, rules=[event.rule])

    assert deliveries == [] and calls == []


async def test_delivery_failure_never_raises():
    settings = default_settings(_tmp(), webhook_retries=1)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    notifier = Notifier(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0
    )
    event = make_event("opened")
    event.rule = {"webhook_url": "https://webhook.test/x", "email_to": []}
    deliveries = await notifier.notify(event, rules=[event.rule])

    assert deliveries[0].ok is False and "ConnectError" in deliveries[0].detail


def test_email_targets_parse_raw_db_json():
    """Rules straight from the DB carry email_to as JSON text, not a list."""
    settings = default_settings(_tmp())

    # An empty "[]" must not be iterated into junk recipients '[' and ']'.
    assert email_targets(settings, [{"email_to": "[]"}]) == []

    # A populated JSON list text must resolve to real addresses.
    assert email_targets(settings, [{"email_to": '["oncall@example.com"]'}]) == ["oncall@example.com"]

    # Defaults come first, duplicates (even from the raw form) are dropped.
    settings.default_alert_emails = ["ops@example.com"]
    assert email_targets(settings, [{"email_to": '["ops@example.com", "a@example.com"]'}]) == [
        "ops@example.com",
        "a@example.com",
    ]
