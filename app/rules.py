"""Alert rule evaluation.

`evaluate` is a pure function: it takes a stored rule plus a snapshot of recent
check history and reports whether the rule is currently firing. All state that
an incident decision needs comes from `RuleContext`, so rules are easy to unit
test without a database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.anomaly import CONTAMINATION, MIN_POINTS, fit_scores, flags_for

# Kept in app.rules (not schemas) so every consumer — Pydantic validation, the
# /api/rule-types endpoint, the React form — reads one list.
RULE_TYPES = ("failure_streak", "latency", "error_rate", "anomaly")

# Cap on how many history rows a single evaluation inspects.
MAX_HISTORY = 1000


@dataclass
class RuleContext:
    monitor: dict
    checks: list[dict] = field(default_factory=list)  # newest first
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    now: datetime | None = None

    @property
    def last_check(self) -> dict | None:
        return self.checks[0] if self.checks else None


@dataclass
class Evaluation:
    fired: bool
    reason: str = ""


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def checks_needed_for_duration(rule: dict, monitor: dict) -> int:
    """How many consecutive violating checks satisfy a sustained-duration rule."""
    duration = int(rule.get("duration_seconds") or 0)
    if duration <= 0:
        return 1
    interval = max(1, int(monitor.get("interval_seconds") or 60))
    return max(1, math.ceil(duration / interval))


def evaluate(rule: dict, ctx: RuleContext) -> Evaluation:
    """Return whether `rule` fires given the recent check history."""
    if not rule.get("enabled", 1):
        return Evaluation(False)

    kind = rule["type"]

    if kind == "failure_streak":
        threshold = int(rule["threshold"])
        if ctx.consecutive_failures >= threshold:
            last = ctx.last_check or {}
            error = last.get("error") or f"status {last.get('status_code')}"
            return Evaluation(
                True,
                f"{ctx.consecutive_failures} consecutive failed checks "
                f"(threshold {threshold}); last error: {error}",
            )
        return Evaluation(False)

    if kind == "latency":
        needed = checks_needed_for_duration(rule, ctx.monitor)
        window = ctx.checks[:needed]
        if len(window) < needed:
            return Evaluation(False)
        threshold = float(rule["threshold"])
        violating = [c for c in window if c.get("latency_ms") is not None and c["latency_ms"] > threshold]
        if len(violating) == needed:
            worst = max(c["latency_ms"] for c in violating)
            suffix = (
                f" sustained over {needed} checks"
                if needed > 1
                else ""
            )
            return Evaluation(
                True,
                f"latency {worst:.0f}ms exceeds {threshold:.0f}ms{suffix}",
            )
        return Evaluation(False)

    if kind == "error_rate":
        now = ctx.now or datetime.now(UTC)
        window_seconds = int(rule.get("window_seconds") or 300)
        cutoff = now - timedelta(seconds=window_seconds)
        in_window = [c for c in ctx.checks if _parse(c["checked_at"]) >= cutoff]
        if len(in_window) < int(rule.get("min_samples") or 1):
            return Evaluation(False)
        failures = sum(1 for c in in_window if not c["ok"])
        ratio = failures / len(in_window)
        if ratio >= float(rule["threshold"]):
            return Evaluation(
                True,
                f"error rate {ratio:.0%} over the last {window_seconds}s "
                f"({failures}/{len(in_window)} checks, threshold {float(rule['threshold']):.0%})",
            )
        return Evaluation(False)

    if kind == "anomaly":
        now = ctx.now or datetime.now(UTC)
        window_seconds = int(rule.get("window_seconds") or 300)
        cutoff = now - timedelta(seconds=window_seconds)
        # Chronological and latency-bearing only: the detector needs time order
        # to build its trailing features, and a timeout records no latency.
        samples = [
            c
            for c in reversed(ctx.checks)
            if c.get("latency_ms") is not None and _parse(c["checked_at"]) >= cutoff
        ]
        # min_samples is the operator's ask, MIN_POINTS the detector's floor.
        needed = max(int(rule.get("min_samples") or 1), MIN_POINTS)
        if len(samples) < needed:
            return Evaluation(False)

        contamination = float(rule.get("threshold") or CONTAMINATION)
        values = [float(c["latency_ms"]) for c in samples]
        scores = fit_scores(values)
        mask = flags_for(scores, contamination)
        # Only the sample just recorded can open an incident; an anomaly that is
        # still in the window but no longer current must not re-fire.
        if not mask[-1]:
            return Evaluation(False)
        flagged = int(mask.sum())
        return Evaluation(
            True,
            f"Isolation Forest flagged the latest check at {values[-1]:.0f}ms "
            f"(score {scores[-1]:.3f}); {flagged}/{len(values)} samples flagged "
            f"at contamination {contamination:g}",
        )

    return Evaluation(False)


def rules_for_monitor(rules: list[dict], monitor: dict) -> list[dict]:
    """Global rules (monitor_id NULL) plus the monitor's own rules."""
    return [r for r in rules if r.get("monitor_id") in (None, monitor["id"])]


def build_context(
    monitor: dict, checks: list[dict], now: datetime | None = None
) -> RuleContext:
    return RuleContext(
        monitor=monitor,
        checks=checks,
        consecutive_failures=monitor.get("consecutive_failures", 0),
        consecutive_successes=monitor.get("consecutive_successes", 0),
        now=now,
    )
