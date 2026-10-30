"""Benchmark app.anomaly against the Numenta Anomaly Benchmark (NAB).

Evaluates on all 47 ``real*`` series (47 series, ~321k points) using the
standard point-adjust protocol: a detection anywhere inside a labelled window
counts as detecting that whole window; false positives are flagged points
outside every window.

The detector is unsupervised and never sees the labels — labels are used only
here, for evaluation. Hyperparameters are the production defaults from
app.anomaly, chosen a priori: no tuning on the benchmark.

Usage:
    python scripts/benchmark_nab.py [--contamination 0.02] [--json out.json]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.anomaly import CONTAMINATION, WINDOW, fit_scores, flags_for  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NAB_DATA = ROOT / "benchmarks" / "nab" / "data"
NAB_LABELS = ROOT / "benchmarks" / "nab" / "labels" / "combined_windows.json"


def load_series(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a NAB csv into (timestamps as epoch seconds, values)."""
    times: list[float] = []
    values: list[float] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            # NAB timestamps are naive UTC; epoch conversion via fromisoformat
            from datetime import datetime

            times.append(datetime.fromisoformat(row["timestamp"]).timestamp())
            values.append(float(row["value"]))
    return np.asarray(times), np.asarray(values)


def point_adjust_flags(flags: np.ndarray, times: np.ndarray, windows: list[list[str]]) -> np.ndarray:
    """Standard NAB point-adjust: any hit inside a window marks the whole window."""
    adjusted = flags.copy()
    from datetime import datetime

    for start_iso, end_iso in windows:
        start = datetime.fromisoformat(start_iso).timestamp()
        end = datetime.fromisoformat(end_iso).timestamp()
        in_window = (times >= start) & (times <= end)
        if in_window.any() and flags[in_window].any():
            adjusted |= in_window
    return adjusted


def evaluate(contamination: float) -> dict:
    labels = json.loads(NAB_LABELS.read_text())
    series_paths = sorted(p for p in NAB_DATA.glob("real*/*.csv"))
    assert series_paths, "NAB data missing — run the download step first"

    per_series = []
    tp = fp = fn = tn = 0
    windows_total = windows_hit = 0
    started = time.perf_counter()

    for path in series_paths:
        key = f"{path.parent.name}/{path.name}"
        windows = labels.get(key, [])
        times, values = load_series(path)
        scores = fit_scores(values, window=WINDOW)
        raw_flags = flags_for(scores, contamination)
        adjusted = point_adjust_flags(raw_flags, times, windows)

        from datetime import datetime

        truth = np.zeros(len(values), dtype=bool)
        for start_iso, end_iso in windows:
            start = datetime.fromisoformat(start_iso).timestamp()
            end = datetime.fromisoformat(end_iso).timestamp()
            truth |= (times >= start) & (times <= end)

        t = int((adjusted & truth).sum())
        f = int((adjusted & ~truth).sum())
        m = int((~adjusted & truth).sum())
        n = int((~adjusted & ~truth).sum())
        tp += t
        fp += f
        fn += m
        tn += n
        windows_hit += sum(
            1
            for ws, we in windows
            if adjusted[(times >= datetime.fromisoformat(ws).timestamp())
                        & (times <= datetime.fromisoformat(we).timestamp())].any()
        )
        windows_total += len(windows)
        per_series.append({"series": key, "tp": t, "fp": f, "fn": m, "windows": len(windows)})

    elapsed = time.perf_counter() - started
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "contamination": contamination,
        "series": len(series_paths),
        "points": tp + fp + fn + tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "windows": windows_total,
        "windows_detected": windows_hit,
        "window_recall": round(windows_hit / windows_total, 4) if windows_total else 0.0,
        "seconds": round(elapsed, 1),
        "per_series": per_series,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contamination", type=float, default=CONTAMINATION)
    parser.add_argument("--json", type=Path, default=None, help="write full results to this file")
    args = parser.parse_args()

    result = evaluate(args.contamination)
    print("NAB real-data benchmark (point-adjust protocol)")
    print(f"  series          : {result['series']}")
    print(f"  points          : {result['points']}")
    print(f"  contamination   : {result['contamination']}")
    print(f"  precision       : {result['precision']}")
    print(f"  recall          : {result['recall']}")
    print(f"  F1              : {result['f1']}")
    print(f"  windows         : {result['windows_detected']}/{result['windows']}"
          f" detected (window recall {result['window_recall']})")
    print(f"  wall time       : {result['seconds']}s")

    worst = sorted(result["per_series"], key=lambda r: r["fn"], reverse=True)[:5]
    print("  most-missed series:")
    for row in worst:
        print(f"    {row['series']}: fn={row['fn']} fp={row['fp']} windows={row['windows']}")

    if args.json:
        args.json.write_text(json.dumps(result, indent=2))
        print(f"  wrote {args.json}")


if __name__ == "__main__":
    main()
