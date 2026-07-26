"""Backfill f1_market_cap: 上海+深圳合计 (修正 V3.8 只取上海的 bug).

V3.8 首次引入 F1 时 fetch_market_cap 只取了市价总值-上海，
V3.8.3 改为沪深合计但未回填历史 raw_data。本脚本修正全历史 f1_market_cap。

Usage: python3 scripts/backfill_f1_market_cap.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak
import pandas as pd

from fgi.config.settings import DB_PATH
from fgi.storage.database import Database


def main():
    print("Fetching macro_china_stock_market_cap...", flush=True)
    df = ak.macro_china_stock_market_cap()
    df["date"] = df["数据日期"].str.extract(r"(\d{4})年(\d{2})月份") \
        .apply(lambda x: f"{x[0]}-{x[1]}-01", axis=1)
    sh = pd.to_numeric(df["市价总值-上海"], errors="coerce").fillna(0)
    sz = pd.to_numeric(df["市价总值-深圳"], errors="coerce").fillna(0)
    df["market_cap"] = sh + sz
    df = df[["date", "market_cap"]].dropna(subset=["market_cap"]).sort_values("date").reset_index(drop=True)
    print(f"Got {len(df)} months, range {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}", flush=True)

    db = Database(DB_PATH)
    db.connect()

    # 统计旧值
    n_old = db.count_raw_data_by_indicator("f1_market_cap")
    print(f"Old f1_market_cap rows: {n_old}", flush=True)

    # 修正：月度市场cap前向填充到交易日
    # 读取所有 f1_margin_balance 的交易日（融资余额是日频）
    margin_df = db.get_raw_data("f1_margin_balance", "1900-01-01", "2999-12-31")
    if margin_df is None or margin_df.empty:
        print("No f1_margin_balance data found, aborting", flush=True)
        return
    margin_dates = sorted(margin_df["date"].unique().tolist())

    # 用月度 cap 数据前向填充到每个交易日
    cap_map = dict(zip(df["date"], df["market_cap"], strict=True))
    cap_dates = sorted(cap_map.keys())

    updated = 0
    for md in margin_dates:
        # 找到 <= md 的最近一个月度 cap
        matched_cap = None
        for cd in reversed(cap_dates):
            if cd <= md:
                matched_cap = cap_map[cd]
                break
        if matched_cap is not None and matched_cap > 0:
            db.upsert_raw_data(md, "f1_market_cap", float(matched_cap))
            updated += 1
            if updated % 500 == 0:
                db.commit()
                print(f"  {updated}/{len(margin_dates)}", flush=True)

    db.commit()

    # 验证
    n_new = db.count_raw_data_by_indicator("f1_market_cap")
    stats = db.get_raw_value_stats("f1_market_cap")
    if stats:
        min_v, max_v, avg_v = stats
        print(f"\nDONE: {n_new} rows, value range {min_v:.2e} ~ {max_v:.2e}, avg {avg_v:.2e}", flush=True)
    else:
        print(f"\nDONE: {n_new} rows", flush=True)
    db.close()


if __name__ == "__main__":
    main()
