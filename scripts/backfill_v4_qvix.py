"""V4 (QVIX 50ETF 期权隐含波动率) 历史数据回填脚本.

数据源：ak.index_option_50etf_qvix() — 历史 2015-02-09 起，每日更新。
raw_data key: v4_qvix (字段 close)
"""
import sys
from pathlib import Path

# 让 scripts/ 模块可被直接执行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak

from fgi.storage.database import Database


DB_PATH = "data/fgi.db"


def main():
    print("[V4 backfill] 拉取 ak.index_option_50etf_qvix()...")
    df = ak.index_option_50etf_qvix()
    if df is None or df.empty:
        print("[V4 backfill] ERROR: no data returned")
        return 1

    df["date"] = df["date"].astype(str)
    n_total = len(df)
    print(f"[V4 backfill] {n_total} rows, range {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

    db = Database(DB_PATH)
    with db:
        n_written = 0
        for _, row in df.iterrows():
            d = str(row["date"])
            close = float(row["close"])
            db.upsert_raw_data(d, "v4_qvix", close)
            n_written += 1
            if n_written % 500 == 0:
                db.commit()
                print(f"[V4 backfill] {n_written}/{n_total} ({d})")
        db.commit()
    print(f"[V4 backfill] DONE: {n_written}/{n_total} rows written to v4_qvix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
