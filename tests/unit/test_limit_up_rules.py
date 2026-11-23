"""涨停家数推导规则（fgi.analysis.limit_up）的单元测试。

不依赖 pyarrow / parquet：全部用构造的 DataFrame，CI 的 [dev] 环境即可运行。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fgi.analysis import limit_up as lu

# ── 板块与比例 ────────────────────────────────────────────


@pytest.mark.parametrize(
    "code,board",
    [
        ("600519.SH", "main"),
        ("601318.SH", "main"),
        ("000001.SZ", "main"),
        ("002415.SZ", "main"),
        ("300750.SZ", "growth"),
        ("301001.SZ", "growth"),
        ("688981.SH", "star"),
        ("830799.BJ", "bj"),
        ("430047.BJ", "bj"),
    ],
)
def test_board_of(code: str, board: str) -> None:
    assert lu.board_of(code) == board


def test_main_board_is_ten_percent() -> None:
    assert lu.price_limit_ratio("600519.SH", dt.date(2026, 11, 20)) == pytest.approx(0.10)


def test_growth_board_switch_on_2020_08_24() -> None:
    assert lu.price_limit_ratio("300750.SZ", dt.date(2020, 8, 21)) == pytest.approx(0.10)
    assert lu.price_limit_ratio("300750.SZ", dt.date(2020, 8, 24)) == pytest.approx(0.20)
    assert lu.price_limit_ratio("300750.SZ", dt.date(2026, 11, 20)) == pytest.approx(0.20)


def test_star_and_bj() -> None:
    assert lu.price_limit_ratio("688981.SH", dt.date(2019, 7, 22)) == pytest.approx(0.20)
    assert lu.price_limit_ratio("830799.BJ", dt.date(2026, 11, 20)) == pytest.approx(0.30)


# ── 涨停价：四舍五入到分（round half up，不是银行家舍入）────────


@pytest.mark.parametrize(
    "prev,ratio,expected",
    [
        (4.37, 0.10, 4.81),  # 4.807 → 4.81
        (3.33, 0.10, 3.66),  # 3.663 → 3.66
        (10.05, 0.10, 11.06),  # 11.055 恰好 .5 → 进位
        (10.00, 0.10, 11.00),
        (2.45, 0.10, 2.70),  # 2.695 → 2.70
        (1.23, 0.20, 1.48),  # 1.476 → 1.48
        (5.00, 0.30, 6.50),
    ],
)
def test_limit_price_rounds_half_up_to_cent(prev: float, ratio: float, expected: float) -> None:
    assert lu.limit_price(prev, ratio) == pytest.approx(expected)


# ── 时区：date_ms 是上海零点，按 UTC 解析会整体错一天 ──────────────


def test_date_ms_is_shanghai_midnight() -> None:
    # 2026-11-20 00:00 上海 = 2026-11-19 16:00 UTC
    ms = int(dt.datetime(2026, 11, 19, 16, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert ms % 86_400_000 == 57_600_000
    dates = lu.to_shanghai_dates(pd.Series([ms]))
    assert dates.iloc[0] == dt.date(2026, 11, 20)


# ── 逐股判定与逐日聚合 ────────────────────────────────────


def _bars(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["thscode", "date", "close_price"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def test_counts_limit_up_and_skips_first_bar_per_stock() -> None:
    bars = _bars(
        [
            # 600000：第二天涨停（10.00 → 11.00）
            ("600000.SH", "2026-11-18", 10.00),
            ("600000.SH", "2026-11-19", 11.00),
            # 600001：第二天只涨 9.99%，不算
            ("600001.SH", "2026-11-18", 10.00),
            ("600001.SH", "2026-11-19", 10.99),
        ]
    )
    hits = lu.annotate_limit_ups(bars)
    assert hits["is_limit_up"].sum() == 1
    # 窗口首日没有前收，必须被剔除而不是当成 0
    assert dt.date(2026, 11, 18) not in set(hits.loc[hits["is_limit_up"], "date"])
    counts = lu.derive_daily_counts(bars)
    assert counts.loc[dt.date(2026, 11, 19)] == 1


def test_suspension_uses_last_available_close() -> None:
    bars = _bars(
        [
            ("600000.SH", "2026-11-02", 10.00),
            # 停牌数日，11-10 复牌后涨停（基准仍是 11-02 的 10.00）
            ("600000.SH", "2026-11-10", 11.00),
        ]
    )
    counts = lu.derive_daily_counts(bars)
    assert counts.loc[dt.date(2026, 11, 10)] == 1


def test_growth_board_uses_20pct() -> None:
    bars = _bars(
        [
            ("300001.SZ", "2026-11-18", 10.00),
            ("300001.SZ", "2026-11-19", 11.00),  # +10% 不是创业板涨停
            ("300002.SZ", "2026-11-18", 10.00),
            ("300002.SZ", "2026-11-19", 12.00),  # +20% 是
        ]
    )
    assert lu.derive_daily_counts(bars).loc[dt.date(2026, 11, 19)] == 1


def test_bj_can_be_excluded() -> None:
    bars = _bars(
        [
            ("830799.BJ", "2026-11-18", 10.00),
            ("830799.BJ", "2026-11-19", 13.00),  # +30%
            ("600000.SH", "2026-11-18", 10.00),
            ("600000.SH", "2026-11-19", 11.00),  # +10%
        ]
    )
    assert lu.derive_daily_counts(bars).loc[dt.date(2026, 11, 19)] == 2
    assert lu.derive_daily_counts(bars, include_bj=False).loc[dt.date(2026, 11, 19)] == 1


# ── 比对与判定 ────────────────────────────────────────────


def _series(pairs: list[tuple[str, int]]) -> pd.Series:
    return pd.Series(
        [v for _, v in pairs],
        index=[dt.date.fromisoformat(d) for d, _ in pairs],
        dtype="int64",
    )


def test_compare_within_tolerance_is_ok() -> None:
    derived = _series([("2026-11-19", 30), ("2026-11-20", 120)])
    stored = _series([("2026-11-19", 28), ("2026-11-20", 115)])
    cmp = lu.compare_counts(derived, stored)
    assert list(cmp["status"]) == ["ok", "ok"]  # 2<=max(3,1.4)；5<=max(3,5.75)


def test_compare_breach_levels() -> None:
    derived = _series([("2026-11-19", 35), ("2026-11-20", 60)])
    stored = _series([("2026-11-19", 28), ("2026-11-20", 20)])
    cmp = lu.compare_counts(derived, stored)
    assert list(cmp["status"]) == ["warn", "fail"]  # 7 家：超容差未到 10；40 家：fail


def test_compare_only_uses_dates_present_in_both() -> None:
    derived = _series([("2026-11-19", 30), ("2026-11-20", 40)])
    stored = _series([("2026-11-19", 30)])
    assert len(lu.compare_counts(derived, stored)) == 1


def test_assess_flags_three_consecutive_breaches() -> None:
    derived = _series([("2026-11-17", 34), ("2026-11-18", 35), ("2026-11-19", 36)])
    stored = _series([("2026-11-17", 28), ("2026-11-18", 29), ("2026-11-19", 30)])
    summary = lu.assess(lu.compare_counts(derived, stored))
    assert summary["warn_days"] == 3
    assert summary["max_consecutive"] == 3
    assert summary["verdict"] == "fail"


def test_assess_passes_on_noise() -> None:
    derived = _series([("2026-11-18", 30), ("2026-11-19", 31)])
    stored = _series([("2026-11-18", 28), ("2026-11-19", 30)])
    summary = lu.assess(lu.compare_counts(derived, stored))
    assert summary["ok_days"] == 2
    assert summary["verdict"] == "pass"


def test_assess_fails_on_recent_hard_breach() -> None:
    # 最后一天偏差 40 家：新出现的问题，必须报警
    derived = _series([("2026-11-17", 30), ("2026-11-18", 31), ("2026-11-19", 70)])
    stored = _series([("2026-11-17", 28), ("2026-11-18", 30), ("2026-11-19", 30)])
    summary = lu.assess(lu.compare_counts(derived, stored))
    assert summary["recent_fail_days"] == 1
    assert summary["verdict"] == "fail"


def test_assess_ignores_single_stale_fail_day() -> None:
    # 很久以前有 1 天 fail，之后恢复正常：只进报告，不报警
    pairs_derived = [("2026-11-02", 70)] + [(f"2026-11-{d:02d}", 30) for d in range(3, 21)]
    pairs_stored = [("2026-11-02", 30)] + [(f"2026-11-{d:02d}", 29) for d in range(3, 21)]
    summary = lu.assess(lu.compare_counts(_series(pairs_derived), _series(pairs_stored)))
    assert summary["fail_days"] == 1
    assert summary["recent_fail_days"] == 0
    assert summary["verdict"] == "pass"


def test_assess_fails_on_sustained_fail_days() -> None:
    # 3 天 fail 已滚出最近 5 日窗口，但累计达阈值 → 仍报警
    bad = ["2026-11-02", "2026-11-03", "2026-11-04"]
    good = [f"2026-11-{d:02d}" for d in range(5, 13)]
    derived = _series([(d, 70) for d in bad] + [(d, 30) for d in good])
    stored = _series([(d, 30) for d in bad] + [(d, 29) for d in good])
    summary = lu.assess(lu.compare_counts(derived, stored))
    assert summary["fail_days"] == 3
    assert summary["recent_fail_days"] == 0
    assert summary["verdict"] == "fail"


def test_tolerance_parameters_are_honoured() -> None:
    derived = _series([("2026-11-19", 36)])
    stored = _series([("2026-11-19", 30)])
    strict = lu.compare_counts(derived, stored, abs_tolerance=2, rel_tolerance=0.01)
    loose = lu.compare_counts(derived, stored, abs_tolerance=10, rel_tolerance=0.01)
    assert strict.loc[dt.date(2026, 11, 19), "status"] == "warn"
    assert loose.loc[dt.date(2026, 11, 19), "status"] == "ok"
