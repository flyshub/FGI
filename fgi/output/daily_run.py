import argparse
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()
from fgi.collector.fallback import DataSourceManager
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.mootdx_source import MootdxSource
from fgi.collector.tencent_source import TencentSource
from fgi.collector.zzshare_source import ZZShareSource
from fgi.collector.chains import configure_manager
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
from fgi.output.alert import Alert

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

    # mootdx/tencent 作为兜底追加；不支持的方法会被 FallbackChain 安全剔除
    extra = []
    if MOOTDX_ENABLED:
        extra.append("mootdx")
    if TENCENT_ENABLED:
        extra.append("tencent")
    configure_manager(manager, extra_fallbacks=extra or None)

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
            anomaly_detected = Alert(db_path=db.path).check_and_alert(target_date, result)
        except Exception as e:
            print(f"Anomaly check skipped: {e}")
            anomaly_detected = False

        if anomaly_detected:
            print("Anomaly detected — suspending daily FGI push, manual review required (spec line 262).")
        else:
            ok = send_fgi_report(
                result["fgi_final"], result["dimension_scores"],
                result["indicator_results"], result["health_score"],
                date_str=target_date)
            print(f"PushPlus: {'OK' if ok else 'skipped'}")


if __name__ == "__main__":
    main()
