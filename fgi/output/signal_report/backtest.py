"""回测 v2：Rank IC、10 档分层、逆情绪 DCA（ADR-0003）。纯计算。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_rank_ic(
    df: pd.DataFrame, indicator_col: str = "FGI_final", horizon: int = 20
) -> dict | None:
    """Compute Spearman Rank IC between an indicator and forward market return.

    Args:
        df: DataFrame with columns ['close', indicator_col].
        horizon: forward trading days for return calculation.

    Returns:
        {ic, n, mean, std, ir, win_rate, indicator, horizon} or None if insufficient data.
    """
    if indicator_col not in df.columns or df[indicator_col].dropna().empty:
        return None
    working = df.copy()
    working["forward_return"] = working["close"].shift(-horizon) / working["close"] - 1.0
    working = working.dropna(subset=["forward_return", indicator_col])
    if len(working) < 30:
        return None
    # Split into 20-day rolling sub-windows for IC statistics
    ic_values = []
    window_size = 20
    for i in range(len(working) - window_size + 1):
        win = working.iloc[i : i + window_size]
        if len(win) < 10:
            continue
        val = win[indicator_col].corr(win["forward_return"], method="spearman")
        if not pd.isna(val):
            ic_values.append(float(val))
    if not ic_values:
        return None
    ic_series = np.array(ic_values)
    mean_ic = float(np.mean(ic_series))
    std_ic = float(np.std(ic_series, ddof=1))
    ir = mean_ic / std_ic if std_ic > 0 else 0.0
    win_rate = float(np.mean(ic_series > 0))
    return {
        "ic": float(working[indicator_col].corr(working["forward_return"], method="spearman")),
        "n": len(working),
        "mean": mean_ic,
        "std": std_ic,
        "ir": ir,
        "win_rate": win_rate,
        "indicator": indicator_col,
        "horizon": horizon,
        "bonferroni_threshold": 0.05 / 36,  # spec 5.3: α/36 = 0.0014
    }


def compute_rolling_ic_window(
    df: pd.DataFrame,
    indicator_col: str = "FGI_final",
    horizon: int = 20,
    half_year: int = 126,
    window_years: int = 3,
) -> list:
    """Compute rolling Rank IC over semi-annual windows using trailing 3-year data.

    Returns a list of {date, ic, n, mean, std, ir, win_rate} dicts, one per
    semi-annual evaluation point. Skips points with fewer than `min_valid_obs`
    valid observations within the window.
    """
    results = []
    working = df.copy()
    working["forward_return"] = working["close"].shift(-horizon) / working["close"] - 1.0
    working = working.dropna(subset=["forward_return", indicator_col])
    window_size = 252 * window_years  # 756 trading days
    min_valid_obs = 252  # spec: skip if fewer than 252 valid obs in window
    step = half_year
    n = len(working)
    for end in range(window_size, n, step):
        window_df = working.iloc[end - window_size : end]
        if len(window_df) < min_valid_obs:
            continue
        ic = float(window_df[indicator_col].corr(window_df["forward_return"], method="spearman"))
        # Compute sub-window statistics within this rolling window
        ic_vals = []
        sub_win = 20
        for i in range(len(window_df) - sub_win + 1):
            s = window_df.iloc[i : i + sub_win]
            if len(s) < 10:
                continue
            val = s[indicator_col].corr(s["forward_return"], method="spearman")
            if not pd.isna(val):
                ic_vals.append(float(val))
        win_mean = float(np.mean(ic_vals)) if ic_vals else ic
        win_std = float(np.std(ic_vals, ddof=1)) if len(ic_vals) > 1 else None
        win_ir = win_mean / win_std if (win_std and win_std > 0) else 0.0
        win_wr = float(np.mean(np.array(ic_vals) > 0)) if ic_vals else 0.5
        results.append(
            {
                "date": str(window_df["date"].iloc[-1]),
                "ic": ic,
                "n": len(window_df),
                "mean": win_mean,
                "std": win_std,
                "ir": win_ir,
                "win_rate": win_wr,
            }
        )
    return results


def layer_backtest_10(df: pd.DataFrame, horizons: list | None = None) -> dict:
    """10-quantile layer backtest: FGI decile × forward market returns.

    Returns {horizon: [{layer, n, mean_return, win_rate}]} keyed by horizon.
    """
    horizons = horizons or [5, 20, 60]
    working = df.copy()
    working = working.sort_values("date").reset_index(drop=True)
    for h in horizons:
        working[f"forward_{h}"] = working["close"].shift(-h) / working["close"] - 1.0

    result = {}
    for h in horizons:
        col = f"forward_{h}"
        valid = working.dropna(subset=[col, "FGI_final"]).copy()
        if len(valid) < 10:
            result[h] = []
            continue
        valid["layer"] = pd.qcut(valid["FGI_final"], 10, labels=False, duplicates="drop")
        layers = []
        for layer in range(10):
            subset = valid[valid["layer"] == layer]
            if len(subset) == 0:
                continue
            rets = subset[col].values
            layers.append(
                {
                    "layer": layer + 1,  # 1-indexed
                    "n": len(subset),
                    "mean_return": float(np.mean(rets)),
                    "win_rate": float(np.mean(rets > 0)),
                }
            )
        result[h] = layers
    return result


def simulate_dca(
    df: pd.DataFrame, base_amount: float = 10000, indicator_col: str = "FGI_final"
) -> dict:
    """Simulate contrarian DCA: monthly investment scaled by (1 - FGI/100) × 2.

    Buys on the first trading day of each month. Benchmark: equal monthly DCA.

    Returns {dca_return, dca_annualized, benchmark_return, benchmark_annualized,
             dca_max_drawdown, benchmark_max_drawdown, dca_sharpe, benchmark_sharpe,
             n_months}
    """
    working = df.copy()
    working["_dt"] = pd.to_datetime(working["date"])
    working["_year"] = working["_dt"].dt.year
    working["_month"] = working["_dt"].dt.month
    # First trading day of each month
    monthly = working.groupby(["_year", "_month"]).first().reset_index(drop=True)
    monthly = monthly.dropna(subset=["close", indicator_col])
    if len(monthly) < 12:
        return {"error": "Insufficient data (need >= 12 months)"}

    fgi_vals = monthly[indicator_col].values
    close_vals = monthly["close"].values

    # DCA: amount = base × (1 - FGI/100) × 2
    amounts = base_amount * (1.0 - fgi_vals / 100.0) * 2.0
    shares = amounts / close_vals
    total_shares_dca = np.cumsum(shares)
    final_value_dca = total_shares_dca[-1] * close_vals[-1]
    total_invested_dca = np.sum(amounts)
    total_return_dca = (final_value_dca / total_invested_dca) - 1.0
    n_trading_days = len(working)
    n_years = n_trading_days / 252.0

    # Benchmark: equal monthly DCA
    bench_shares = base_amount / close_vals
    total_shares_bench = np.cumsum(bench_shares)
    final_value_bench = total_shares_bench[-1] * close_vals[-1]
    total_invested_bench = base_amount * len(close_vals)
    total_return_bench = (final_value_bench / total_invested_bench) - 1.0

    # Annualized
    dca_annualized = (1.0 + total_return_dca) ** (1.0 / max(n_years, 0.5)) - 1.0
    bench_annualized = (1.0 + total_return_bench) ** (1.0 / max(n_years, 0.5)) - 1.0

    # Max drawdown
    dca_portfolio = total_shares_dca * close_vals
    bench_portfolio = total_shares_bench * close_vals

    def _max_drawdown(series):
        peak = np.maximum.accumulate(series)
        drawdown = (series - peak) / peak
        return float(np.min(drawdown))

    dca_mdd = _max_drawdown(dca_portfolio)
    bench_mdd = _max_drawdown(bench_portfolio)

    # Sharpe (monthly returns annualized)
    dca_monthly_ret = np.diff(dca_portfolio) / dca_portfolio[:-1]
    bench_monthly_ret = np.diff(bench_portfolio) / bench_portfolio[:-1]
    dca_sharpe = (
        float(np.mean(dca_monthly_ret) / np.std(dca_monthly_ret, ddof=1) * np.sqrt(12))
        if np.std(dca_monthly_ret, ddof=1) > 0
        else 0.0
    )
    bench_sharpe = (
        float(np.mean(bench_monthly_ret) / np.std(bench_monthly_ret, ddof=1) * np.sqrt(12))
        if np.std(bench_monthly_ret, ddof=1) > 0
        else 0.0
    )

    return {
        "n_months": len(monthly),
        "dca_total_return": float(total_return_dca),
        "dca_annualized": float(dca_annualized),
        "benchmark_total_return": float(total_return_bench),
        "benchmark_annualized": float(bench_annualized),
        "dca_max_drawdown": dca_mdd,
        "benchmark_max_drawdown": bench_mdd,
        "dca_sharpe": dca_sharpe,
        "benchmark_sharpe": bench_sharpe,
    }
