/** Thin typed client for the Sentinel API. */

import type {
  AlertRule,
  Anomalies,
  Incident,
  Metrics,
  Monitor,
  MonitorInput,
  RuleInput,
  Summary,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (Array.isArray(body.detail)) {
        detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
      } else if (typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  summary: () => request<Summary>("/api/summary"),
  monitors: () => request<Monitor[]>("/api/monitors"),
  createMonitor: (payload: MonitorInput) =>
    request<Monitor>("/api/monitors", { method: "POST", body: JSON.stringify(payload) }),
  updateMonitor: (id: number, payload: Partial<MonitorInput>) =>
    request<Monitor>(`/api/monitors/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteMonitor: (id: number) => request<void>(`/api/monitors/${id}`, { method: "DELETE" }),
  checkNow: (id: number) =>
    request<{ check: { ok: boolean }; monitor: Monitor }>(`/api/monitors/${id}/check`, {
      method: "POST",
    }),
  metrics: (id: number, hours = 24, buckets = 48) =>
    request<Metrics>(`/api/monitors/${id}/metrics?hours=${hours}&buckets=${buckets}`),
  incidents: (status?: "open" | "resolved", limit = 200) =>
    request<Incident[]>(`/api/incidents?limit=${limit}${status ? `&status=${status}` : ""}`),
  monitorIncidents: (id: number) => request<Incident[]>(`/api/monitors/${id}/incidents`),
  anomalies: (id: number, hours = 24) =>
    request<Anomalies>(`/api/monitors/${id}/anomalies?hours=${hours}`),
  acknowledgeIncident: (id: number) =>
    request<Incident>(`/api/incidents/${id}/acknowledge`, { method: "POST" }),
  resolveIncident: (id: number) =>
    request<Incident>(`/api/incidents/${id}/resolve`, { method: "POST" }),
  rules: () => request<AlertRule[]>("/api/rules"),
  createRule: (payload: RuleInput) =>
    request<AlertRule>("/api/rules", { method: "POST", body: JSON.stringify(payload) }),
  updateRule: (id: number, payload: Partial<RuleInput>) =>
    request<AlertRule>(`/api/rules/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteRule: (id: number) => request<void>(`/api/rules/${id}`, { method: "DELETE" }),
};
