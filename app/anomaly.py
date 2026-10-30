"""Unsupervised latency anomaly detection (Isolation Forest).

The detector turns a 1-D latency series into engineered features (level, first
difference, trailing mean/std, residual) so the forest can separate genuine
anomalies — spikes, level shifts, elevated plateaus — from routine jitter.

Unsupervised by design: nothing here ever sees labels. Labels are only used by
the offline benchmark (scripts/benchmark_nab.py) for evaluation.

Threshold rule: anomalies are points scoring strictly above the
``(1 - contamination)`` quantile of the score distribution. Strict inequality
matters — a perfectly flat series produces identical scores, and a ``>=``
threshold would flag every point on it.

Benchmarked on the Numenta Anomaly Benchmark (NAB), 47 real-world series.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

# Detector defaults (shared by the API and the benchmark on purpose).
MIN_POINTS = 30  # below this a series carries no usable distribution
WINDOW = 20  # trailing window for mean/std features, in samples
CONTAMINATION = 0.02  # expected upper bound on the anomaly fraction
N_ESTIMATORS = 200
RANDOM_STATE = 42  # deterministic output for tests and reproducible benchmarks


@dataclass(frozen=True)
class Anomaly:
    """One flagged point: index into the input series plus its anomaly score."""

    index: int
    score: float  # higher = more anomalous


def _trailing_stats(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Trailing mean/std over up to ``window`` samples, partial at the start."""
    n = len(values)
    csum = np.concatenate(([0.0], np.cumsum(values)))
    csum2 = np.concatenate(([0.0], np.cumsum(values * values)))
    counts = np.arange(1, n + 1)
    ends = np.minimum(counts, window)
    sums = csum[counts] - csum[counts - ends]
    sums2 = csum2[counts] - csum2[counts - ends]
    mean = sums / ends
    variance = np.maximum(sums2 / ends - mean * mean, 0.0)
    return mean, np.sqrt(variance)


def build_features(values: Sequence[float], window: int = WINDOW) -> np.ndarray:
    """Engineer the feature matrix: value, delta, trailing mean/std, residual."""
    v = np.nan_to_num(np.asarray(values, dtype=np.float64))
    mean, std = _trailing_stats(v, window)
    delta = np.zeros_like(v)
    delta[1:] = np.diff(v)
    residual = v - mean
    return np.column_stack([v, delta, mean, std, residual])


def fit_scores(values: Sequence[float], window: int = WINDOW) -> np.ndarray:
    """Fit the forest and return one anomaly score per point (higher = worse)."""
    v = np.nan_to_num(np.asarray(values, dtype=np.float64))
    if len(v) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} points, got {len(v)}")
    forest = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination="auto",  # thresholding happens in flags_for, not the model
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    features = build_features(v, window)
    forest.fit(features)
    # score_samples: higher = more normal; negate so higher = more anomalous.
    return -forest.score_samples(features)


def threshold_for(scores: np.ndarray, contamination: float = CONTAMINATION) -> float:
    """Score cutoff at the ``(1 - contamination)`` quantile."""
    contamination = float(np.clip(contamination, 1e-6, 0.5))
    return float(np.percentile(scores, 100.0 * (1.0 - contamination)))


def flags_for(scores: np.ndarray, contamination: float = CONTAMINATION) -> np.ndarray:
    """Boolean anomaly mask; strict ``>`` so exact ties never flood the output."""
    return scores > threshold_for(scores, contamination)


def detect(
    values: Sequence[float],
    contamination: float = CONTAMINATION,
    window: int = WINDOW,
) -> list[Anomaly]:
    """Convenience wrapper: fit, threshold, and return the flagged points."""
    scores = fit_scores(values, window=window)
    mask = flags_for(scores, contamination)
    return [Anomaly(index=int(i), score=float(scores[i])) for i in np.flatnonzero(mask)]
