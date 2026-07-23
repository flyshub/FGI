import argparse
import logging
from datetime import datetime, timedelta
from fgi.collector.fallback import DataSourceManager, FallbackChain
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.mootdx_source import MootdxSource
from fgi.collector.tencent_source import TencentSource
from fgi.collector.zzshare_source import ZZShareSource
from fgi.collector.trading_calendar import TradingCalendar
from fgi.calculator.fgi import FGICalculator
from fgi.storage.database import Database
from fgi.config.settings import (
    DB_PATH, AKSHARE_ENABLED, MOOTDX_ENABLED, TENCENT_ENABLED,
)

try:
    import zzshare  # noqa: F401
    ZZSHARE_ENABLED = True
except ImportError:
    ZZSHARE_ENABLED = False
from fgi.output.pushplus import send_fgi_report
from fgi.output.status import record_indicator_status

logger = logging.getLogger(__name__)


def is_trading_day(date_str: str, trading_days=None) -> bool:
    """真实交易日历优先；日历不可用时回退工作日判断。"""
    if trading_days is None:
        trading_days = TradingCalendar().load()
    if trading_days is not None:
        return date_str in set(trading_days)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.weekday() < 5


def setup_data_manager() -> DataSourceManager:
    manager = DataSourceManager()

    if AKSHARE_ENABLED:
        manager.register_source("akshare", AKShareSource())
    if MOOTDX_ENABLED:
        manager.register_source("mootdx", MootdxSource())
    if TENCENT_ENABLED:
        manager.register_source("tencent", TencentSource())
    if ZZSHARE_ENABLED:
        manager.register_source("zzshare", ZZShareSource())

    zzshare_ok = manager.has_source("zzshare")
    chain_configs = {
        "m1_zt_stats": ["zzshare"],
        "m2_market_overview": ["zzshare"],
        "m3_index": ["akshare"],
        "m4_cyb_volume": ["akshare"],
        "s2_sentiment": ["zzshare"],
        "s3_zt_daily": ["zzshare"],
        "v1_pe": ["akshare"],
        "v1_bond": ["akshare"],
        "v2_index": ["akshare"],
        "f1_margin": ["akshare"],
        "f1_market_cap": ["akshare"],
        "f2_fund_position": ["akshare"],
        "f3_industry_flow": ["akshare"],
        "f3_index": ["akshare"],
    }

    for indicator, sources in chain_configs.items():
        if indicator in ("m2_market_overview", "s2_sentiment") and zzshare_ok:
            sources = sources + ["akshare"]
        # mootdx/tencent 作为兜底追加；不支持的方法会被 FallbackChain 安全剔除
        sources = sources + ["mootdx", "tencent"]
        sources = [s for s in sources if manager.has_source(s)]
        if sources:
            manager.configure_chain(indicator, sources)

    return manager


def main():
    parser = argparse.ArgumentParser(description="Run daily FGI calculation")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD)")
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    if not is_trading_day(target_date):
        print(f"Skipping {target_date} - not a trading day")
        return

    print(f"Running FGI calculation for {target_date}")

    data_manager = setup_data_manager()

    with Database(DB_PATH) as db:
        db.init_schema()

        calculator = FGICalculator(data_manager, db)
        result = calculator.run(target_date)

        record_indicator_status(db, target_date, result.get("indicator_results", {}))

        print(f"FGI Final: {result['fgi_final']}")
        print(f"Health Score: {result['health_score']}")
        print(f"Indicator Status: {result['indicator_results']}")

        try:
            from fgi.output.alert import Alert
            Alert(db_path=db.path).check_and_alert(target_date, result)
        except Exception as e:
            print(f"Anomaly check skipped: {e}")

        ok = send_fgi_report(
            result["fgi_raw"], result["dimension_scores"],
            result["indicator_results"], result["health_score"],
            date_str=target_date)
        print(f"PushPlus: {'OK' if ok else 'skipped'}")


if __name__ == "__main__":
    main()
