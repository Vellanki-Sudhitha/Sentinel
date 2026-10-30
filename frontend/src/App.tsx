import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { IncidentsPanel } from "./components/IncidentsPanel";
import { MonitorDetail, MonitorList } from "./components/MonitorList";
import { RulesPanel } from "./components/RulesPanel";
import type { AlertRule, Incident, Monitor, Summary } from "./types";

const REFRESH_MS = 10_000;

type Tab = "monitors" | "incidents" | "rules";

export default function App() {
  const [tab, setTab] = useState<Tab>("monitors");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [monitors, setMonitors] = useState<Monitor[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextSummary, nextMonitors, nextIncidents, nextRules] = await Promise.all([
        api.summary(),
        api.monitors(),
        api.incidents(),
        api.rules(),
      ]);
      setSummary(nextSummary);
      setMonitors(nextMonitors);
      setIncidents(nextIncidents);
      setRules(nextRules);
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const selected = selectedId === null ? null : (monitors.find((m) => m.id === selectedId) ?? null);
  const openIncidents = incidents.filter((incident) => incident.status === "open");

  return (
    <div className="app">
      <header className="topbar">
        <div className="row">
          <span className="logo">◉ Sentinel</span>
          <nav className="tabs">
            {(
              [
                ["monitors", "Monitors"],
                ["incidents", `Incidents${openIncidents.length ? ` (${openIncidents.length})` : ""}`],
                ["rules", "Alert rules"],
              ] as [Tab, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                className={tab === key ? "tab active" : "tab"}
                onClick={() => {
                  setTab(key);
                  setSelectedId(null);
                }}
              >
                {label}
              </button>
            ))}
          </nav>
        </div>
        <div className="row">
          <span className="subtle">
            {lastUpdated
              ? `updated ${lastUpdated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
              : "loading…"}
          </span>
          <button className="btn" onClick={() => void refresh()}>
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="error-banner">
          API error: {error} — is the backend running on its configured port?
        </div>
      )}

      {summary && (
        <div className="summary">
          <div className="card">
            <div className="label">Monitors up</div>
            <div className="value">
              {summary.up}
              <span className="subtle"> / {summary.total_monitors}</span>
            </div>
          </div>
          <div className="card">
            <div className="label">Down</div>
            <div className={summary.down > 0 ? "value bad-text" : "value"}>{summary.down}</div>
          </div>
          <div className="card">
            <div className="label">Open incidents</div>
            <div className={summary.open_incidents > 0 ? "value bad-text" : "value"}>
              {summary.open_incidents}
            </div>
          </div>
          <div className="card">
            <div className="label">Uptime 24h</div>
            <div className="value">
              {summary.checks_24h > 0
                ? `${(((summary.checks_24h - summary.failures_24h) / summary.checks_24h) * 100).toFixed(2)}%`
                : "—"}
            </div>
          </div>
        </div>
      )}

      <main>
        {tab === "monitors" &&
          (selected ? (
            <MonitorDetail
              monitor={selected}
              onBack={() => setSelectedId(null)}
              onChanged={refresh}
            />
          ) : (
            <MonitorList monitors={monitors} onChanged={refresh} onSelect={setSelectedId} />
          ))}
        {tab === "incidents" && (
          <IncidentsPanel
            incidents={incidents}
            onChanged={refresh}
            onSelectMonitor={(id) => {
              setTab("monitors");
              setSelectedId(id);
            }}
          />
        )}
        {tab === "rules" && <RulesPanel rules={rules} monitors={monitors} onChanged={refresh} />}
      </main>

    </div>
  );
}
