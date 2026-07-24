"""Backfill PB history (v1_pb) and pre-compute PE/PB 5y rolling percentiles.

PB raw data is required by the decision matrix module (fgi/output/decision_matrix.py).
The 5-year rolling percentiles for PE and PB are pre-computed and stored as
v1_pe_percentile / v1_pb_percentile raw_data for fast O(1) lookup at runtime.

Usage:
    python3.12 -m scripts.backfill_pb_pe_percentile
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import akshare as ak
import pandas as pd

from fgi.storage.database import Database
from fgi.config.settings import DB_PATH

ROLLING_WINDOW = 5 * 252  # 5 years of trading days


def rolling_pct(series: pd.Series, window: int = ROLLING_WINDOW) -> pd.Series:
    """Vectorized rolling percentile: for each row, what fraction of the
    trailing `window` values (inclusive) are <= this row's value."""
    vals = series.to_numpy(dtype=float)
    n = len(vals)
    out = np.full(n, np.nan)
    for i in range(n):
        lo = max(0, i - window + 1)
        w = vals[lo:i + 1]
        w = w[~np.isnan(w)]
        if len(w) < 252 or np.unique(w).size <= 1:
            out[i] = np.nan
            continue
        # percentile rank of vals[i] within w
        out[i] = np.nanmean(w <= vals[i])
    return pd.Series(out, index=series.index)


def main():
    print("Fetching PB and PE history from akshare...", flush=True)
    pb_df = ak.stock_index_pb_lg(symbol="沪深300")
    pe_df = ak.stock_index_pe_lg(symbol="沪深300")
    print(f"PB: {len(pb_df)} rows; PE: {len(pe_df)} rows", flush=True)

    # normalize PB
    pb_df = pb_df.rename(columns={"日期": "date", "市净率": "pb"})
    pb_df["date"] = pd.to_datetime(pb_df["date"]).dt.strftime("%Y-%m-%d")
    pb_df["pb"] = pd.to_numeric(pb_df["pb"], errors="coerce")
    pb_df = pb_df[["date", "pb"]].dropna(subset=["pb"]).sort_values("date").reset_index(drop=True)

    # normalize PE (滚动市盈率 — same field as V1)
    pe_df = pe_df.rename(columns={"日期": "date", "滚动市盈率": "pe"})
    pe_df["date"] = pd.to_datetime(pe_df["date"]).dt.strftime("%Y-%m-%d")
    pe_df["pe"] = pd.to_numeric(pe_df["pe"], errors="coerce")
    pe_df = pe_df[["date", "pe"]].dropna(subset=["pe"]).sort_values("date").reset_index(drop=True)

    # merge on date
    df = pd.merge(pe_df, pb_df, on="date", how="inner")
    print(f"Merged: {len(df)} rows, range {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}", flush=True)

    # compute 5y rolling percentiles
    print("Computing 5y rolling percentiles (this may take ~30s)...", flush=True)
    df["pe_pct"] = rolling_pct(df["pe"])
    df["pb_pct"] = rolling_pct(df["pb"])
    valid = df.dropna(subset=["pe_pct", "pb_pct"])
    print(f"Valid percentile rows: {len(valid)}", flush=True)

    db = Database(DB_PATH)
    db.connect()

    # write PB raw
    n_existing_pb = db.count_rows("raw_data", "indicator='v1_pb'")
    if n_existing_pb:
        db.delete_raw_data("v1_pb")
        print(f"Deleted {n_existing_pb} old v1_pb rows", flush=True)
    inserted = 0
    for _, row in df.iterrows():
        db.upsert_raw_data(row["date"], "v1_pb", float(row["pb"]))
        inserted += 1
        if inserted % 1000 == 0:
            db.commit()
    db.commit()
    print(f"v1_pb: inserted {inserted} rows", flush=True)

    # write PE percentile raw
    for key in ("v1_pe_percentile", "v1_pb_percentile"):
        n_old = db.count_rows("raw_data", f"indicator='{key}'")
        if n_old:
            db.delete_raw_data(key)
            print(f"Deleted {n_old} old {key} rows", flush=True)

    inserted_pe = inserted_pb = 0
    for _, row in valid.iterrows():
        db.upsert_raw_data(row["date"], "v1_pe_percentile", float(row["pe_pct"]))
        db.upsert_raw_data(row["date"], "v1_pb_percentile", float(row["pb_pct"]))
        inserted_pe += 1
        inserted_pb += 1
        if inserted_pe % 1000 == 0:
            db.commit()
    db.commit()
    print(f"v1_pe_percentile: inserted {inserted_pe} rows", flush=True)
    print(f"v1_pb_percentile: inserted {inserted_pb} rows", flush=True)

    # summary
    pb_stats = db.get_raw_value_stats("v1_pb")
    pe_pct_stats = db.get_raw_value_stats("v1_pe_percentile")
    pb_pct_stats = db.get_raw_value_stats("v1_pb_percentile")
    print(f"\nv1_pb stats: min={pb_stats[0]:.4f} max={pb_stats[1]:.4f} avg={pb_stats[2]:.4f}" if pb_stats else "v1_pb: no stats")
    print(f"v1_pe_percentile stats: min={pe_pct_stats[0]:.3f} max={pe_pct_stats[1]:.3f} avg={pe_pct_stats[2]:.3f}" if pe_pct_stats else "v1_pe_percentile: no stats")
    print(f"v1_pb_percentile stats: min={pb_pct_stats[0]:.3f} max={pb_pct_stats[1]:.3f} avg={pb_pct_stats[2]:.3f}" if pb_pct_stats else "v1_pb_percentile: no stats")
    db.close()
    print("\nDONE.", flush=True)


if __name__ == "__main__":
    main()
