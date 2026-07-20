import numpy as np
import pandas as pd


def rolling_percentile(series: pd.Series, window: int = 1260) -> pd.Series:
    def percentile_rank(x):
        current = x.iloc[-1]
        return np.sum(x <= current) / len(x)

    return series.rolling(window=window, min_periods=252).apply(percentile_rank, raw=False)


def zscore(series: pd.Series, window: int = 1260) -> pd.Series:
    mean = series.rolling(window=window, min_periods=252).mean()
    std = series.rolling(window=window, min_periods=252).std()
    return (series - mean) / std


def sigmoid(x: pd.Series) -> pd.Series:
    return 100 / (1 + np.exp(-x))


def normalized_diff(high_count: pd.Series, low_count: pd.Series) -> pd.Series:
    total = high_count + low_count
    return (high_count - low_count) / total


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    lower_bound = series.quantile(lower)
    upper_bound = series.quantile(upper)
    return series.clip(lower=lower_bound, upper=upper_bound)


def mad_filter(series: pd.Series, threshold: float = 5.0) -> pd.Series:
    median = series.median()
    mad = np.median(np.abs(series - median))
    modified_z = 0.6745 * (series - median) / mad
    return series[np.abs(modified_z) < threshold]


def calculate_fgi(dimension_scores: dict) -> float:
    weights = {"momentum": 0.25, "sentiment": 0.25, "valuation": 0.25, "funding": 0.25}
    raw_fgi = sum(dimension_scores[dim] * weights[dim] for dim in weights)
    return raw_fgi


def apply_consistency_adjustment(fgi: float, dimension_scores: dict) -> float:
    if fgi < 15 or fgi > 85:
        low_dims = [dim for dim, score in dimension_scores.items() if score < 30]
        high_dims = [dim for dim, score in dimension_scores.items() if score > 70]
        if fgi < 15 and low_dims:
            adjustment = sum(dimension_scores[dim] for dim in low_dims) * 0.05
            return fgi + adjustment
        elif fgi > 85 and high_dims:
            adjustment = sum(dimension_scores[dim] for dim in high_dims) * 0.05
            return fgi - adjustment
    return fgi


def calculate_health_score(status_df: pd.DataFrame) -> float:
    total_indicators = len(status_df)
    if total_indicators == 0:
        return 0
    
    normal_count = len(status_df[status_df["status"] == "normal"])
    degraded_count = len(status_df[status_df["status"] == "degraded"])
    missing_count = len(status_df[status_df["status"] == "missing"])
    
    normal_ratio = normal_count / total_indicators
    degraded_ratio = degraded_count / total_indicators
    missing_ratio = missing_count / total_indicators
    
    health_score = (normal_ratio * 50) + ((1 - degraded_ratio) * 30) + ((1 - missing_ratio) * 20)
    return min(100, max(0, health_score))
