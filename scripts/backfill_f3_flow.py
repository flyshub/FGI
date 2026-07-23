"""Backfill f3_industry_net_flow from stock_market_fund_flow (历史主力净流入).

Issue #42: pre-fix fetch_industry_fund_flow used the real-time endpoint
stock_fund_flow_industry(symbol='即时'), which only returns the current day's
snapshot. All 2808 historical rows were polluted with only 2 distinct values.

This script:
1. Deletes the polluted f3_industry_net_flow history.
2. Re-fetches 120 days of history from stock_market_fund_flow.
3. Inserts the new values.

Note: stock_market_fund_flow only provides 120 days of trailing history.
Dates older than 120 days will have no real f3_industry_net_flow and will fall
back to proxy (price_change × volume) at compute time. This is by design.

Usage: python3.12 -m scripts.backfill_f3_flow
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak
import pandas as pd

from fgi.storage.database import Database
from fgi.config.settings import DB_PATH


def main():
    print("Fetching historical 主力净流入 from stock_market_fund_flow...", flush=True)
    df = ak.stock_market_fund_flow()
    df = df[["日期", "主力净流入-净额"]].rename(columns={
        "日期": "date",
        "主力净流入-净额": "net_flow",
    })
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["net_flow"] = pd.to_numeric(df["net_flow"], errors="coerce")
    df = df.dropna(subset=["net_flow"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        print("ERROR: stock_market_fund_flow returned no rows. Aborting.", flush=True)
        return
    print(
        f"Got {len(df)} rows, range {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}",
        flush=True,
    )

    db = Database(DB_PATH)
    db.connect()

    n_old = db.count_rows("raw_data", "indicator='f3_industry_net_flow'")
    print(
        f"Pre-state: {n_old} polluted rows (bug evidence: only 2 distinct values pre-fix)",
        flush=True,
    )

    deleted = db.delete_raw_data("f3_industry_net_flow")
    print(f"Deleted {n_old} polluted rows (rowcount={deleted})", flush=True)

    for _, row in df.iterrows():
        db.upsert_raw_data(row["date"], "f3_industry_net_flow", float(row["net_flow"]))
    db.commit()

    n_new = db.count_rows("raw_data", "indicator='f3_industry_net_flow'")
    stats = db.get_raw_value_stats("f3_industry_net_flow")
    if stats:
        min_v, max_v, avg_v = stats
        print(
            f"\nDONE: {n_new} rows\n"
            f"  range: {min_v:.2e} ~ {max_v:.2e}  avg={avg_v:.2e}",
            flush=True,
        )
    else:
        print(f"\nDONE: {n_new} rows", flush=True)
    db.close()


if __name__ == "__main__":
    main()
