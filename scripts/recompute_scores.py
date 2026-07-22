"""Phase 2 only: 从 DB 现有 raw_data 重算全部历史 FGI 得分。

- 清空 scores_daily 和 daily_status（避免旧公式残留）
- 用 trading_calendar 解析交易日，逐日跑 FGICalculator.run(date)
- 纯 DB 读，无网络依赖（除非 calculator 内部当日 fetch，如 M4 last-good-value）
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fgi.storage.database import Database
from fgi.output.backfill import setup_data_manager
from fgi.calculator.fgi import FGICalculator
from fgi.output.status import record_indicator_status
from fgi.collector.trading_calendar import resolve_trading_days


def main(start="2015-01-01", end=None):
    from datetime import datetime
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")

    db = Database()
    db.connect()
    db.init_schema()

    print(f"=== Recompute FGI scores: {start} -> {end} ===", flush=True)
    print("Clearing scores_daily and daily_status...", flush=True)
    db._conn.execute("DELETE FROM scores_daily")
    db._conn.execute("DELETE FROM daily_status")
    db.commit()

    dm = setup_data_manager()
    calc = FGICalculator(dm, db)
    dates = resolve_trading_days(start, end, db=db)
    print(f"Trading days: {len(dates)}", flush=True)

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
    db.close()


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2015-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else None
    main(start, end)
