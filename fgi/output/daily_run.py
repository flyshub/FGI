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
                       "s1_sentiment_zz", "s3_sentiment", "s4_zt_daily",
                       "v1_pe", "v1_bond", "v2_index",
                       "f1_margin", "f1_market_cap", "f2_fund_position", "f3_index", "f3_industry_flow"]:
        sources = []
        if indicator in ("s1_sentiment_zz", "m2_sentiment", "s3_sentiment") and zzshare_ok:
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


def check_anomaly(db, date_str: str, fgi_raw: float):
    """Check if today's FGI change exceeds the 5-year 99th percentile."""
    import pandas as pd
    end = date_str
    start = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=365 * 6)).strftime("%Y-%m-%d")
    scores = db.get_scores(start, end)
    if scores is None or len(scores) < 252:
        print("  [anomaly] insufficient history for anomaly check")
        return

    changes = scores["FGI_raw"].dropna().diff().abs().dropna()
    if len(changes) < 252:
        print("  [anomaly] insufficient daily changes")
        return

    threshold = changes.quantile(0.99)
    latest = scores[scores.index == date_str]
    if latest.empty or pd.isna(latest["FGI_raw"].iloc[0]):
        return

    prev = scores[scores.index < date_str]
    if prev.empty:
        return
    prev_fgi = prev["FGI_raw"].iloc[-1]
    today_change = abs(fgi_raw - prev_fgi)

    if today_change > threshold:
        print(f"  [anomaly] WARNING: FGI change {today_change:.1f} exceeds 99% threshold {threshold:.1f}")


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

        check_anomaly(db, target_date, result["fgi_raw"])

        try:
            from fgi.output.pushplus import send_fgi_report
            send_fgi_report(
                fgi_raw=result["fgi_raw"],
                dimension_scores=result["dimension_scores"],
                indicator_results=result["indicator_results"],
                health=result["health_score"],
                date_str=target_date,
            )
        except Exception as e:
            print(f"Push notification skipped: {e}")


if __name__ == "__main__":
    main()
