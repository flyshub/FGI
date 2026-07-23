"""Phase 2 only: 从 DB 现有 raw_data 重算全部历史 FGI 得分。

- 清空 scores_daily 和 daily_status（避免旧公式残留）
- 用 trading_calendar 解析交易日，逐日跑 FGICalculator.run(date)
- 自动设 FGI_OFFLINE=1，强制从 raw_data 重构，无网络依赖
- 默认 T+1 模式（end=昨日），避免当日数据未入库；--include-today 包含今日
"""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fgi.storage.database import Database
from fgi.output.backfill import setup_data_manager
from fgi.calculator.fgi import FGICalculator
from fgi.output.status import record_indicator_status
from fgi.collector.trading_calendar import resolve_trading_days
from fgi.common.utils import calculate_health_score, calculate_correlation_exceed_rate


def main(start="2015-01-01", end=None, include_today=False, resume=False):
    if end is None:
        # 默认 T+1 模式：end = 昨日（避免当日数据未入库导致全 missing）
        end = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if include_today:
        # 显式覆盖 end（无论是否传入）
        end = datetime.now().strftime("%Y-%m-%d")

    # 强制 offline 模式，杜绝 calculator 内部 fetch 触发网络
    os.environ["FGI_OFFLINE"] = "1"

    db = Database()
    db.connect()
    db.init_schema()

    print(f"=== Recompute FGI scores: {start} -> {end} ===", flush=True)
    if resume:
        # resume 模式：跳过已完成的日期（FGI_final 非空），不清理数据
        done = {row[0] for row in db._conn.execute(
            "SELECT date FROM scores_daily WHERE FGI_final IS NOT NULL"
        )}
        all_dates = resolve_trading_days(start, end, db=db)
        dates = [d for d in all_dates if d not in done]
        done_n = len(done)
        total = len(all_dates)
        print(f"RESUME mode: done={done_n}/{total}, remaining={len(dates)}", flush=True)
    else:
        print("Clearing scores_daily and daily_status...", flush=True)
        db._conn.execute("DELETE FROM scores_daily")
        db._conn.execute("DELETE FROM daily_status")
        db.commit()
        dates = resolve_trading_days(start, end, db=db)
    print(f"Trading days to process: {len(dates)}", flush=True)

    dm = setup_data_manager()
    dm.set_db(db)  # 注入 DB 用于 offline 模式从 raw_data 重构 DataFrame
    calc = FGICalculator(dm, db)

    ok = miss = err = 0
    t0 = time.time()
    for i, d in enumerate(dates):
        try:
            r = calc.run(d)
            record_indicator_status(db, d, r.get("indicator_results", {}))
            fgi = r.get("fgi_final")
            if isinstance(fgi, (int, float)):
                ok += 1
            else:
                miss += 1
        except Exception as e:
            err += 1
            if err <= 5:
                print(f"  ERR {d}: {type(e).__name__}: {str(e)[:120]}", flush=True)
        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(dates)}] ok={ok} miss={miss} err={err} ({time.time()-t0:.0f}s)", flush=True)

    db.commit()
    n = db._conn.execute("SELECT COUNT(*) FROM scores_daily").fetchone()[0]
    nonnull = db._conn.execute("SELECT COUNT(*) FROM scores_daily WHERE FGI_final IS NOT NULL").fetchone()[0]
    status_n = db._conn.execute("SELECT COUNT(*) FROM daily_status").fetchone()[0]
    print(f"DONE ok={ok} miss={miss} err={err} in {time.time()-t0:.0f}s", flush=True)
    print(f"scores_daily: {n} rows, FGI_final non-null: {nonnull}", flush=True)
    print(f"daily_status: {status_n} rows", flush=True)

    # Phase 2.5: 二次扫描重算 health_score（依赖完整的 scores_daily 历史）
    print("=== Recompute health_score (phase 2.5) ===", flush=True)
    t1 = time.time()
    updated = health_err = 0
    for d in dates:
        try:
            # 从 daily_status 取当日所有 indicator 状态
            rows = db._conn.execute(
                "SELECT indicator, status FROM daily_status WHERE date = ?", (d,)
            ).fetchall()
            if not rows:
                continue
            status_df = pd.DataFrame(rows, columns=["indicator", "status"])
            exceed_rate = calculate_correlation_exceed_rate(db, d)
            health = calculate_health_score(status_df, exceed_rate)
            db._conn.execute(
                "UPDATE scores_daily SET health_score = ? WHERE date = ?",
                (health, d),
            )
            updated += 1
        except Exception as e:
            health_err += 1
            if health_err <= 5:
                print(f"  HEALTH ERR {d}: {type(e).__name__}: {str(e)[:120]}", flush=True)
    db.commit()
    print(f"Health updated: {updated} rows, errors: {health_err} in {time.time()-t1:.0f}s", flush=True)
    db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Offline FGI score recompute")
    parser.add_argument("start", nargs="?", default="2015-01-01")
    parser.add_argument("end", nargs="?", default=None, help="end date (default: yesterday)")
    parser.add_argument("--include-today", action="store_true",
                        help="include today in recompute (default excludes today for T+1 mode)")
    parser.add_argument("--resume", action="store_true",
                        help="resume mode: skip dates where FGI_final already exists, keep existing data")
    args = parser.parse_args()
    main(args.start, args.end, include_today=args.include_today, resume=args.resume)
