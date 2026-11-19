"""区间划分、前瞻收益与逐区间统计——纯计算，不渲染、不取数之外的副作用。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fgi.storage.database import Database

ZONES = [
    ("极度恐惧", 0.0, 20.0),
    ("恐惧", 20.0, 40.0),
    ("中性", 40.0, 60.0),
    ("贪婪", 60.0, 80.0),
    ("极度贪婪", 80.0, 100.0),
]

HORIZONS = [5, 20, 60]

SAMPLE_SPLIT_YEAR = 2023  # <=2022 in-sample, >=2023 out-of-sample


def assign_zone(fgi: float | None) -> str:
    """Map an FGI value to its zone label. None/NaN → '未知'."""
    if fgi is None:
        return "未知"
    try:
        if pd.isna(fgi):
            return "未知"
    except (TypeError, ValueError):
        pass
    for label, lo, hi in ZONES:
        if lo <= fgi < hi:
            return label
    return "极度贪婪"  # fgi == 100.0 (edge case for the last zone's upper bound)


def compute_forward_returns(df: pd.DataFrame, horizons: list[int] | None = None) -> pd.DataFrame:
    """Add forward_N columns: (close[t+N] / close[t] - 1) for each horizon.

    The last N rows for each horizon get NaN (no future data).
    """
    horizons = horizons or HORIZONS
    df = df.copy()
    df = df.sort_values("date").reset_index(drop=True)
    for h in horizons:
        df[f"forward_{h}"] = df["close"].shift(-h) / df["close"] - 1.0
    return df


def compute_zone_stats(df: pd.DataFrame, horizon: int) -> list[dict]:
    """Compute per-zone statistics for a single forward horizon.

    Returns one dict per zone with keys:
    zone, n, mean, std, ci_lower (None if n<30), ci_upper (None if n<30), win_rate
    """
    col = f"forward_{horizon}"
    valid = df[df[col].notna()].copy()
    stats = []
    for label, lo, hi in ZONES:
        subset = valid[(valid["FGI_final"] >= lo) & (valid["FGI_final"] < hi)]
        n = len(subset)
        returns = subset[col].values
        entry = {
            "zone": label,
            "n": n,
            "mean": float(np.mean(returns)) if n > 0 else None,
            "std": float(np.std(returns, ddof=1)) if n > 1 else None,
            "ci_lower": None,
            "ci_upper": None,
            "win_rate": float(np.mean(returns > 0)) if n > 0 else None,
        }
        if n >= 30 and entry["std"] is not None and entry["std"] > 0:
            se = entry["std"] / np.sqrt(n)
            entry["ci_lower"] = float(entry["mean"] - 1.96 * se)
            entry["ci_upper"] = float(entry["mean"] + 1.96 * se)
        stats.append(entry)
    return stats


class SignalReportEngine:
    """Core engine: loads data, computes forward returns, produces per-zone stats."""

    def __init__(self, db: Database):
        self._db = db

    def load_data(self) -> pd.DataFrame:
        """Load FGI_final from scores_daily and Shanghai Composite close from raw_data.

        优先用 f3_proxy_close（F3 calculator 每日写入），回退到 m3_close（仅 backfill 写入）。
        """
        scores = self._db.get_scores("2009-01-01", "2099-12-31")
        close_df = self._db.get_raw_data("f3_proxy_close", "2009-01-01", "2099-12-31")
        if close_df.empty:
            close_df = self._db.get_raw_data("m3_close", "2009-01-01", "2099-12-31")

        if scores is None or scores.empty:
            return pd.DataFrame(columns=["date", "FGI_final", "close"])
        if close_df is None or close_df.empty:
            return pd.DataFrame(columns=["date", "FGI_final", "close"])

        scores = scores[["date", "FGI_final"]].dropna(subset=["FGI_final"]).copy()
        close_df = close_df.rename(columns={"value": "close"})[["date", "close"]].copy()

        merged = scores.merge(close_df, on="date", how="inner")
        merged = merged.sort_values("date").reset_index(drop=True)
        merged["date"] = merged["date"].astype(str)
        return merged

    def _compute_split(self, df: pd.DataFrame) -> dict:
        """Compute stats for a single data split across all horizons."""
        result = {}
        df = compute_forward_returns(df, horizons=HORIZONS)
        for h in HORIZONS:
            result[h] = compute_zone_stats(df, horizon=h)
        return result

    def run(self) -> dict:
        """Run the full pipeline and return structured results.

        Returns dict with:
        - metadata: {start_date, end_date, total_days, benchmark, fgi_definition}
        - stats: {horizon: [zone_stats]}
        - in_sample: same structure for 2015-2022
        - out_sample: same structure for 2023-2026
        """
        df = self.load_data()
        if df.empty:
            return {
                "metadata": {"start_date": None, "end_date": None, "total_days": 0},
                "stats": {},
                "in_sample": None,
                "out_sample": None,
            }

        # Parse sample splits
        df["_year"] = pd.to_datetime(df["date"]).dt.year
        in_df = df[df["_year"] < SAMPLE_SPLIT_YEAR]
        out_df = df[df["_year"] >= SAMPLE_SPLIT_YEAR]

        # Full-sample stats (combined)
        stats = self._compute_split(df)

        metadata = {
            "start_date": str(df["date"].iloc[0]),
            "end_date": str(df["date"].iloc[-1]),
            "total_days": len(df),
            "benchmark": "上证综指 (m3_close)",
            "fgi_definition": "FGI_final",
            "in_sample_range": f"{in_df['date'].iloc[0]} ~ {in_df['date'].iloc[-1]}"
            if len(in_df) > 0
            else "N/A",
            "out_sample_range": f"{out_df['date'].iloc[0]} ~ {out_df['date'].iloc[-1]}"
            if len(out_df) > 0
            else "N/A",
        }

        return {
            "metadata": metadata,
            "stats": stats,
            "in_sample": self._compute_split(in_df) if len(in_df) > 0 else None,
            "out_sample": self._compute_split(out_df) if len(out_df) > 0 else None,
        }


def get_zone_for_fgi(fgi: float | None) -> str:
    """Map an FGI value to its five-zone label (aligned with signal report zones)."""
    return assign_zone(fgi)
