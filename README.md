# Sentinel

### Full-Stack Uptime Monitoring & Anomaly Detection Platform

PulseOps is a self-hosted HTTP monitoring platform that tracks endpoint availability and response latency, evaluates configurable alert rules, and manages incidents through an automated lifecycle. It includes a React dashboard for monitoring services, visualizing performance metrics, and managing alerts, along with an Isolation Forest-based anomaly detection component.

## Features

- **HTTP Monitoring:** Periodically checks registered HTTP endpoints using asynchronous requests, with configurable methods, headers, timeouts, and expected status codes.
- **Performance Tracking:** Records response times, status codes, and check history, with uptime and latency statistics including average, p95, and maximum latency.
- **Alert Rules:** Supports consecutive failure streaks, sustained latency thresholds, rolling error rates, and ML-based anomaly detection.
- **Incident Management:** Automatically creates incidents when alert rules trigger, supports acknowledgement and manual resolution, and automatically resolves incidents when services recover.
- **Notifications:** Sends incident and recovery notifications through configurable webhooks and SMTP email, with webhook retries and backoff.
- **Anomaly Detection:** Uses Isolation Forest to identify unusual latency patterns based on engineered time-series features.
- **Interactive Dashboard:** Provides monitor management, incident tracking, alert-rule configuration, and latency/uptime visualizations.

## Dashboard

<img width="944" height="346" alt="image" src="https://github.com/user-attachments/assets/ed86888c-7fd8-4cbe-b2b6-7ac30f5a3ae1" />


## Tech Stack

| Category | Technologies |
|---|---|
| Backend | Python, FastAPI, Uvicorn, Pydantic |
| Frontend | React 18, TypeScript, Vite |
| Database | SQLite (WAL mode) |
| HTTP Requests | httpx, asyncio |
| Machine Learning | scikit-learn, NumPy, Isolation Forest |
| Visualization | Recharts |
| Testing | pytest, pytest-asyncio |
| Code Quality | Ruff, TypeScript strict checks |
| CI/CD | GitHub Actions |
| Containerization | Docker, Docker Compose |

## Architecture

```text
                  React Dashboard
                  (TypeScript + Vite)
                         |
                         v
                   FastAPI REST API
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
      Monitoring     Alert Rules    Incidents
       Service        Engine       Management
          |              |              |
          v              +-------+------+
    Async Scheduler              |
          |                      v
          v                Notifications
      HTTP Probes           Webhook / SMTP
          |
          v
      SQLite Database
          |
          v
    Check History
          |
          v
    Isolation Forest
    Anomaly Detection
```
## Machine Learning and Benchmark Evaluation

Sentinel integrates an Isolation Forest model for detecting anomalous latency patterns.

The detector uses engineered features derived from latency time series:

- Latency level
- First-order difference
- Trailing mean and standard deviation
- Residual relative to the trailing mean

The detector uses a default contamination setting of `0.02`, `200` estimators, and a minimum requirement of `30` observations.

### Numenta Anomaly Benchmark (NAB)

The anomaly detector was evaluated on the public Numenta Anomaly Benchmark using 47 real-world time series containing 321,206 data points.

| Evaluation Metric | Result |
|---|---:|
| Precision | 88.65% |
| Recall | 97.62% |
| F1 Score | 0.9292 |
| Anomaly Windows Detected | 103 / 110 |
| Anomaly-Window Recall | 93.64% |
| Recorded Runtime | 18.6 seconds |

**Evaluation methodology:** The benchmark uses the official NAB combined anomaly-window labels and a point-adjust evaluation protocol. A labeled anomaly window is counted as detected when at least one point within that window is flagged. The model configuration was fixed using production defaults rather than tuned against benchmark results.
