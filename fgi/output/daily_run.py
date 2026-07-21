import argparse
import sys
from datetime import datetime, timedelta
from fgi.collector.fallback import DataSourceManager, FallbackChain
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.mootdx_source import MootdxSource
from fgi.collector.tencent_source import TencentSource
from fgi.collector.zzshare_source import ZZShareSource
from fgi.storage.database import Database
from fgi.calculator.fgi import FGICalculator
from fgi.config.settings import (
    AKSHARE_ENABLED, MOOTDX_ENABLED, TENCENT_ENABLED,
    LOOKBACK_START
)


def is_trading_day(date_str: str) -> bool:
    from datetime import datetime
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    return True


def setup_data_manager() -> DataSourceManager:
    manager = DataSourceManager()
    zzshare_ok = False
    if AKSHARE_ENABLED:
        manager.register_source("akshare", AKShareSource())
    if MOOTDX_ENABLED:
        manager.register_source("mootdx", MootdxSource())
    if TENCENT_ENABLED:
        manager.register_source("tencent", TencentSource())
    try:
        manager.register_source("zzshare", ZZShareSource())
        zzshare_ok = True
    except Exception:
        pass
    for indicator in ["m1_zt_stats", "m2_sentiment", "m3_index", "m4_cyb_turnover",
                       "s1_sentiment_zz", "s2_sentiment", "s3_index", "s4_zt_daily",
                       "v1_pe", "v2_index",
                       "f1_margin", "f2_northbound", "f3_index"]:
        sources = []
        if indicator in ("s1_sentiment_zz", "s2_sentiment", "m2_sentiment") and zzshare_ok:
            sources.append("zzshare")
        if AKSHARE_ENABLED:
            sources.append("akshare")
        if MOOTDX_ENABLED:
            sources.append("mootdx")
        if TENCENT_ENABLED:
            sources.append("tencent")
        if sources:
            manager.configure_chain(indicator, sources)
    return manager


def main():
    parser = argparse.ArgumentParser(description="FGI Daily Update")
    parser.add_argument("--date", type=str, default=None,
                        help="Date to run (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.date:
        target_date = args.date
    else:
        target_date = datetime.now().strftime("%Y-%m-%d")

    if not is_trading_day(target_date):
        print(f"Skipping {target_date} - not a trading day")
        return

    print(f"Running FGI update for {target_date}")
    data_manager = setup_data_manager()
    db = Database()
    with db:
        db.init_schema()
        calculator = FGICalculator(data_manager, db)
        result = calculator.run(target_date)
        print(f"FGI Result: {result['fgi_final']:.2f}")
        print(f"Health Score: {result['health_score']:.2f}")
        for dim, score in result['dimension_scores'].items():
            print(f"  {dim}: {score:.2f}")


if __name__ == "__main__":
    main()
