from datetime import datetime, timedelta
from typing import List, Optional
from fgi.config.settings import LOOKBACK_START
from fgi.storage.database import Database
from fgi.calculator.fgi import FGICalculator
from fgi.collector.fallback import DataSourceManager
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.mock_source import MockSource


def setup_data_manager() -> DataSourceManager:
    manager = DataSourceManager()
    manager.register_source("akshare", AKShareSource())
    manager.register_source("mock", MockSource())
    for indicator in ["m1_zt_stats", "m2_sentiment", "m3_index", "m4_cyb_turnover",
                       "s1_sentiment_zz", "s2_sentiment", "s3_index", "s4_zt_daily",
                       "v1_pe", "v2_index",
                       "f1_margin", "f2_northbound", "f3_index"]:
        manager.configure_chain(indicator, ["akshare", "mock"])
    return manager


def is_trading_day(date_str: str) -> bool:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    return True


def get_date_range(start_date: str, end_date: str) -> List[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = []
    current = start
    while current <= end:
        if is_trading_day(current.strftime("%Y-%m-%d")):
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def batch_dates(dates: List[str], batch_size: int) -> List[List[str]]:
    return [dates[i:i + batch_size] for i in range(0, len(dates), batch_size)]


def backfill_indicator(db: Database, calculator, indicator: str, dates: List[str], batch_size: int = 30):
    total = len(dates)
    processed = 0
    for i in range(0, total, batch_size):
        batch = dates[i:i + batch_size]
        for date in batch:
            try:
                result = calculator.run(date)
                print(f"Processed {date} - FGI: {result.get('fgi_final', 'N/A')}")
                processed += 1
                print(f"Progress: {processed}/{total} ({processed/total*100:.1f}%)")
            except Exception as e:
                print(f"Error processing {date}: {e}")


def backfill(start_date: Optional[str] = None, end_date: Optional[str] = None):
    data_manager = setup_data_manager()
    db = Database()

    with db:
        db.init_schema()
        calculator = FGICalculator(data_manager, db)

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = LOOKBACK_START

        print(f"Starting backfill from {start_date} to {end_date}")

        dates = get_date_range(start_date, end_date)
        total = len(dates)
        print(f"Total trading days: {total}")

        success = 0
        failed = 0
        for i, date in enumerate(dates):
            try:
                result = calculator.run(date)
                fgi = result.get("fgi_final", "N/A")
                health = result.get("health_score", "N/A")
                print(f"[{i+1}/{total}] {date}: FGI={fgi:.2f}, health={health:.2f}" if isinstance(fgi, float) and isinstance(health, float) else f"[{i+1}/{total}] {date}: FGI={fgi}, health={health}")
                success += 1
            except Exception as e:
                print(f"[{i+1}/{total}] {date}: ERROR - {e}")
                failed += 1

        print(f"\nBackfill complete: {success} success, {failed} failed")
        print(f"Total scores in database: {db._conn.execute('SELECT COUNT(*) FROM scores_daily').fetchone()[0]}")


if __name__ == "__main__":
    import sys
    start = sys.argv[1] if len(sys.argv) > 1 else None
    end = sys.argv[2] if len(sys.argv) > 2 else None
    backfill(start, end)
