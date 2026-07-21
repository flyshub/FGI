import argparse
import time
import levistock as ls
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from pathlib import Path
from fgi.storage.database import Database
from fgi.config.settings import DB_PATH


def fetch_m1_s4(date_str: str) -> Dict:
    limit_up = ls.limit_up_his_kph(date=date_str)
    zt_count = len(limit_up)
    seal_fund_sum = sum(item.get("seal_money", 0) for item in limit_up) / 1e8

    emotion = ls.market_emotion_kph(date=date_str)
    zt_from_emotion = emotion.get("sjzt", emotion.get("zt", zt_count))

    return {
        "zt_count": zt_count,
        "zt_count_emotion": zt_from_emotion,
        "seal_fund_sum": seal_fund_sum,
    }


def get_trading_days(start_date: str, end_date: str) -> List[str]:
    import akshare as ak
    try:
        df = ak.tool_trade_date_hist_sina()
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]
        return df["trade_date"].tolist()
    except Exception:
        pass
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def get_existing_dates(db: Database, start_date: str, end_date: str) -> set:
    m1 = db.get_raw_data("m1_zt_count", start_date, end_date)
    s4 = db.get_raw_data("s4_seal_fund", start_date, end_date)
    existing = set(m1["date"].tolist()) if not m1.empty else set()
    existing |= set(s4["date"].tolist()) if not s4.empty else set()
    return existing


def zt_backfill(start_date: str, end_date: str, db_path: Optional[Path] = None):
    db = Database(db_path or DB_PATH)
    with db:
        db.init_schema()

        all_dates = get_trading_days(start_date, end_date)
        total = len(all_dates)
        existing = get_existing_dates(db, start_date, end_date)
        todo = [d for d in all_dates if d not in existing]

        if len(todo) == 0:
            print(f"All {total} trading days already collected, nothing to do.")
            return

        print(f"Total trading days: {total}")
        print(f"Already collected:  {len(existing)}")
        print(f"Remaining to fetch: {len(todo)}")
        print()

        start_time = time.time()
        fetched = 0
        skipped = 0
        errors = []

        for i, date_str in enumerate(todo):
            elapsed = time.time() - start_time
            avg = elapsed / (i + 1)
            eta = avg * (len(todo) - i - 1)

            try:
                result = fetch_m1_s4(date_str)
                zt_count = result["zt_count"]
                seal_fund = result["seal_fund_sum"]

                db.upsert_raw_data(date_str, "m1_zt_count", zt_count)
                db.upsert_raw_data(date_str, "s4_seal_fund", seal_fund)
                db.commit()
                fetched += 1

                speed = f"{avg:.1f}s/date"
                eta_str = f"{int(eta // 60)}m{int(eta % 60):02d}s" if eta > 60 else f"{eta:.0f}s"
                print(f"  [{i+1:>4}/{len(todo)}] {date_str} | ↑{zt_count:>4} ¥{seal_fund:>7.1f}亿 | {speed} | ETA {eta_str}")

            except Exception as e:
                errors.append((date_str, str(e)))
                print(f"  [{i+1:>4}/{len(todo)}] {date_str} | ⚠ ERROR: {e}")
                skipped += 1

        total_time = time.time() - start_time
        total_min = int(total_time // 60)
        total_sec = int(total_time % 60)
        print(f"\n{'='*50}")
        print(f"Backfill complete in {total_min}m{total_sec:02d}s")
        print(f"  Fetched:  {fetched} dates")
        print(f"  Skipped:  {skipped} dates (errors)")
        if errors:
            print(f"\n  Errors ({len(errors)}):")
            for d, e in errors[:10]:
                print(f"    {d}: {e}")
            if len(errors) > 10:
                print(f"    ... and {len(errors) - 10} more")
        print(f"  Database: {db._conn.execute('SELECT COUNT(*) FROM raw_data WHERE indicator IN (\"m1_zt_count\", \"s4_seal_fund\")').fetchone()[0]} raw records")


def main():
    parser = argparse.ArgumentParser(description="M1/S4 涨停板数据回填（levistock）")
    parser.add_argument("--start", type=str, default="2020-01-01",
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None,
                        help="End date (YYYY-MM-DD), default today")
    args = parser.parse_args()

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    zt_backfill(args.start, end)


if __name__ == "__main__":
    main()
