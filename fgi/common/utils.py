from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_percentile(series: pd.Series, window: int = 1260) -> pd.Series:
    def percentile_rank(x):
        current = x.iloc[-1]
        if pd.isna(current):
            return np.nan
        x = x.dropna()
        n = len(x)
        if n < 2:
            return np.nan
        rank = np.sum(x <= current)
        return (rank - 1) / (n - 1)

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


def calculate_fgi(dimension_scores: dict):
    """Weighted composite. Dimensions whose score is None (fully missing) are
    excluded and the remaining dimension weights renormalized proportionally.
    Returns None when every dimension is missing, or when fewer than 2 dimensions
    have valid scores (insufficient coverage for a meaningful composite)."""
    weights = {"momentum": 0.25, "sentiment": 0.25, "valuation": 0.25, "funding": 0.25}
    available = [
        (dim, dimension_scores[dim])
        for dim in weights
        if dimension_scores.get(dim) is not None and not pd.isna(dimension_scores[dim])
    ]
    if not available or len(available) < 2:
        return None
    total_weight = sum(weights[dim] for dim, _ in available)
    return sum(score * weights[dim] / total_weight for dim, score in available)


def extract_indicator_score(result: dict, name: str):
    """Extract an indicator score from a calculator result dict.

    Looks up keys in order: 'score', canonical name, lower-cased name.
    None or NaN values count as missing; 0.0 is valid.
    """
    for key in ("score", name, name.lower()):
        score = result.get(key)
        if score is None:
            continue
        try:
            if pd.isna(score):
                continue
        except (TypeError, ValueError):
            # Non-numeric scalar — accept as-is (caller's responsibility)
            pass
        return score
    return None


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


def calculate_health_score(status_df: pd.DataFrame, correlation_exceed_rate: float = 0.0) -> float:
    total_indicators = len(status_df)
    if total_indicators == 0:
        return 0

    normal_count = len(status_df[status_df["status"] == "normal"])
    degraded_count = len(status_df[status_df["status"] == "degraded"])

    normal_ratio = normal_count / total_indicators
    degraded_ratio = degraded_count / total_indicators

    health_score = (normal_ratio * 50) + ((1 - degraded_ratio) * 30) + ((1 - correlation_exceed_rate) * 20)
    return min(100, max(0, health_score))


def calculate_correlation_exceed_rate(db, date: str, lookback: int = 60) -> float:
    """Compute fraction of within-dimension indicator-score pairs exceeding 0.75 correlation."""
    import numpy as np
    dims = {
        "momentum": ["M1", "M2", "M3", "M4"],
        "sentiment": ["S2", "S3"],
        "valuation": ["V1", "V2"],
        "funding": ["F1", "F2", "F3"],
    }
    try:
        from datetime import datetime, timedelta
        start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=lookback * 2)).strftime("%Y-%m-%d")
        scores = db.get_scores(start, date)
        if scores is None or len(scores) < 20:
            return 0.0
        total_pairs = 0
        exceeding = 0
        for cols in dims.values():
            available = [c for c in cols if c in scores.columns]
            for i in range(len(available)):
                for j in range(i + 1, len(available)):
                    a = scores[available[i]].dropna()
                    b = scores[available[j]].dropna()
                    common = a.index.intersection(b.index)
                    if len(common) < 20:
                        continue
                    corr = abs(a.loc[common].corr(b.loc[common]))
                    total_pairs += 1
                    if corr > 0.75:
                        exceeding += 1
        if total_pairs == 0:
            return 0.0
        return exceeding / total_pairs
    except Exception:
        return 0.0
