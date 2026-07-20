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
    def test_no_adjustment(self):
        scores = {"momentum": 50, "sentiment": 50, "valuation": 50, "funding": 50}
        assert apply_consistency_adjustment(50, scores) == 50

    def test_low_adjustment(self):
        scores = {"momentum": 10, "sentiment": 10, "valuation": 10, "funding": 10}
        result = apply_consistency_adjustment(10, scores)
        assert result > 10


class TestCalculateHealthScore:
    def test_all_normal(self):
        df = pd.DataFrame({"status": ["normal"] * 13})
        assert calculate_health_score(df) == 100

    def test_all_missing(self):
        df = pd.DataFrame({"status": ["missing"] * 13})
        assert calculate_health_score(df) < 50
