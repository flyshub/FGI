"""用全市场日 K 推导「当日涨停家数」，用于校验 levistock 的 M1 原始值。

纯计算，无 I/O：不联网、不读数据库、不依赖 pyarrow，因此可在 CI 的 [dev]
环境里直接单测。取数与比对在 scripts/validate_m1.py。

为什么要它
----------
M1 的原始值来自开盘红的爬虫接口（levistock `market_emotion_kph`），上游改版
或静默失真时 `raw_data` 里不会出现异常，只会安静地变成错数。用交易所规则从
日 K 自行推导一份，就能把「源静默失真」变成可见失败（AGENTS 不变式 #2）。

已知的、消不掉的偏差
--------------------
实测 1877 个交易日（2017-06-02 ~ 2026-11-19），推导值比 levistock 系统性
**偏高**：全期中位 +2 家，近 120 个交易日 |Δ| 中位 2 家、p95 6 家。

- **2017 年的对比不可信**：该年 86 天偏差 ≥10 家（如 2017-11-02 推导 23 / 存储 3），
  是开盘红早期数据不全，不是推导错误。看趋势请从 2018 年起。
- **2026-07-06 起偏差台阶式抬升**：主板 ST 涨跌幅由 5% 改为 10% 后，ST 股开始能
  触及 +10%，本模块会把它们计入涨停，而开盘红不把 ST 计入涨停池——该日前后
  偏差从 +0~5 跳到 +8~13（2026-07-23 为 +13）。
- 新股上市前五日无涨跌幅、退市整理股、开盘红自身的过滤口径，也会各贡献一点。

**所以判定用区间容差 + 相对自身基线的突变，不用等值比较**——方向恒为正这一点
反而有用：源若返回 0，偏差会变成 +50 级别。

两个必须记住的坑
----------------
1. `date_ms` 是**上海零点**（ms % 86_400_000 == 57_600_000）。按 UTC 解析会
   让整条序列错一天，产生几十家的假偏差。一律走 `to_shanghai_dates()`。
2. 涨停价要**四舍五入到分**（4.37 × 1.10 = 4.807 → 4.81）。用浮点直接比
   `close >= prev * 1.1` 会系统性漏判。
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

SHANGHAI_TZ = "Asia/Shanghai"

# 板块涨跌幅比例断点（交易所规则，见 docs/research/2026-11-22-limit-up-history-sources.md）
CYB_20PCT_FROM = dt.date(2020, 8, 24)  # 创业板 10% → 20%
# 注：主板 ST 自 2026-07-06 由 5% 改为 10%。历史 ST 状态拿不到，本模块一律按
# 10% 计，因此 2026-07-06 之前的 ST 涨停会被漏计——实测这部分偏差很小。

RATIO_MAIN = 0.10
RATIO_GROWTH = 0.20
RATIO_STAR = 0.20
RATIO_BJ = 0.30

# 四舍五入的浮点补偿：prev*ratio*100 与 0.5 的边界误差约 1e-10，1e-7 足够安全
_ROUND_EPS = 1e-7

DEFAULT_ABS_TOLERANCE = 5
DEFAULT_REL_TOLERANCE = 0.08
DEFAULT_FAIL_DELTA = 10
DEFAULT_CONSECUTIVE_LIMIT = 3
DEFAULT_RECENT_DAYS = 5
DEFAULT_SUSTAINED_LIMIT = 3


# ── 板块与比例 ────────────────────────────────────────────


def board_of(thscode: str) -> str:
    """由 thscode（如 600519.SH / 300750.SZ / 830799.BJ）判断板块。"""
    if thscode.endswith(".BJ"):
        return "bj"
    ticker = thscode.split(".")[0]
    if ticker.startswith("688"):
        return "star"
    if ticker.startswith(("300", "301")):
        return "growth"
    return "main"


def _boards(thscode: pd.Series) -> np.ndarray:
    codes = thscode.astype(str)
    boards = np.full(len(codes), "main", dtype=object)
    boards[codes.str.startswith("688").to_numpy()] = "star"
    boards[codes.str.startswith(("300", "301")).to_numpy()] = "growth"
    boards[codes.str.endswith(".BJ").to_numpy()] = "bj"
    return boards


def price_limit_ratios(thscode: pd.Series, dates: pd.Series) -> pd.Series:
    """按板块与日期取涨跌幅比例（向量化版本，单一实现）。"""
    boards = _boards(thscode)
    ratios = np.where(
        boards == "bj",
        RATIO_BJ,
        np.where(boards == "star", RATIO_STAR, RATIO_MAIN),
    )
    is_growth = boards == "growth"
    if is_growth.any():
        after_switch = (
            pd.to_datetime(pd.Series(list(dates))) >= pd.Timestamp(CYB_20PCT_FROM)
        ).to_numpy()
        ratios = np.where(is_growth & after_switch, RATIO_GROWTH, ratios)
    return pd.Series(ratios, index=thscode.index, dtype="float64")


def price_limit_ratio(thscode: str, on_date: dt.date) -> float:
    """单个 (股票, 日期) 的比例。主板 ST 的 5% 历史拿不到，一律按 10%。"""
    idx = pd.Index([0])
    return float(
        price_limit_ratios(pd.Series([thscode], index=idx), pd.Series([on_date], index=idx)).iloc[0]
    )


def limit_prices(prev_close, ratio) -> np.ndarray:
    """涨停价 = round_half_up(前收 × (1+比例), 2)。"""
    x = np.asarray(prev_close, dtype="float64") * (1.0 + np.asarray(ratio, dtype="float64"))
    return np.floor(x * 100.0 + 0.5 + _ROUND_EPS) / 100.0


def limit_price(prev_close: float, ratio: float) -> float:
    return float(limit_prices([prev_close], [ratio])[0])


# ── 日期 ─────────────────────────────────────────────────


def to_shanghai_dates(date_ms: pd.Series) -> pd.Series:
    """date_ms（上海零点的时间戳）→ datetime.date。按 UTC 解析会错一天。"""
    return pd.to_datetime(date_ms, unit="ms", utc=True).dt.tz_convert(SHANGHAI_TZ).dt.date


# ── 逐股判定与逐日聚合 ────────────────────────────────────


def annotate_limit_ups(bars: pd.DataFrame) -> pd.DataFrame:
    """给每根 K 打上「是否涨停」。

    输入列：thscode / date / close_price。每只股票的第一根没有前收，会被剔除。
    停牌不产生 K 线，因此前收取「上一根有成交的 K」，复牌日基准正确。
    """
    df = bars.loc[:, ["thscode", "date", "close_price"]].copy()
    df["thscode"] = df["thscode"].astype(str)
    df = df.dropna(subset=["close_price"])
    df = df.sort_values(["thscode", "date"], kind="mergesort")

    grouped = df.groupby("thscode", sort=False)["close_price"]
    df["prev_close"] = grouped.shift(1)
    df = df.dropna(subset=["prev_close"])

    df["ratio"] = price_limit_ratios(df["thscode"], df["date"]).to_numpy()
    df["limit_price"] = limit_prices(df["prev_close"], df["ratio"])
    df["is_limit_up"] = np.isclose(
        df["close_price"].to_numpy(dtype="float64"),
        df["limit_price"].to_numpy(dtype="float64"),
        atol=1e-6,
    )
    return df.loc[:, ["date", "thscode", "prev_close", "close_price", "limit_price", "is_limit_up"]]


def derive_daily_counts(bars: pd.DataFrame, *, include_bj: bool = True) -> pd.Series:
    """逐日涨停家数（int64），按日期升序。"""
    hits = annotate_limit_ups(bars)
    if not include_bj:
        hits = hits.loc[~hits["thscode"].str.endswith(".BJ")]
    counts = hits.groupby("date")["is_limit_up"].sum().astype("int64").sort_index()
    return counts


# ── 比对与判定 ────────────────────────────────────────────


def compare_counts(
    derived: pd.Series,
    stored: pd.Series,
    *,
    abs_tolerance: int = DEFAULT_ABS_TOLERANCE,
    rel_tolerance: float = DEFAULT_REL_TOLERANCE,
    fail_delta: int = DEFAULT_FAIL_DELTA,
) -> pd.DataFrame:
    """逐日比对。容差 = max(abs_tolerance, rel_tolerance × stored)。

    status: ok / warn（超容差但未到 fail_delta）/ fail（≥ fail_delta）。
    只保留两边都有的日期。
    """
    cmp = pd.concat(
        [derived.rename("derived").astype("float64"), stored.rename("stored").astype("float64")],
        axis=1,
        join="inner",
    ).dropna()
    cmp["delta"] = cmp["derived"] - cmp["stored"]
    cmp["tolerance"] = np.maximum(abs_tolerance, rel_tolerance * cmp["stored"].abs())
    breach = cmp["delta"].abs() > cmp["tolerance"]
    cmp["status"] = np.where(
        ~breach, "ok", np.where(cmp["delta"].abs() >= fail_delta, "fail", "warn")
    )
    return cmp.sort_index()


def assess(
    cmp: pd.DataFrame,
    *,
    consecutive_limit: int = DEFAULT_CONSECUTIVE_LIMIT,
    recent_days: int = DEFAULT_RECENT_DAYS,
    sustained_limit: int = DEFAULT_SUSTAINED_LIMIT,
) -> dict[str, object]:
    """汇总结论。

    fail 的三种情形（任一即 fail）：
    1. 最近 `recent_days` 个交易日里出现 fail 日 —— 新出现的问题；
    2. 窗口内 fail 日累计 ≥ `sustained_limit` —— 持续性问题；
    3. 连续 `consecutive_limit` 天同向超容差 —— 缓慢漂移。

    单个久远的 fail 日只进报告、不报警：否则 120 天窗口里一次历史异常会让
    每周任务连挂三个月。
    """
    total = len(cmp)
    statuses = cmp["status"] if total else pd.Series(dtype=object)
    ok_days = int((statuses == "ok").sum())
    warn_days = int((statuses == "warn").sum())
    fail_days = int((statuses == "fail").sum())
    delta = cmp["delta"] if total else pd.Series(dtype="float64")

    max_run = run = 0
    prev_dir = 0
    for status, value in zip(statuses, delta, strict=True):
        if status == "ok" or value == 0:
            run, prev_dir = 0, 0
            continue
        direction = 1 if value > 0 else -1
        run = run + 1 if direction == prev_dir else 1
        prev_dir = direction
        max_run = max(max_run, run)

    recent_fail_days = int((cmp.tail(recent_days)["status"] == "fail").sum()) if total else 0
    failed = recent_fail_days > 0 or fail_days >= sustained_limit or max_run >= consecutive_limit

    return {
        "days": total,
        "ok_days": ok_days,
        "warn_days": warn_days,
        "fail_days": fail_days,
        "recent_fail_days": recent_fail_days,
        "equal_days": int((delta == 0).sum()) if total else 0,
        "mean_delta": float(delta.mean()) if total else 0.0,
        "max_delta": int(delta.max()) if total else 0,
        "min_delta": int(delta.min()) if total else 0,
        "max_consecutive": max_run,
        "verdict": "fail" if failed else "pass",
    }
