import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { durationBetween, fmtLatency, fmtUptime, timeAgo, truncate } from "../format";
import type { Anomalies, Incident, Metrics, Monitor, MonitorInput } from "../types";
import { StatusBadge } from "./StatusBadge";

interface ListProps {
  monitors: Monitor[];
  onChanged: () => Promise<void> | void;
  onSelect: (id: number) => void;
}

export function MonitorList({ monitors, onChanged, onSelect }: ListProps) {
  const [showForm, setShowForm] = useState(monitors.length === 0);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState<number | null>(null);

  const checkNow = async (monitor: Monitor) => {
    setChecking(monitor.id);
    try {
      await api.checkNow(monitor.id);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setChecking(null);
    }
  };

  const remove = async (monitor: Monitor) => {
    if (!window.confirm(`Delete monitor "${monitor.name}" and all of its history?`)) return;
    try {
      await api.deleteMonitor(monitor.id);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const toggle = async (monitor: Monitor) => {
    try {
      await api.updateMonitor(monitor.id, { enabled: !monitor.enabled });
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div>
      {error && <div className="error-banner">{error}</div>}
      <div className="row spread" style={{ marginBottom: 12 }}>
        <span className="subtle">
          {monitors.length} monitor{monitors.length === 1 ? "" : "s"} · checks run on the server
          schedule
        </span>
        <button className="btn primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Close" : "Add monitor"}
        </button>
      </div>

      {showForm && (
        <div className="section">
          <h3>New monitor</h3>
          <MonitorForm
            onSubmit={async (payload) => {
              setError(null);
              try {
                await api.createMonitor(payload);
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
        {monitors.length === 0 ? (
          <div className="empty">No monitors yet — add one to start tracking.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Name</th>
                <th>Endpoint</th>
                <th>Last check</th>
                <th>Latency</th>
                <th>Uptime 24h</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {monitors.map((monitor) => (
                <tr key={monitor.id} className="clickable" onClick={() => onSelect(monitor.id)}>
                  <td>
                    <StatusBadge status={monitor.status} enabled={monitor.enabled} />
                  </td>
                  <td>
                    <strong>{monitor.name}</strong>
                    {monitor.last_error && (
                      <div className="reason">{truncate(monitor.last_error, 56)}</div>
                    )}
                  </td>
                  <td className="mono">
                    {monitor.method} {truncate(monitor.url, 44)}
                  </td>
                  <td className="subtle">{timeAgo(monitor.last_checked_at)}</td>
                  <td>{fmtLatency(monitor.last_latency_ms)}</td>
                  <td>
                    <UptimeBar ratio={monitorUptime(monitor)} />
                  </td>
                  <td onClick={(event) => event.stopPropagation()}>
                    <div className="row">
                      <button
                        className="btn"
                        disabled={checking === monitor.id}
                        onClick={() => checkNow(monitor)}
                      >
                        {checking === monitor.id ? "…" : "Check"}
                      </button>
                      <button className="btn" onClick={() => toggle(monitor)}>
                        {monitor.enabled ? "Pause" : "Resume"}
                      </button>
                      <button className="btn danger" onClick={() => remove(monitor)}>
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

// The list only knows the summary uptime once detail has loaded it; keep the
// row honest with the last known status until then.
function monitorUptime(monitor: Monitor): number | null {
  if (monitor.status === "up") return 1;
  if (monitor.status === "down") return 0;
  return null;
}

export function UptimeBar({ ratio }: { ratio: number | null }) {
  const percent = ratio === null ? 0 : Math.round(ratio * 100);
  return (
    <div className="row" style={{ gap: 8 }}>
      <div className="uptime-bar">
        <div className={ratio !== null && ratio < 1 ? "bad" : ""} style={{ width: `${percent}%` }} />
      </div>
      <span className="subtle">{fmtUptime(ratio)}</span>
    </div>
  );
}

interface FormProps {
  onSubmit: (payload: MonitorInput) => Promise<void> | void;
}

export function MonitorForm({ onSubmit }: FormProps) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [method, setMethod] = useState("GET");
  const [interval, setInterval] = useState(30);
  const [timeout, setTimeoutSeconds] = useState(10);
  const [statusMin, setStatusMin] = useState(200);
  const [statusMax, setStatusMax] = useState(399);
  const [unhealthy, setUnhealthy] = useState(3);
  const [healthy, setHealthy] = useState(2);

  return (
    <form
      className="grid"
      onSubmit={(event) => {
        event.preventDefault();
        void onSubmit({
          name,
          url,
          method,
          interval_seconds: interval,
          timeout_seconds: timeout,
          expected_status_min: statusMin,
          expected_status_max: statusMax,
          unhealthy_threshold: unhealthy,
          healthy_threshold: healthy,
        });
      }}
    >
      <label className="field">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} required placeholder="payments-api" />
      </label>
      <label className="field">
        URL
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          placeholder="https://api.example.com/health"
        />
      </label>
      <label className="field">
        Method
        <select value={method} onChange={(e) => setMethod(e.target.value)}>
          {["GET", "POST", "HEAD"].map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
      </label>
      <label className="field">
        Interval (s)
        <input
          type="number"
          min={5}
          value={interval}
          onChange={(e) => setInterval(Number(e.target.value))}
        />
      </label>
      <label className="field">
        Timeout (s)
        <input
          type="number"
          min={1}
          step="0.5"
          value={timeout}
          onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
        />
      </label>
      <label className="field">
        Expected status min
        <input
          type="number"
          min={100}
          max={599}
          value={statusMin}
          onChange={(e) => setStatusMin(Number(e.target.value))}
        />
      </label>
      <label className="field">
        Expected status max
        <input
          type="number"
          min={100}
          max={599}
          value={statusMax}
          onChange={(e) => setStatusMax(Number(e.target.value))}
        />
      </label>
      <label className="field">
        Alert after N failures
        <input
          type="number"
          min={1}
          value={unhealthy}
          onChange={(e) => setUnhealthy(Number(e.target.value))}
        />
      </label>
      <label className="field">
        Recover after N successes
        <input
          type="number"
          min={1}
          value={healthy}
          onChange={(e) => setHealthy(Number(e.target.value))}
        />
      </label>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <button className="btn primary" type="submit">
          Create monitor
        </button>
      </div>
    </form>
  );
}

interface DetailProps {
  monitor: Monitor;
  onBack: () => void;
  onChanged: () => Promise<void> | void;
}

export function MonitorDetail({ monitor, onBack, onChanged }: DetailProps) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [anomalies, setAnomalies] = useState<Anomalies | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [nextMetrics, nextIncidents, nextAnomalies] = await Promise.all([
          api.metrics(monitor.id, 24, 48),
          api.monitorIncidents(monitor.id),
          api.anomalies(monitor.id, 24),
        ]);
        if (!cancelled) {
          setMetrics(nextMetrics);
          setIncidents(nextIncidents);
          setAnomalies(nextAnomalies);
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [monitor.id, monitor.last_checked_at]);

  const series = (metrics?.buckets ?? [])
    .filter((bucket) => bucket.total > 0)
    .map((bucket) => ({
      time: new Date(bucket.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      avg: bucket.avg_latency_ms,
      p95: bucket.p95_latency_ms,
      uptime: Math.round(bucket.uptime * 100),
    }));

  // Mark buckets whose time range contains at least one ML-flagged anomaly,
  // so the detector's output lands on the chart the operator already reads.
  const anomalyTimes = new Set(
    (anomalies?.anomalies ?? [])
      .map((point) => point.timestamp)
      .filter((ts): ts is string => ts !== null)
      .map((ts) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })),
  );
  const seriesWithFlags = series.map((point) => ({
    ...point,
    anomaly: anomalyTimes.has(point.time) ? point.avg : null,
  }));
  const anomalyCount = anomalies?.anomalies.length ?? 0;

  return (
    <div>
      <div className="row spread" style={{ marginBottom: 12 }}>
        <div className="row">
          <button className="btn" onClick={onBack}>
            ← Back
          </button>
          <h3 style={{ margin: 0 }}>{monitor.name}</h3>
          <StatusBadge status={monitor.status} enabled={monitor.enabled} />
        </div>
        <span className="subtle mono">
          {monitor.method} {monitor.url}
        </span>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="summary">
        <div className="card">
          <div className="label">Uptime 24h</div>
          <div className="value">{fmtUptime(metrics?.uptime ?? null)}</div>
        </div>
        <div className="card">
          <div className="label">Avg latency</div>
          <div className="value">{fmtLatency(metrics?.avg_latency_ms ?? null)}</div>
        </div>
        <div className="card">
          <div className="label">p95 latency</div>
          <div className="value">{fmtLatency(metrics?.p95_latency_ms ?? null)}</div>
        </div>
        <div className="card">
          <div className="label">Checks 24h</div>
          <div className="value">
            {metrics?.total ?? 0}
            <span className="subtle"> · {metrics?.failures ?? 0} failed</span>
          </div>
        </div>
      </div>

      <div className="section">
        <h3>
          Latency (24h)
          {anomalies?.sufficient_data && (
            <span className="subtle" style={{ fontWeight: "normal", marginLeft: 8, fontSize: 12 }}>
              {anomalyCount === 0
                ? "· no ML anomalies detected"
                : `· ${anomalyCount} ML anomal${anomalyCount === 1 ? "y" : "ies"} flagged`}
            </span>
          )}
        </h3>
        {series.length === 0 ? (
          <div className="empty">No checks recorded in this window yet.</div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={seriesWithFlags} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#263246" strokeDasharray="3 3" />
              <XAxis dataKey="time" stroke="#8b98ac" minTickGap={40} />
              <YAxis stroke="#8b98ac" width={44} unit="ms" />
              <Tooltip
                contentStyle={{ background: "#131a26", border: "1px solid #263246" }}
                formatter={(value, name) => (name === "anomaly" ? `⚠ ${fmtLatency(Number(value))}` : fmtLatency(Number(value)))}
              />
              <Line type="monotone" dataKey="avg" name="avg" stroke="#5b8cff" dot={false} connectNulls />
              <Line
                type="monotone"
                dataKey="p95"
                name="p95"
                stroke="#f5b14c"
                dot={false}
                strokeDasharray="4 3"
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="anomaly"
                name="anomaly"
                stroke="none"
                dot={{ r: 4, fill: "#ff5d73", strokeWidth: 0 }}
                activeDot={{ r: 6 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
        {anomalies && !anomalies.sufficient_data && (
          <div className="empty" style={{ marginTop: 6 }}>
            ML anomaly detection needs {30 - anomalies.points_analyzed} more checks
            ({anomalies.points_analyzed}/30 collected).
          </div>
        )}
      </div>

      <div className="section">
        <h3>Uptime per 30 minutes</h3>
        {series.length === 0 ? (
          <div className="empty">Nothing to chart yet.</div>
        ) : (
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={series} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#263246" strokeDasharray="3 3" />
              <XAxis dataKey="time" stroke="#8b98ac" minTickGap={40} />
              <YAxis stroke="#8b98ac" width={36} domain={[0, 100]} unit="%" />
              <Tooltip
                contentStyle={{ background: "#131a26", border: "1px solid #263246" }}
                formatter={(value) => `${value}%`}
              />
              <Bar dataKey="uptime" name="uptime" fill="#33d69f" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="section">
        <h3>Incident history</h3>
        {incidents.length === 0 ? (
          <div className="empty">No incidents recorded for this monitor.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Reason</th>
                <th>Opened</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((incident) => (
                <tr key={incident.id}>
                  <td>
                    <span className={`badge ${incident.status === "open" ? "down" : "up"}`}>
                      {incident.status}
                    </span>
                  </td>
                  <td className="reason">{incident.reason}</td>
                  <td className="subtle">{timeAgo(incident.opened_at)}</td>
                  <td className="subtle">{durationBetween(incident.opened_at, incident.closed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" onClick={() => void onChanged()}>
            Refresh
          </button>
        </div>
      </div>
    </div>
  );
}
