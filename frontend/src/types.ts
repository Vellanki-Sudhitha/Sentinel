/** Types mirroring the FastAPI response models. */

export type MonitorStatus = "unknown" | "up" | "down";

export interface Monitor {
  id: number;
  name: string;
  url: string;
  method: string;
  headers: Record<string, string>;
  interval_seconds: number;
  timeout_seconds: number;
  expected_status_min: number;
  expected_status_max: number;
  unhealthy_threshold: number;
  healthy_threshold: number;
  enabled: boolean;
  status: MonitorStatus;
  consecutive_failures: number;
  consecutive_successes: number;
  last_checked_at: string | null;
  last_ok: boolean | null;
  last_status_code: number | null;
  last_latency_ms: number | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface MonitorInput {
  name: string;
  url: string;
  method?: string;
  interval_seconds?: number;
  timeout_seconds?: number;
  expected_status_min?: number;
  expected_status_max?: number;
  unhealthy_threshold?: number;
  healthy_threshold?: number;
  enabled?: boolean;
}

export interface MetricBucket {
  start: string;
  end: string;
  total: number;
  failures: number;
  uptime: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  max_latency_ms: number | null;
}

export interface Metrics {
  monitor_id: number;
  window_hours: number;
  total: number;
  failures: number;
  uptime: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  max_latency_ms: number | null;
  buckets: MetricBucket[];
}

export interface AnomalyPoint {
  index: number;
  timestamp: string | null;
  value_ms: number;
  score: number;
}

export interface Anomalies {
  monitor_id: number;
  window_hours: number;
  contamination: number;
  points_analyzed: number;
  sufficient_data: boolean;
  anomalies: AnomalyPoint[];
}

export type RuleType = "failure_streak" | "latency" | "error_rate" | "anomaly";

export interface AlertRule {
  id: number;
  name: string;
  monitor_id: number | null;
  type: RuleType;
  threshold: number;
  window_seconds: number;
  duration_seconds: number;
  min_samples: number;
  webhook_url: string;
  email_to: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface RuleInput {
  name: string;
  monitor_id: number | null;
  type: RuleType;
  threshold: number;
  window_seconds?: number;
  duration_seconds?: number;
  min_samples?: number;
  webhook_url?: string;
  email_to?: string[];
  enabled?: boolean;
}

export interface Incident {
  id: number;
  monitor_id: number;
  monitor_name: string | null;
  rule_id: number | null;
  status: "open" | "resolved";
  reason: string;
  opened_at: string;
  updated_at: string;
  closed_at: string | null;
  acknowledged_at: string | null;
}

export interface SummaryMonitor {
  id: number;
  name: string;
  url: string;
  status: MonitorStatus;
  enabled: boolean;
  last_checked_at: string | null;
  last_latency_ms: number | null;
  last_status_code: number | null;
  last_error: string | null;
  uptime_24h: number | null;
  avg_latency_24h: number | null;
  open_incidents: number;
}

export interface Summary {
  total_monitors: number;
  up: number;
  down: number;
  unknown: number;
  disabled: number;
  open_incidents: number;
  checks_24h: number;
  failures_24h: number;
  monitors: SummaryMonitor[];
}
