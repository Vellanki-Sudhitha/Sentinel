import { useState } from "react";
import { api } from "../api";
import { durationBetween, timeAgo, truncate } from "../format";
import type { Incident } from "../types";

interface Props {
  incidents: Incident[];
  onChanged: () => Promise<void> | void;
  onSelectMonitor: (id: number) => void;
}

export function IncidentsPanel({ incidents, onChanged, onSelectMonitor }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const act = async (incident: Incident, action: "acknowledge" | "resolve") => {
    setBusy(incident.id);
    try {
      if (action === "acknowledge") await api.acknowledgeIncident(incident.id);
      else await api.resolveIncident(incident.id);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const open = incidents.filter((incident) => incident.status === "open");
  const resolved = incidents.filter((incident) => incident.status === "resolved").slice(0, 25);

  return (
    <div>
      {error && <div className="error-banner">{error}</div>}

      <div className="section">
        <h3>Open incidents ({open.length})</h3>
        {open.length === 0 ? (
          <div className="empty">All monitored services are healthy.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Monitor</th>
                <th>Reason</th>
                <th>Open for</th>
                <th>Acknowledged</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {open.map((incident) => (
                <tr key={incident.id}>
                  <td>
                    <a
                      href="#"
                      onClick={(event) => {
                        event.preventDefault();
                        onSelectMonitor(incident.monitor_id);
                      }}
                    >
                      {incident.monitor_name ?? `monitor #${incident.monitor_id}`}
                    </a>
                  </td>
                  <td className="reason">{truncate(incident.reason, 90)}</td>
                  <td className="subtle">{durationBetween(incident.opened_at, null)}</td>
                  <td className="subtle">
                    {incident.acknowledged_at ? timeAgo(incident.acknowledged_at) : "no"}
                  </td>
                  <td>
                    <div className="row">
                      <button
                        className="btn"
                        disabled={busy === incident.id || Boolean(incident.acknowledged_at)}
                        onClick={() => void act(incident, "acknowledge")}
                      >
                        Acknowledge
                      </button>
                      <button
                        className="btn danger"
                        disabled={busy === incident.id}
                        onClick={() => void act(incident, "resolve")}
                      >
                        Resolve
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="section">
        <h3>Recent resolved</h3>
        {resolved.length === 0 ? (
          <div className="empty">No resolved incidents yet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Monitor</th>
                <th>Reason</th>
                <th>Opened</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {resolved.map((incident) => (
                <tr key={incident.id}>
                  <td>{incident.monitor_name ?? `monitor #${incident.monitor_id}`}</td>
                  <td className="reason">{truncate(incident.reason, 90)}</td>
                  <td className="subtle">{timeAgo(incident.opened_at)}</td>
                  <td className="subtle">
                    {durationBetween(incident.opened_at, incident.closed_at)}
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
