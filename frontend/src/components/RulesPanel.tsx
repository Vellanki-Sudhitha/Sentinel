import { useState } from "react";
import { api } from "../api";
import type { AlertRule, Monitor, RuleInput, RuleType } from "../types";

const TYPE_HELP: Record<RuleType, string> = {
  failure_streak: "fires after N consecutive failed checks",
  latency: "fires when latency stays above the threshold",
  error_rate: "fires when the failure ratio in the window is too high",
  anomaly: "flags latency outliers with an Isolation Forest (needs at least 30 checks of history)",
};

interface Props {
  rules: AlertRule[];
  monitors: Monitor[];
  onChanged: () => Promise<void> | void;
}

export function RulesPanel({ rules, monitors, onChanged }: Props) {
  const [showForm, setShowForm] = useState(rules.length === 0);
  const [error, setError] = useState<string | null>(null);
  const monitorName = (id: number | null) =>
    id === null ? "all monitors" : monitors.find((m) => m.id === id)?.name ?? `#${id}`;

  const remove = async (rule: AlertRule) => {
    if (!window.confirm(`Delete rule "${rule.name}"?`)) return;
    try {
      await api.deleteRule(rule.id);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const toggle = async (rule: AlertRule) => {
    try {
      await api.updateRule(rule.id, { enabled: !rule.enabled });
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div>
      {error && <div className="error-banner">{error}</div>}
      <div className="row spread" style={{ marginBottom: 12 }}>
        <span className="subtle">Rules decide when a check becomes an incident</span>
        <button className="btn primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Close" : "Add rule"}
        </button>
      </div>

      {showForm && (
        <div className="section">
          <h3>New alert rule</h3>
          <RuleForm
            monitors={monitors}
            onSubmit={async (payload) => {
              setError(null);
              try {
                await api.createRule(payload);
                setShowForm(false);
                await onChanged();
              } catch (err) {
                setError((err as Error).message);
              }
            }}
          />
        </div>
      )}

      <div className="section">
        {rules.length === 0 ? (
          <div className="empty">
            No rules yet — without one, incidents are never opened and no alerts are sent.
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Scope</th>
                <th>Type</th>
                <th>Condition</th>
                <th>Channels</th>
                <th>Enabled</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id}>
                  <td>
                    <strong>{rule.name}</strong>
                  </td>
                  <td className="subtle">{monitorName(rule.monitor_id)}</td>
                  <td className="subtle">{rule.type}</td>
                  <td className="mono">{describeRule(rule)}</td>
                  <td className="subtle">
                    {[rule.webhook_url ? "webhook" : "", rule.email_to.length ? "email" : ""]
                      .filter(Boolean)
                      .join(", ") || "—"}
                  </td>
                  <td>{rule.enabled ? "yes" : "no"}</td>
                  <td>
                    <div className="row">
                      <button className="btn" onClick={() => void toggle(rule)}>
                        {rule.enabled ? "Disable" : "Enable"}
                      </button>
                      <button className="btn danger" onClick={() => void remove(rule)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export function describeRule(rule: AlertRule): string {
  switch (rule.type) {
    case "failure_streak":
      return `${rule.threshold} consecutive failures`;
    case "latency":
      return rule.duration_seconds > 0
        ? `> ${rule.threshold}ms for ${rule.duration_seconds}s`
        : `> ${rule.threshold}ms`;
    case "error_rate":
      return `${Math.round(rule.threshold * 100)}% failures in ${rule.window_seconds}s (min ${rule.min_samples} checks)`;
    case "anomaly":
      return `outlier at ${Math.round(rule.threshold * 100)}% contamination in ${rule.window_seconds}s (min ${rule.min_samples} checks)`;
  }
}

interface FormProps {
  monitors: Monitor[];
  onSubmit: (payload: RuleInput) => Promise<void> | void;
}

export function RuleForm({ monitors, onSubmit }: FormProps) {
  const [name, setName] = useState("");
  const [monitorId, setMonitorId] = useState<number | null>(null);
  const [type, setType] = useState<RuleType>("failure_streak");
  const [threshold, setThreshold] = useState(3);
  const [windowSeconds, setWindowSeconds] = useState(300);
  const [durationSeconds, setDurationSeconds] = useState(0);
  const [minSamples, setMinSamples] = useState(5);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [emails, setEmails] = useState("");

  return (
    <form
      className="grid"
      onSubmit={(event) => {
        event.preventDefault();
        void onSubmit({
          name,
          monitor_id: monitorId,
          type,
          threshold,
          window_seconds: windowSeconds,
          duration_seconds: durationSeconds,
          min_samples: minSamples,
          webhook_url: webhookUrl,
          email_to: emails
            .split(",")
            .map((address) => address.trim())
            .filter(Boolean),
        });
      }}
    >
      <label className="field">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} required placeholder="payments down" />
      </label>
      <label className="field">
        Scope
        <select
          value={monitorId ?? ""}
          onChange={(e) => setMonitorId(e.target.value === "" ? null : Number(e.target.value))}
        >
          <option value="">All monitors</option>
          {monitors.map((monitor) => (
            <option key={monitor.id} value={monitor.id}>
              {monitor.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Type
        <select
          value={type}
          onChange={(e) => {
            const next = e.target.value as RuleType;
            setType(next);
            if (next === "latency") setThreshold(500);
            if (next === "error_rate") setThreshold(0.5);
            if (next === "failure_streak") setThreshold(3);
            if (next === "anomaly") {
              setThreshold(0.05); // default contamination
              setMinSamples(50); // above the detector's 30-point floor
            }
          }}
        >
          <option value="failure_streak">Consecutive failures</option>
          <option value="latency">Latency</option>
          <option value="error_rate">Error rate</option>
          <option value="anomaly">Latency anomaly (ML)</option>
        </select>
        <span className="subtle">{TYPE_HELP[type]}</span>
      </label>
      <label className="field">
        Threshold {type === "error_rate" ? "(0–1 ratio)" : type === "anomaly" ? "(0–0.5 contamination)" : ""}
        <input
          type="number"
          min={type === "error_rate" || type === "anomaly" ? 0.01 : 1}
          max={type === "anomaly" ? 0.5 : undefined}
          step={type === "error_rate" || type === "anomaly" ? 0.01 : 1}
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
        />
      </label>
      {type === "latency" && (
        <label className="field">
          Sustained for (s, 0 = single check)
          <input
            type="number"
            min={0}
            value={durationSeconds}
            onChange={(e) => setDurationSeconds(Number(e.target.value))}
          />
        </label>
      )}
      {(type === "error_rate" || type === "anomaly") && (
        <>
          <label className="field">
            Window (s)
            <input
              type="number"
              min={1}
              value={windowSeconds}
              onChange={(e) => setWindowSeconds(Number(e.target.value))}
            />
          </label>
          <label className="field">
            {type === "anomaly" ? "History (min 30 checks)" : "Minimum checks"}
            <input
              type="number"
              min={type === "anomaly" ? 30 : 1}
              value={minSamples}
              onChange={(e) => setMinSamples(Number(e.target.value))}
            />
          </label>
        </>
      )}
      <label className="field">
        Webhook URL
        <input
          value={webhookUrl}
          onChange={(e) => setWebhookUrl(e.target.value)}
          placeholder="https://hooks.slack.com/…"
        />
      </label>
      <label className="field">
        Alert emails (comma separated)
        <input
          value={emails}
          onChange={(e) => setEmails(e.target.value)}
          placeholder="oncall@example.com"
        />
      </label>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <button className="btn primary" type="submit">
          Create rule
        </button>
      </div>
    </form>
  );
}
