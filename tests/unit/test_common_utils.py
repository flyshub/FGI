import pytest
from fgi.common.utils import (
    rolling_percentile,
    zscore,
    sigmoid,
    normalized_diff,
    winsorize,
    mad_filter,
    calculate_fgi,
    apply_consistency_adjustment,
    calculate_health_score,
)
import pandas as pd
import numpy as np


class TestRollingPercentile:
    def test_basic(self):
        series = pd.Series(range(1, 301))
        result = rolling_percentile(series, window=252)
        assert result.iloc[-1] == 1.0

    def test_with_nan(self):
        series = pd.Series([np.nan] * 252 + [1, 2, 3, 4, 5])
        result = rolling_percentile(series, window=252)
        assert pd.isna(result.iloc[0])


class TestZscore:
    def test_basic(self):
        series = pd.Series([1, 2, 3, 4, 5] * 252)
        result = zscore(series, window=252)
        assert abs(result.iloc[-1]) < 2


class TestSigmoid:
    def test_positive(self):
        result = sigmoid(pd.Series([2]))
        assert result.iloc[0] > 50

    def test_negative(self):
        result = sigmoid(pd.Series([-2]))
        assert result.iloc[0] < 50

    def test_zero(self):
        result = sigmoid(pd.Series([0]))
        assert result.iloc[0] == 50


class TestNormalizedDiff:
    def test_basic(self):
        result = normalized_diff(pd.Series([70]), pd.Series([30]))
        assert result.iloc[0] == 0.4


class TestCalculateFgi:
    def test_equal_weights(self):
        scores = {"momentum": 50, "sentiment": 50, "valuation": 50, "funding": 50}
        assert calculate_fgi(scores) == 50

    def test_extreme_greed(self):
        scores = {"momentum": 100, "sentiment": 100, "valuation": 100, "funding": 100}
        assert calculate_fgi(scores) == 100


class TestApplyConsistencyAdjustment:
    """V3.8: MAD-based consistency adjustment"""

    def test_mad_computation(self):
        scores = [50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50]
        _, mad = apply_consistency_adjustment(50, scores)
        assert mad == 0.0

    def test_mad_with_variance(self):
        scores = [10, 20, 80, 90, 30, 70, 40, 60, 25, 75, 35, 65]
        _, mad = apply_consistency_adjustment(50, scores)
        assert mad > 0

    def test_mad_adjustment_extreme(self):
        from fgi.common.utils import adjust_fgi_with_mad_pct
        result_low = adjust_fgi_with_mad_pct(10, 0.3, 0.5)
        assert result_low > 10  # extreme fear pushed toward 50
        assert result_low < 50
        result = adjust_fgi_with_mad_pct(50, 0.3, 0.5)
        assert result == 50  # neutral, no adjustment


class TestCalculateHealthScore:
    def test_all_normal(self):
        df = pd.DataFrame({"status": ["normal"] * 13})
        assert calculate_health_score(df) == 100

    def test_all_missing(self):
        df = pd.DataFrame({"status": ["missing"] * 13})
        assert calculate_health_score(df) < 50
