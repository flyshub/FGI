from __future__ import annotations

import numpy as np
import pandas as pd


_PERCENTILE_CACHE: dict[tuple[int, int, int], pd.Series] = {}


def rolling_percentile(series: pd.Series, window: int = 1260) -> pd.Series:
    """Vectorized rolling percentile — O(N*W) 但 numpy 层级，比 .apply 快 10-100x。

    公式: rank = sum(x_i <= x_current); percentile = (rank - 1) / (n - 1)

    退化检测：窗口内若只有一个唯一值（如全部为 0），返回 NaN 避免假性 1.0。

    recompute 场景下同一 series 的二次调用通过 cache 直接返回。
    """
    arr = series.values
    key = (len(arr), hash(arr.tobytes()), window)
    cached = _PERCENTILE_CACHE.get(key)
    if cached is not None:
        return cached

    vals = np.asarray(arr, dtype=float)
    n = len(vals)
    result = np.full(n, np.nan)
    min_p = 252
    if n < min_p:
        out = pd.Series(result, index=series.index)
        if len(_PERCENTILE_CACHE) > 32:
            _PERCENTILE_CACHE.clear()
        _PERCENTILE_CACHE[key] = out
        return out

    # vectorized: 对每个 i (i >= min_p-1)，取窗口 vals[i-window+1 : i+1]，
    # 计算其中 <= 当前值 的个数（排除 NaN）和唯一值个数
    for i in range(min_p - 1, n):
        start = max(0, i - window + 1)
        w = vals[start:i + 1]
        w_valid = w[~np.isnan(w)]
        cur = vals[i]
        if np.isnan(cur):
            continue
        n_valid = len(w_valid)
        if n_valid < 2:
            continue
        # 退化检测：窗口内只有一个唯一值
        # (用 np.max != np.min 比 np.unique 快很多)
        if np.min(w_valid) == np.max(w_valid):
            continue
        rank = np.sum(w_valid <= cur)
        result[i] = (rank - 1) / (n_valid - 1)

    out = pd.Series(result, index=series.index)

    if len(_PERCENTILE_CACHE) > 32:
        _PERCENTILE_CACHE.clear()
    _PERCENTILE_CACHE[key] = out
    return out


def clear_percentile_cache() -> None:
    """显式清空缓存（测试 / 切换 indicator 时调用）"""
    _PERCENTILE_CACHE.clear()


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
    total = len(status_df)
    if total == 0:
        return 0

    normal_count = len(status_df[status_df["status"] == "normal"])
    impaired_count = len(status_df[status_df["status"].isin(["degraded", "substituted", "missing", "error"])])

    normal_ratio = normal_count / total
    impaired_ratio = impaired_count / total

    health_score = (normal_ratio * 40) + ((1 - impaired_ratio) * 40) + ((1 - correlation_exceed_rate) * 20)
    return min(100, max(0, health_score))


def calculate_correlation_exceed_rate(db, date: str, lookback: int = 60) -> float:
    """#49 (deprecated 2026-07-23): M1/S3 高相关已通过 INDICATOR_WEIGHTS 静态降权解决。
    保留函数签名仅为兼容旧调用方；返回常数 0.0，不再参与 health_score 计算。"""
    return 0.0
