import argparse
import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
from fgi.calculator.fgi import FGICalculator
from fgi.collector.factory import setup_data_manager
from fgi.collector.trading_calendar import TradingCalendar
from fgi.config.settings import DB_PATH
from fgi.output.alert import Alert
from fgi.output.decision_matrix import compute_decision_matrix
from fgi.output.pushplus import send_fgi_report
from fgi.output.status import record_indicator_status
from fgi.storage.database import Database

logger = logging.getLogger(__name__)

# 崩掉的指标达到这个数量时，FGI 已不代表市场，宁可中断也不推一个假数字。
ERROR_SUPPRESS_THRESHOLD = 3


class TradingCalendarUnavailable(RuntimeError):
    """交易日历不可用。宁可失败，也不要把节假日当交易日跑出「昨日数据的今日副本」。"""


def is_trading_day(date_str: str, trading_days=None) -> bool:
    """以真实交易日历为准。

    日历不可用时**不再**回退到「周一至周五」：那会把法定节假日当交易日执行，
    使 forward-fill 的昨日分数以 normal 状态被推送为今日数据
    （docs/audits/full-review-2026-11-18.md P0-2）。
    """
    if trading_days is None:
        trading_days = TradingCalendar().load()
    if trading_days is None:
        raise TradingCalendarUnavailable(
            "trading calendar unavailable (akshare and disk cache both failed); "
            "refusing to guess weekdays as trading days"
        )
    return date_str in set(trading_days)


def main():
    parser = argparse.ArgumentParser(description="Run daily FGI calculation")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD)")
    parser.add_argument(
        "--first-pass", action="store_true", help="Silent run: calculate but skip push"
    )
    parser.add_argument(
        "--force-push",
        action="store_true",
        help="Skip anomaly check and push (use after manual review, spec line 262)",
    )
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

        error_count = result.get("error_count", 0)
        degraded_count = result.get("degraded_count", 0)
        missing_count = result.get("missing_count", 0)
        ok_count = result.get("ok_count", 0)

        print(f"FGI Final: {result['fgi_final']}")
        print(f"Health Score: {result['health_score']}")
        print(
            f"Indicators: {ok_count}/12 normal, {degraded_count} degraded, "
            f"{missing_count} missing, {error_count} error"
        )

        # 数据日期与目标日期不一致 = 该指标是沿用的旧值，读者需要知道。
        # 暂只标注（观察两周后再决定是否收紧为阻断）。
        stale = sorted(
            name
            for name, r in result.get("indicator_results", {}).items()
            if r.get("source_date") not in (None, target_date)
        )
        if stale:
            print(
                f"Stale data: {len(stale)} indicators filled from earlier dates: {', '.join(stale)}"
            )

        # 没有任何一个维度形成 FGI（可用维度 < 2）时，没有任何东西可发布。
        # 这不是「低分」，是「没算出来」，必须让 workflow 判定失败（P1-9）。
        if result.get("fgi_final") is None:
            print(
                "No FGI produced (fewer than 2 dimensions available) — refusing to publish.",
                file=sys.stderr,
            )
            sys.exit(1)

        # 失败可见性优先于推送：多个指标崩溃时 FGI 已经失真，非零退出让
        # workflow 的失败判定生效（不写回数据 + 开 issue）。
        if error_count >= ERROR_SUPPRESS_THRESHOLD:
            print(
                f"Push suppressed: {error_count} indicators failed (threshold "
                f"{ERROR_SUPPRESS_THRESHOLD}); FGI no longer represents the market.",
                file=sys.stderr,
            )
            sys.exit(1)

        # 首次运行（--first-pass）只算不推送，用于 workflow 中先干跑再回填后正式推送
        # 默认（无 --first-pass 或第二次跑）正常推送
        # --force-push：人工复核通过后跳过异常检测直接推送（spec line 262 的人工放行通道）
        first_pass = args.first_pass or os.environ.get("FGI_FIRST_PASS") == "1"
        if first_pass:
            return

        if args.force_push:
            anomaly_detected = False
        else:
            try:
                anomaly_detected = Alert(db_path=db.path).check_and_alert(target_date, result)
            except Exception as e:
                # 异常检测自己崩溃时必须当作「未通过」：它原本会是 fail-closed，
                # 在这里吞掉就等于在最需要告警的时刻放行（P0-4）。
                print(
                    f"Anomaly check failed: {e} — push suppressed, manual review required",
                    file=sys.stderr,
                )
                sys.exit(1)

        if anomaly_detected:
            print(
                "Anomaly detected — suspending daily FGI push, manual review required (spec line 262)."
            )
            return

        dm = compute_decision_matrix(db, target_date, result["fgi_final"])
        dm_dict = dm.to_dict() if dm else None
        ok = send_fgi_report(
            db,
            result["fgi_final"],
            result["dimension_scores"],
            result["indicator_results"],
            result["health_score"],
            date_str=target_date,
            decision_matrix=dm_dict,
        )
        if not ok:
            # 推送失败曾只打印一行日志就退出 0，告警链在最后一公里断掉（P0-5）。
            print("PushPlus: failed — daily report was not delivered", file=sys.stderr)
            sys.exit(1)
        print("PushPlus: OK")


if __name__ == "__main__":
    main()
