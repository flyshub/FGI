"""单独回填 m4_turnover（创业板换手率）。

直接调 AKShareSource.fetch_cyb_daily 拉全区间，存入 raw_data。
绕开 calculator 的 score 计算——本阶段只补 raw 数据。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fgi.storage.database import Database
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.base import DataSourceStatus


def main():
    db = Database()
    db.connect()
    db.init_schema()
    src = AKShareSource()

    print("=== Fetching m4_turnover (创业板换手率) 2015-至今 ===", flush=True)
    t0 = time.time()
    try:
        result = src.fetch_cyb_daily("2015-01-01", "2026-12-31")
    except Exception as e:
        print(f"FAIL after {time.time()-t0:.0f}s: {type(e).__name__}: {e}", flush=True)
        db.close()
        return 1

    elapsed = time.time() - t0
    if result.status != DataSourceStatus.HEALTHY or result.data is None or result.data.empty:
        print(f"FAIL after {elapsed:.0f}s: status={result.status} error={result.error}", flush=True)
        db.close()
        return 1

    df = result.data
    n = 0
    for _, row in df.iterrows():
        d = str(row["date"])
        val = row["turnover_rate"]
        try:
            db.upsert_raw_data(d, "m4_turnover", float(val))
            n += 1
        except (ValueError, TypeError):
            pass
    db.commit()

    after = db._conn.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM raw_data WHERE indicator='m4_turnover'"
    ).fetchone()
    print(f"OK: wrote {n} rows in {elapsed:.0f}s", flush=True)
    print(f"m4_turnover in DB: {after[0]} rows, range {after[1]} ~ {after[2]}", flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
