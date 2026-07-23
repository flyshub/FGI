"""单独回填 m4_volume（创业板指成交量）。

新浪 stock_zh_index_daily 稳定可用，不走东财反爬。全量历史 3918 行一次拉取入 DB。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from fgi.collector.akshare_source import AKShareSource
from fgi.storage.database import Database


def main():
    db = Database(Path("data/fgi.db"))
    db.connect()
    src = AKShareSource()

    print("=== Fetching m4_volume (创业板指成交量) 2015-至今 ===", flush=True)
    # 新浪源对 sz399006 实际数据 2016 起；20150101 是脚本拉取起点，最早行不足会自动落入空集。
    r = src.fetch_cyb_daily("20150101", "20260722")
    if r.status.name != "HEALTHY" or r.data is None:
        print(f"FAIL: {r.error}", flush=True)
        return 1

    df = r.data
    print(f"fetched {len(df)} rows, range {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}", flush=True)

    for _, row in df.iterrows():
        d = row["date"]
        d = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        db.upsert_raw_data(d, "m4_volume", float(row["volume"]))
    db.commit()

    after_n = db.count_rows("raw_data", "indicator='m4_volume'")
    after_range = db.get_raw_date_range("m4_volume")
    if after_range:
        print(f"m4_volume in DB: {after_n} rows, range {after_range[0]} ~ {after_range[1]}", flush=True)
    else:
        print(f"m4_volume in DB: 0 rows", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
