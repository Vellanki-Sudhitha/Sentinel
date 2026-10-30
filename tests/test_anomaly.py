"""Unit tests for the Isolation Forest anomaly detector."""

from __future__ import annotations

import numpy as np
import pytest

from app.anomaly import (
    CONTAMINATION,
    MIN_POINTS,
    build_features,
    detect,
    fit_scores,
    flags_for,
    threshold_for,
)


def make_series(n: int = 200, spike_at: tuple[int, ...] = (), seed: int = 7) -> list[float]:
    """Noisy baseline around 100ms with optional injected spikes."""
    rng = np.random.default_rng(seed)
    values = 100.0 + rng.normal(0.0, 3.0, n)
    for index in spike_at:
        values[index] = 420.0
    return [float(v) for v in values]


def test_build_features_shapes_and_partial_window():
    values = [10.0, 20.0, 30.0, 40.0]
    features = build_features(values, window=3)
    assert features.shape == (4, 5)
    # first row: trailing stats use only the samples seen so far (no NaN padding)
    assert np.isfinite(features).all()
    # value column mirrors the input
    np.testing.assert_allclose(features[:, 0], values)


def test_detect_finds_injected_spike():
    values = make_series(spike_at=(95,))
    anomalies = detect(values)
    flagged = {a.index for a in anomalies}
    assert 95 in flagged, "the injected 420ms spike must be flagged"
    # the spike must be the single most anomalous point
    assert max(anomalies, key=lambda a: a.score).index == 95
    # contamination=0.02 is a hard budget: ~4 flags on 200 points, never a flood
    assert len(anomalies) <= 5
    # non-spike flags may go either way (dips are anomalies too) but must stay
    # within a sane band of the baseline, i.e. we flag structure, not garbage
    baseline = [v for i, v in enumerate(values) if i != 95]
    mean, std = float(np.mean(baseline)), float(np.std(baseline))
    for anomaly in anomalies:
        if anomaly.index != 95:
            assert abs(values[anomaly.index] - mean) <= 4 * std


def test_flat_series_flags_nothing():
    # Exact ties must not flood the output: a perfectly flat series is not anomalous.
    assert detect([100.0] * 120) == []


def test_too_short_series_is_rejected():
    with pytest.raises(ValueError, match=f"at least {MIN_POINTS}"):
        fit_scores([1.0] * (MIN_POINTS - 1))
    # detect() surfaces the same guard
    with pytest.raises(ValueError):
        detect([1.0] * 5)


def test_scores_are_deterministic():
    values = make_series(spike_at=(40, 150))
    np.testing.assert_array_equal(fit_scores(values), fit_scores(values))


def test_contamination_controls_flag_count():
    rng = np.random.default_rng(11)
    values = [float(v) for v in 100.0 + rng.normal(0, 5, 400)]
    scores = fit_scores(values)
    lo = int(flags_for(scores, 0.01).sum())
    hi = int(flags_for(scores, 0.10).sum())
    assert lo <= hi, "stricter contamination must never flag more points"
    assert hi <= 400 * 0.10 + 5  # quantile threshold respects the budget


def test_threshold_is_quantile_and_strict():
    scores = np.arange(100, dtype=float)
    # np.percentile interpolates: position 0.98 * (100 - 1) = 97.02
    assert threshold_for(scores, 0.02) == pytest.approx(97.02)
    mask = flags_for(scores, 0.02)
    assert mask[98] and mask[99]
    assert not mask[97], "strict > excludes points at or below the quantile"


def test_contamination_is_clamped():
    scores = np.arange(100, dtype=float)
    # out-of-range values are clipped rather than crashing
    assert threshold_for(scores, contamination=0.0) == pytest.approx(threshold_for(scores, 1e-6))
    assert threshold_for(scores, contamination=9.0) == pytest.approx(threshold_for(scores, 0.5))


def test_level_shift_is_detected():
    # steady 100ms then jumps to 200ms and stays: the shift region must be flagged
    values = [100.0] * 120 + [200.0] * 80
    rng = np.random.default_rng(3)
    values = [v + float(rng.normal(0, 1.5)) for v in values]
    flagged = {a.index for a in detect(values)}
    assert any(i >= 120 for i in flagged), "post-shift plateau must contain flags"
    assert sum(i < 120 for i in flagged) <= 10, "healthy baseline must stay mostly clean"


def test_nan_values_do_not_crash():
    values = make_series()
    values[10] = float("nan")
    values[11] = float("inf")
    anomalies = detect(np.asarray(values))
    assert isinstance(anomalies, list)


def test_default_contamination_exported():
    assert 0 < CONTAMINATION < 0.1
