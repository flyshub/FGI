from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fgi.config.settings import LOOKBACK_START
from fgi.storage.database import Database
from fgi.calculator.fgi import FGICalculator
from fgi.collector.fallback import DataSourceManager, FallbackChain
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.mootdx_source import MootdxSource
from fgi.collector.tencent_source import TencentSource


def setup_data_manager() -> DataSourceManager:
    manager = DataSourceManager()
    manager.register_source("akshare", AKShareSource())
    manager.register_source("mootdx", MootdxSource())
    manager.register_source("tencent", TencentSource())
    for indicator in ["m3_index", "m1_zt", "m2_sentiment", "m4_turnover",
                       "s1_rise_fall", "s4_zt_ratio", "v1_pe", "v2_bond",
                       "f1_margin", "f2_northbound", "f3_large_single"]:
        sources = ["akshare", "mootdx", "tencent"]
        manager.configure_chain(indicator, sources)
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


def backfill_indicator(db: Database, calculator: FGICalculator, indicator: str, dates: List[str], batch_size: int = 30):
    total = len(dates)
    processed = 0
    
    for i in range(0, total, batch_size):
        batch = dates[i:i + batch_size]
        for date in batch:
            try:
                result = calculator.run(date)
                print(f"Processed {date} - FGI: {result['fgi_final']:.2f}")
                processed += 1
                print(f"Progress: {processed}/{total} ({processed/total*100:.1f}%)")
            except Exception as e:
                print(f"Error processing {date}: {e}")


def backfill():
    data_manager = setup_data_manager()
    db = Database()
    
    with db:
        db.init_schema()
        calculator = FGICalculator(data_manager, db)
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = LOOKBACK_START
        
        print(f"Starting backfill from {start_date} to {end_date}")
        
        indicators = ["m3_index", "m1_zt", "m2_sentiment", "m4_turnover",
                     "s1_rise_fall", "s4_zt_ratio", "v1_pe", "v2_bond",
                     "f1_margin", "f2_northbound", "f3_large_single"]
        
        for indicator in indicators:
            print(f"\nBackfilling indicator: {indicator}")
            missing_dates = db.get_missing_dates(indicator, start_date, end_date)
            
            if not missing_dates:
                print(f"  No missing dates for {indicator}")
                continue
                
            print(f"  Found {len(missing_dates)} missing dates")
            
            # Process in monthly batches (30 days)
            batches = batch_dates(missing_dates, 30)
            for i, batch in enumerate(batches):
                print(f"  Processing batch {i+1}/{len(batches)}")
                backfill_indicator(db, calculator, indicator, batch, batch_size=30)