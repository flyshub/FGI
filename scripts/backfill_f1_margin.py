"""Backfill f1_margin_balance from Eastmoney (沪+深合计) to replace historical SSE (沪市) values.

This is part of V3.8.3 F1 caliber unification: pre-V3.8.3 raw f1_margin_balance
is SSE 沪市 only (~1.36e12 元); switch to Eastmoney 沪深合计 (~2.7e12 元) and
overwrite full history so percentile is computed on a consistent caliber.

Usage: python3.12 -m scripts.backfill_f1_margin
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak
import pandas as pd

from fgi.storage.database import Database
from fgi.config.settings import DB_PATH


def main():
    print("Fetching Eastmoney margin history...", flush=True)
    df = ak.stock_margin_account_info()
    df = df.rename(columns={"日期": "date", "融资余额": "margin_balance"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["margin_balance"] = pd.to_numeric(df["margin_balance"], errors="coerce") * 1e8  # 亿→元
    df = df.dropna(subset=["margin_balance"]).sort_values("date").reset_index(drop=True)
    print(f"Got {len(df)} rows, range {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}", flush=True)

    db = Database(DB_PATH)
    db.connect()
    # 删除旧 SSE 历史值
    n_old = db._conn.execute(
        "SELECT COUNT(*) FROM raw_data WHERE indicator = ?", ("f1_margin_balance",)
    ).fetchone()[0]
    db._conn.execute(
        "DELETE FROM raw_data WHERE indicator = ?", ("f1_margin_balance",)
    )
    print(f"Deleted {n_old} old SSE f1_margin_balance rows", flush=True)

    # 写入东财新值
    inserted = 0
    for _, row in df.iterrows():
        db.upsert_raw_data(row["date"], "f1_margin_balance", float(row["margin_balance"]))
        inserted += 1
        if inserted % 500 == 0:
            db.commit()
            print(f"  {inserted}/{len(df)}", flush=True)
    db.commit()

    # 验证
    n_new = db._conn.execute(
        "SELECT COUNT(*) FROM raw_data WHERE indicator = ?", ("f1_margin_balance",)
    ).fetchone()[0]
    min_v, max_v, avg_v = db._conn.execute(
        "SELECT MIN(value), MAX(value), AVG(value) FROM raw_data WHERE indicator = ?",
        ("f1_margin_balance",),
    ).fetchone()
    print(f"\nDONE: {n_new} rows, value range {min_v:.2e} ~ {max_v:.2e}, avg {avg_v:.2e}", flush=True)
    db.close()


if __name__ == "__main__":
    main()
