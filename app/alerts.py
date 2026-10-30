"""Alert delivery: generic JSON webhook plus SMTP email."""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from app.config import Settings
from app.incidents import IncidentEvent

log = logging.getLogger("sentinel.alerts")

EVENT_NAMES = {"opened": "incident.opened", "resolved": "incident.resolved"}
SEVERITIES = {"opened": "critical", "resolved": "resolved"}


@dataclass
class Delivery:
    channel: str  # "webhook" | "email"
    target: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"channel": self.channel, "target": self.target, "ok": self.ok, "detail": self.detail}


def build_payload(event: IncidentEvent) -> dict:
    monitor = event.monitor
    incident = event.incident
    return {
        "source": "sentinel",
        "event": EVENT_NAMES.get(event.kind, event.kind),
        "severity": SEVERITIES.get(event.kind, "info"),
        "monitor": {
            "id": monitor["id"],
            "name": monitor["name"],
            "url": monitor["url"],
            "status": monitor.get("status"),
            "last_error": monitor.get("last_error"),
            "last_latency_ms": monitor.get("last_latency_ms"),
        },
        "incident": {
            "id": incident["id"],
            "status": incident["status"],
            "reason": incident["reason"],
            "opened_at": incident["opened_at"],
            "closed_at": incident.get("closed_at"),
        },
        "occurred_at": incident.get("closed_at") or incident["opened_at"],
    }


def build_email(event: IncidentEvent, settings: Settings) -> tuple[str, str]:
    monitor = event.monitor
    incident = event.incident
    word = "DOWN" if event.kind == "opened" else "RECOVERED"
    subject = f"[SENTINEL][{word}] {monitor['name']}"
    lines = [
        f"Service {word}: {monitor['name']}",
        f"URL: {monitor['url']}",
        f"Current status: {monitor.get('status')}",
        f"Reason: {incident['reason']}",
        f"Incident #{incident['id']} opened at {incident['opened_at']}",
    ]
    if incident.get("closed_at"):
        lines.append(f"Resolved at {incident['closed_at']}")
    if monitor.get("last_error"):
        lines.append(f"Last error: {monitor['last_error']}")
    return subject, "\n".join(lines)


def send_email_sync(settings: Settings, subject: str, body: str, recipients: list[str]) -> None:
    """Blocking SMTP send. Kept separate so tests can substitute it."""
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.email_host, settings.email_port, timeout=15) as server:
        if settings.email_use_tls:
            server.starttls()
        if settings.email_user:
            server.login(settings.email_user, settings.email_password)
        server.send_message(message)


def webhook_targets(settings: Settings, rules: list[dict | None]) -> list[str]:
    targets: list[str] = []
    if settings.default_webhook_url:
        targets.append(settings.default_webhook_url)
    for rule in rules:
        url = (rule or {}).get("webhook_url") or ""
        if url and url not in targets:
            targets.append(url)
    return targets


def _rule_emails(rule: dict | None) -> list[str]:
    """Recipients from a rule, tolerating a raw DB row (email_to as JSON text)."""
    value = (rule or {}).get("email_to") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    if isinstance(value, str):  # single bare address stored as text
        value = [value]
    return [addr for addr in value if addr]


def email_targets(settings: Settings, rules: list[dict | None]) -> list[str]:
    targets: list[str] = list(settings.default_alert_emails)
    for rule in rules:
        for addr in _rule_emails(rule):
            if addr not in targets:
                targets.append(addr)
    return targets


class Notifier:
    """Sends alert events to every channel configured on the firing rule."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        email_sender=None,
        retry_backoff: float = 0.5,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient()
        self._email_sender = email_sender or send_email_sync
        self._backoff = retry_backoff

    async def notify(self, event: IncidentEvent, rules: list[dict | None] | None = None) -> list[Delivery]:
        rules = rules or ([event.rule] if event.rule else [])
        if event.kind == "resolved" and not self.settings.notify_on_recovery:
            return []
        deliveries: list[Delivery] = []
        deliveries.extend(await self._send_webhooks(event, webhook_targets(self.settings, rules)))
        deliveries.extend(await self._send_emails(event, email_targets(self.settings, rules)))
        for delivery in deliveries:
            level = log.info if delivery.ok else log.warning
            level("alert %s -> %s [%s] ok=%s %s", event.kind, delivery.channel, delivery.target,
                  delivery.ok, delivery.detail)
        return deliveries

    async def _send_webhooks(self, event: IncidentEvent, targets: list[str]) -> list[Delivery]:
        if not targets:
            return []
        payload = build_payload(event)
        results: list[Delivery] = []
        for target in targets:
            ok, detail = await self._post_with_retry(target, payload)
            results.append(Delivery("webhook", target, ok, detail))
        return results

    async def _post_with_retry(self, url: str, payload: dict) -> tuple[bool, str]:
        attempts = max(1, self.settings.webhook_retries)
        last_detail = ""
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post(
                    url, json=payload, timeout=self.settings.webhook_timeout_seconds
                )
                if response.status_code < 300:
                    return True, f"HTTP {response.status_code} (attempt {attempt})"
                last_detail = f"HTTP {response.status_code} (attempt {attempt})"
            except httpx.HTTPError as exc:
                last_detail = f"{type(exc).__name__}: {exc} (attempt {attempt})"
            if attempt < attempts and self._backoff:
                await asyncio.sleep(self._backoff * attempt)
        return False, last_detail

    async def _send_emails(self, event: IncidentEvent, recipients: list[str]) -> list[Delivery]:
        if not recipients:
            return []
        if not self.settings.smtp_configured:
            return [
                Delivery("email", ", ".join(recipients), False, "SMTP is not configured (ALERT_EMAIL_HOST)")
            ]
        subject, body = build_email(event, self.settings)
        try:
            await asyncio.to_thread(self._email_sender, self.settings, subject, body, recipients)
        except Exception as exc:  # delivery must never break the check pipeline
            return [Delivery("email", ", ".join(recipients), False, f"{type(exc).__name__}: {exc}")]
        return [Delivery("email", ", ".join(recipients), True, "sent")]

    async def aclose(self) -> None:
        await self._client.aclose()
