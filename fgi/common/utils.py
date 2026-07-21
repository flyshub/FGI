import numpy as np
import pandas as pd


def rolling_percentile(series: pd.Series, window: int = 1260) -> pd.Series:
    def percentile_rank(x):
        current = x.iloc[-1]
        return np.sum(x <= current) / len(x)

    return series.rolling(window=window, min_periods=252).apply(percentile_rank, raw=False)


def zscore(series: pd.Series, window: int = 1260, min_periods: int = 252) -> pd.Series:
    mean = series.rolling(window=window, min_periods=min_periods).mean()
    std = series.rolling(window=window, min_periods=min_periods).std()
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


def apply_consistency_adjustment(fgi_raw: float, indicator_scores: list) -> tuple[float, float]:
    import numpy as np
    if len(indicator_scores) < 4:
        return fgi_raw, 0.0
    arr = np.array(indicator_scores, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 4:
        return fgi_raw, 0.0
    median = np.median(arr)
    mad = float(np.median(np.abs(arr - median)))
    return fgi_raw, mad


def adjust_fgi_with_mad_pct(fgi_raw: float, mad: float, mad_pct: float) -> float:
    if fgi_raw < 15 or fgi_raw > 85:
        lam = min(mad_pct * 0.6, 0.3)
        return fgi_raw * (1.0 - lam) + 50.0 * lam
    return fgi_raw


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
