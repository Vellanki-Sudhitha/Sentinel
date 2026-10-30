"""Environment driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _list(name: str) -> list[str]:
    raw = os.getenv(name) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    """Runtime configuration, sourced from environment variables."""

    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/sentinel.db"))
    scheduler_enabled: bool = field(default_factory=lambda: _bool("SCHEDULER_ENABLED", True))
    check_concurrency: int = field(default_factory=lambda: _int("CHECK_CONCURRENCY", 10))
    metric_retention_days: int = field(default_factory=lambda: _int("METRIC_RETENTION_DAYS", 30))
    maintenance_interval_seconds: int = field(default_factory=lambda: _int("MAINTENANCE_INTERVAL", 3600))
    http_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("HTTP_TIMEOUT_SECONDS", "15") or 15)
    )

    # Alerting: generic webhook (Slack/Discord/PagerDuty style sinks) ------------
    default_webhook_url: str = field(default_factory=lambda: os.getenv("DEFAULT_WEBHOOK_URL", ""))
    webhook_retries: int = field(default_factory=lambda: _int("WEBHOOK_RETRIES", 3))
    webhook_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "10") or 10)
    )

    # Alerting: SMTP email -------------------------------------------------------
    email_host: str = field(default_factory=lambda: os.getenv("ALERT_EMAIL_HOST", ""))
    email_port: int = field(default_factory=lambda: _int("ALERT_EMAIL_PORT", 587))
    email_user: str = field(default_factory=lambda: os.getenv("ALERT_EMAIL_USER", ""))
    email_password: str = field(default_factory=lambda: os.getenv("ALERT_EMAIL_PASSWORD", ""))
    email_use_tls: bool = field(default_factory=lambda: _bool("ALERT_EMAIL_USE_TLS", True))
    email_from: str = field(default_factory=lambda: os.getenv("ALERT_EMAIL_FROM", "sentinel@localhost"))
    default_alert_emails: list[str] = field(default_factory=lambda: _list("DEFAULT_ALERT_EMAILS"))

    notify_on_recovery: bool = field(default_factory=lambda: _bool("NOTIFY_ON_RECOVERY", True))

    @property
    def smtp_configured(self) -> bool:
        return bool(self.email_host)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.default_webhook_url)


def load_settings() -> Settings:
    return Settings()
