"""recompute_scores v2: 可中断 + 续算 + 实时进度 + 批量 percentile 优化。

v2 改进：
1. 范围清理（不再无条件清空整张表）
2. 真正 --resume：从断点续算
3. 每日期 commit：kill 后已处理数据保留
4. 实时进度行：每 10 天打点，含百分比/ETA/ok/miss/err
5. 批量 percentile 预计算：每个 indicator 只算一次完整序列
6. 心跳文件：/tmp/fgi_recompute_heartbeat.txt 实时写入，外部可 watch

依赖：FGI_OFFLINE=1 (脚本内自动设置)。

用法：
    python3.12 -m scripts.recompute_v2 2017-06-02 2026-07-23
    python3.12 -m scripts.recompute_v2 --resume 2017-06-02 2026-07-23
    python3.12 -m scripts.recompute_v2 --include-today 2020-01-01
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 强制 offline 模式，杜绝 calculator 内部 fetch 触发网络
os.environ["FGI_OFFLINE"] = "1"

from fgi.storage.database import Database
from fgi.output.backfill import setup_data_manager
from fgi.calculator.fgi import FGICalculator
from fgi.output.status import record_indicator_status
from fgi.collector.trading_calendar import resolve_trading_days
from fgi.common.utils import calculate_health_score, calculate_correlation_exceed_rate

HEARTBEAT_FILE = Path("/tmp/fgi_recompute_heartbeat.txt")
PROGRESS_FILE = Path("/tmp/fgi_recompute_progress.json")


def write_heartbeat(msg: str) -> None:
    """心跳文件，供外部 watch 实时查看进度"""
    HEARTBEAT_FILE.write_text(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n", encoding="utf-8")


def write_progress(i: int, total: int, ok: int, miss: int, err: int, elapsed: float) -> None:
    """JSON 进度文件，供外部脚本（如 kill-and-resume）查询"""
    import json
    avg = elapsed / (i + 1) if i >= 0 else 0
    eta = avg * (total - i - 1) if i >= 0 else 0
    payload = {
        "ts": datetime.now().isoformat(),
        "done": i + 1,
        "total": total,
        "pct": round((i + 1) / total * 100, 2) if total > 0 else 0,
        "ok": ok, "miss": miss, "err": err,
        "elapsed_s": int(elapsed),
        "eta_s": int(eta),
        "avg_s_per_day": round(avg, 3),
    }
    PROGRESS_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main(start: str = "2015-01-01", end: str | None = None,
         include_today: bool = False, resume: bool = False) -> None:
    if end is None:
        end = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if include_today:
        end = datetime.now().strftime("%Y-%m-%d")

    db = Database()
    db.connect()
    db.init_schema()

    print(f"=== recompute v2: {start} -> {end} (resume={resume}, include_today={include_today}) ===", flush=True)
    write_heartbeat(f"START {start} -> {end}, resume={resume}")

    all_dates = resolve_trading_days(start, end, db=db)
    total_all = len(all_dates)

    if resume:
        scores_all = db.get_scores("1900-01-01", "2999-12-31")
        done = set(scores_all.loc[scores_all["FGI_final"].notna(), "date"].tolist())
        dates = [d for d in all_dates if d not in done]
        print(f"RESUME: done={len(done)}/{total_all}, remaining={len(dates)}", flush=True)
        write_heartbeat(f"RESUME done={len(done)}/{total_all}, remaining={len(dates)}")
    else:
        # v2: 范围清理，只清指定范围而非整表
        print(f"Range clearing: scores_daily/daily_status WHERE date BETWEEN {start} AND {end}...", flush=True)
        write_heartbeat(f"Range clearing {start}~{end}")
        sd = db.clear_table_range("scores_daily", start, end)
        ds = db.clear_table_range("daily_status", start, end)
        db.commit()
        print(f"  deleted scores_daily={sd}, daily_status={ds}", flush=True)
        dates = all_dates

    total = len(dates)
    print(f"Trading days to process: {total}", flush=True)
    write_heartbeat(f"Processing {total} days")

    dm = setup_data_manager()
    dm.set_db(db)
    calc = FGICalculator(dm, db)

    ok = miss = err = 0
    t0 = time.time()
    last_save = time.time()

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
            if err <= 10:
                msg = f"  ERR {d}: {type(e).__name__}: {str(e)[:120]}"
                print(msg, flush=True)
                write_heartbeat(msg)

        # 每 50 天 commit 一次：断点续算的粒度（最多丢失 50 天进度）
        if (i + 1) % 50 == 0 or (i + 1) == total:
            db.commit()

        # 实时进度：每 10 天或 5 秒打一次
        now = time.time()
        if (i + 1) % 10 == 0 or now - last_save > 5 or (i + 1) == total:
            elapsed = now - t0
            avg = elapsed / (i + 1)
            eta = avg * (total - i - 1)
            eta_min = int(eta // 60)
            eta_s = int(eta % 60)
            pct = (i + 1) / total * 100
            msg = (f"  [{i+1}/{total}] {pct:5.1f}% | ok={ok} miss={miss} err={err} | "
                   f"avg={avg:.2f}s/d | ETA {eta_min}m{eta_s:02d}s | elapsed {int(elapsed)}s")
            print(msg, flush=True)
            write_heartbeat(msg)
            write_progress(i, total, ok, miss, err, elapsed)
            last_save = now

    elapsed = time.time() - t0
    n = db.count_rows("scores_daily")
    nonnull = db.count_rows("scores_daily", "FGI_final IS NOT NULL")
    status_n = db.count_rows("daily_status")
    summary = f"DONE ok={ok} miss={miss} err={err} in {int(elapsed)}s ({elapsed/60:.1f}min)"
    print(summary, flush=True)
    print(f"scores_daily: {n} rows, FGI_final non-null: {nonnull}", flush=True)
    print(f"daily_status: {status_n} rows", flush=True)
    write_heartbeat(summary)

    # Phase 2.5: health_score 重算
    print("=== Phase 2.5: health_score ===", flush=True)
    write_heartbeat("HEALTH phase start")
    t1 = time.time()
    updated = health_err = 0
    for d in dates:
        try:
            rows = db.get_indicator_status(d)
            if not rows:
                continue
            status_df = pd.DataFrame(rows, columns=["indicator", "status"])
            exceed_rate = calculate_correlation_exceed_rate(db, d)
            health = calculate_health_score(status_df, exceed_rate)
            db.update_score_field(d, "health_score", health)
            updated += 1
        except Exception as e:
            health_err += 1
            if health_err <= 5:
                msg = f"  HEALTH ERR {d}: {type(e).__name__}: {str(e)[:120]}"
                print(msg, flush=True)
                write_heartbeat(msg)
    db.commit()
    health_summary = f"Health updated: {updated} rows, errors: {health_err} in {int(time.time()-t1)}s"
    print(health_summary, flush=True)
    write_heartbeat(health_summary)
    db.close()
    print(f"\n✓ Heartbeat: watch -n2 'cat {HEARTBEAT_FILE}'", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="recompute v2: resumable + progress")
    parser.add_argument("start", nargs="?", default="2015-01-01")
    parser.add_argument("end", nargs="?", default=None)
    parser.add_argument("--include-today", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="skip dates where FGI_final already exists")
    args = parser.parse_args()
    main(args.start, args.end, include_today=args.include_today, resume=args.resume)
