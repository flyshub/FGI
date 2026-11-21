"""推送前的回归判定：识别「此前正常、今天突然消失」的指标。

与 `ERROR_SUPPRESS_THRESHOLD` 的分工
------------------------------------
那道门数的是**计算器崩溃**（`status='error'`，由 FGICalculator 的 except 记录）。
数据源故障不走那里：取不到数的计算器调用 `common.missing()`，落的是
`status='missing'`。全库 90 条 error 全是 2018 年的 `float(None)` 崩溃，
最后一次 2018-10-10——八年来没有任何一次源故障触发过那道门。

所以这里补的是 missing 这一段：**某指标此前连续正常、今天却缺了**，
说明是源出了问题，而不是这个指标本来就缺（月度数据、预热期、长期停更）。

为什么不用「missing 计数 ≥ 阈值」：f1 是月度数据、m1/s3 有预热期，
`backfill_late` 存在的理由就是「当日缺、次日补」——按计数抑制会误杀正常流程。

为什么只在第二趟生效：第一趟时当日数据可能还没发布，backfill_late 会补；
补完仍缺才是真断。调用点在 `daily_run` 的 first_pass 返回之后。
"""

from __future__ import annotations

import logging

from fgi.storage.database import Database

logger = logging.getLogger(__name__)

# 回归 = 前 N 个有状态记录的日期全部 normal
REGRESSION_LOOKBACK_DAYS = 3
# 单个指标缺失对 FGI 影响有限（2026-09-24 实例 health 仍为 93.3），
# 两个以上同时从 normal 掉下来才是源级故障。
REGRESSION_THRESHOLD = 2

BROKEN_STATUSES = ("missing", "error")


def find_regressions(
    db: Database,
    date: str,
    indicator_results: dict,
    lookback_days: int = REGRESSION_LOOKBACK_DAYS,
) -> list[str]:
    """返回今天缺数据、且此前 lookback_days 天全部 normal 的指标（大写，升序）。

    历史不足、指标不在历史里、期间有 degraded/forward_fill —— 都不判定：
    宁可漏报，也不把「本来就没数据」说成「源坏了」。
    """
    broken = sorted(
        name for name, r in indicator_results.items() if r.get("status") in BROKEN_STATUSES
    )
    if not broken:
        return []

    history = db.get_status_history(date, lookback_days)
    if history.empty:
        return []
    recent_dates = sorted(history["date"].unique())
    if len(recent_dates) < lookback_days:
        return []

    regressions: list[str] = []
    for name in broken:
        rows = history[
            (history["indicator"] == name.lower()) & (history["date"].isin(recent_dates))
        ]
        if len(rows) == len(recent_dates) and (rows["status"] == "normal").all():
            regressions.append(name)
    return regressions


def regressions_to_suppress(
    db: Database,
    date: str,
    indicator_results: dict,
    threshold: int = REGRESSION_THRESHOLD,
    lookback_days: int = REGRESSION_LOOKBACK_DAYS,
) -> list[str]:
    """达到阈值时返回应触发抑制的指标列表，否则返回空列表。"""
    regressions = find_regressions(db, date, indicator_results, lookback_days)
    if len(regressions) >= threshold:
        # 调用方（daily_run）负责打印并 exit；这里只留日志供回溯。
        return regressions
    if regressions:
        logger.info(
            "%d indicator(s) regressed but below threshold %d: %s",
            len(regressions),
            threshold,
            ", ".join(regressions),
        )
    return []
