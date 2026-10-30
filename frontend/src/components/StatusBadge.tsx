import type { MonitorStatus } from "../types";

export function StatusBadge({ status, enabled }: { status: MonitorStatus; enabled: boolean }) {
  if (!enabled) return <span className="badge disabled">paused</span>;
  return <span className={`badge ${status}`}>{status}</span>;
}
