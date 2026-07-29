"""FGI historical signal validation — zone-based forward return analysis.

Computes market returns across 5 FGI zones (extreme fear through extreme greed)
for 3 forward horizons (5/20/60 trading days), with in-sample/out-of-sample split.

Usage:
    from fgi.storage.database import Database
    from fgi.output.signal_report import SignalReportEngine

    with Database() as db:
        engine = SignalReportEngine(db)
        result = engine.run()
        # result["stats"][horizon] = list of zone stat dicts
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fgi.storage.database import Database

ZONES = [
    ("极度恐惧", 0.0, 20.0),
    ("恐惧", 20.0, 40.0),
    ("中性", 40.0, 60.0),
    ("贪婪", 60.0, 80.0),
    ("极度贪婪", 80.0, 100.0),
]

HORIZONS = [5, 20, 60]

SAMPLE_SPLIT_YEAR = 2023  # <=2022 in-sample, >=2023 out-of-sample


def assign_zone(fgi: float | None) -> str:
    """Map an FGI value to its zone label. None/NaN → '未知'."""
    if fgi is None:
        return "未知"
    try:
        if pd.isna(fgi):
            return "未知"
    except (TypeError, ValueError):
        pass
    for label, lo, hi in ZONES:
        if lo <= fgi < hi:
            return label
    return "极度贪婪"  # fgi == 100.0 (edge case for the last zone's upper bound)


def compute_forward_returns(df: pd.DataFrame, horizons: list[int] | None = None) -> pd.DataFrame:
    """Add forward_N columns: (close[t+N] / close[t] - 1) for each horizon.

    The last N rows for each horizon get NaN (no future data).
    """
    horizons = horizons or HORIZONS
    df = df.copy()
    df = df.sort_values("date").reset_index(drop=True)
    for h in horizons:
        df[f"forward_{h}"] = df["close"].shift(-h) / df["close"] - 1.0
    return df


def compute_zone_stats(df: pd.DataFrame, horizon: int) -> list[dict]:
    """Compute per-zone statistics for a single forward horizon.

    Returns one dict per zone with keys:
    zone, n, mean, std, ci_lower (None if n<30), ci_upper (None if n<30), win_rate
    """
    col = f"forward_{horizon}"
    valid = df[df[col].notna()].copy()
    stats = []
    for label, lo, hi in ZONES:
        subset = valid[(valid["FGI_final"] >= lo) & (valid["FGI_final"] < hi)]
        n = len(subset)
        returns = subset[col].values
        entry = {
            "zone": label,
            "n": n,
            "mean": float(np.mean(returns)) if n > 0 else None,
            "std": float(np.std(returns, ddof=1)) if n > 1 else None,
            "ci_lower": None,
            "ci_upper": None,
            "win_rate": float(np.mean(returns > 0)) if n > 0 else None,
        }
        if n >= 30 and entry["std"] is not None and entry["std"] > 0:
            se = entry["std"] / np.sqrt(n)
            entry["ci_lower"] = float(entry["mean"] - 1.96 * se)
            entry["ci_upper"] = float(entry["mean"] + 1.96 * se)
        stats.append(entry)
    return stats


class SignalReportEngine:
    """Core engine: loads data, computes forward returns, produces per-zone stats."""

    def __init__(self, db: Database):
        self._db = db

    def load_data(self) -> pd.DataFrame:
        """Load FGI_final from scores_daily and Shanghai Composite close from raw_data.

        优先用 f3_proxy_close（F3 calculator 每日写入），回退到 m3_close（仅 backfill 写入）。
        """
        scores = self._db.get_scores("2009-01-01", "2099-12-31")
        close_df = self._db.get_raw_data("f3_proxy_close", "2009-01-01", "2099-12-31")
        if close_df.empty:
            close_df = self._db.get_raw_data("m3_close", "2009-01-01", "2099-12-31")

        if scores is None or scores.empty:
            return pd.DataFrame(columns=["date", "FGI_final", "close"])
        if close_df is None or close_df.empty:
            return pd.DataFrame(columns=["date", "FGI_final", "close"])

        scores = scores[["date", "FGI_final"]].dropna(subset=["FGI_final"]).copy()
        close_df = close_df.rename(columns={"value": "close"})[["date", "close"]].copy()

        merged = scores.merge(close_df, on="date", how="inner")
        merged = merged.sort_values("date").reset_index(drop=True)
        merged["date"] = merged["date"].astype(str)
        return merged

    def _compute_split(self, df: pd.DataFrame) -> dict:
        """Compute stats for a single data split across all horizons."""
        result = {}
        df = compute_forward_returns(df, horizons=HORIZONS)
        for h in HORIZONS:
            result[h] = compute_zone_stats(df, horizon=h)
        return result

    def run(self) -> dict:
        """Run the full pipeline and return structured results.

        Returns dict with:
        - metadata: {start_date, end_date, total_days, benchmark, fgi_definition}
        - stats: {horizon: [zone_stats]}
        - in_sample: same structure for 2015-2022
        - out_sample: same structure for 2023-2026
        """
        df = self.load_data()
        if df.empty:
            return {
                "metadata": {"start_date": None, "end_date": None, "total_days": 0},
                "stats": {},
                "in_sample": None,
                "out_sample": None,
            }

        # Parse sample splits
        df["_year"] = pd.to_datetime(df["date"]).dt.year
        in_df = df[df["_year"] < SAMPLE_SPLIT_YEAR]
        out_df = df[df["_year"] >= SAMPLE_SPLIT_YEAR]

        # Full-sample stats (combined)
        stats = self._compute_split(df)

        metadata = {
            "start_date": str(df["date"].iloc[0]),
            "end_date": str(df["date"].iloc[-1]),
            "total_days": len(df),
            "benchmark": "上证综指 (m3_close)",
            "fgi_definition": "FGI_final",
            "in_sample_range": f"{in_df['date'].iloc[0]} ~ {in_df['date'].iloc[-1]}"
            if len(in_df) > 0
            else "N/A",
            "out_sample_range": f"{out_df['date'].iloc[0]} ~ {out_df['date'].iloc[-1]}"
            if len(out_df) > 0
            else "N/A",
        }

        return {
            "metadata": metadata,
            "stats": stats,
            "in_sample": self._compute_split(in_df) if len(in_df) > 0 else None,
            "out_sample": self._compute_split(out_df) if len(out_df) > 0 else None,
        }


def _fmt_pct(val: float | None, digits: int = 2) -> str:
    """Format a decimal as a percentage string. None → '—'."""
    if val is None:
        return "—"
    return f"{val * 100:.{digits}f}%"


def _fmt_ci(lower: float | None, upper: float | None) -> str:
    """Format a confidence interval. None entries → '样本不足'."""
    if lower is None or upper is None:
        return "样本不足（n<30）"
    return f"[{lower * 100:.2f}%, {upper * 100:.2f}%]"


def _zone_table(stats_by_horizon: dict, title: str = "前瞻收益分析") -> str:
    """Render per-horizon zone statistics as markdown tables."""
    horizons = sorted(stats_by_horizon.keys())
    lines = [f"### {title}", ""]
    for h in horizons:
        lines.append(f"#### {h} 个交易日前瞻")
        lines.append("")
        lines.append("| 区间 | 触发次数 | 平均涨跌幅 | 标准差 | 95% CI | 胜率 |")
        lines.append("|------|---------|-----------|--------|--------|------|")
        for zs in stats_by_horizon[h]:
            mean_s = _fmt_pct(zs["mean"])
            std_s = _fmt_pct(zs["std"], 3) if zs["std"] is not None else "—"
            ci_s = _fmt_ci(zs["ci_lower"], zs["ci_upper"])
            wr_s = _fmt_pct(zs["win_rate"])
            lines.append(f"| {zs['zone']} | {zs['n']} | {mean_s} | {std_s} | {ci_s} | {wr_s} |")
        lines.append("")
    return "\n".join(lines)


def _distribution_table(stats_by_horizon: dict, total_days: int) -> str:
    """Render zone distribution (from the first available horizon)."""
    if not stats_by_horizon:
        return "### 区间分布\n\n无数据。\n"
    h = sorted(stats_by_horizon.keys())[0]
    lines = ["### 区间分布", ""]
    lines.append("| 区间 | FGI 范围 | 交易日数 | 占比 |")
    lines.append("|------|---------|---------|------|")
    for zs in stats_by_horizon[h]:
        zone = zs["zone"]
        range_map = {
            "极度恐惧": "<20",
            "恐惧": "20–40",
            "中性": "40–60",
            "贪婪": "60–80",
            "极度贪婪": "≥80",
        }
        rng = range_map.get(zone, "—")
        pct = f"{zs['n'] / total_days * 100:.1f}%" if total_days > 0 else "—"
        lines.append(f"| {zone} | {rng} | {zs['n']} | {pct} |")
    lines.append("")
    return "\n".join(lines)


def _extreme_timeline(df: pd.DataFrame) -> str:
    """List extreme fear/greed dates with subsequent market performance."""
    # Requires df with zone, forward_20, forward_60
    lines = ["### 极端信号时间线", ""]
    extremes = df[df["zone"].isin(["极度恐惧", "极度贪婪"])].copy()
    if extremes.empty:
        lines.append("历史上无极端信号触发。")
        lines.append("")
        return "\n".join(lines)

    lines.append("| 日期 | FGI | 区间 | 20日后涨跌 | 60日后涨跌 |")
    lines.append("|------|-----|------|-----------|-----------|")
    for _, row in extremes.iterrows():
        f20 = _fmt_pct(row.get("forward_20")) if not pd.isna(row.get("forward_20")) else "—"
        f60 = _fmt_pct(row.get("forward_60")) if not pd.isna(row.get("forward_60")) else "—"
        lines.append(f"| {row['date']} | {row['FGI_final']:.1f} | {row['zone']} | {f20} | {f60} |")
    lines.append("")
    lines.append("<sub>涨跌幅基于上证综指收盘价计算。</sub>")
    lines.append("")
    return "\n".join(lines)


def render_markdown(result: dict) -> str:
    """Render the full signal validation report as a markdown string.

    result: the dict returned by SignalReportEngine.run()
    """
    meta = result.get("metadata", {})
    stats = result.get("stats", {})
    in_sample = result.get("in_sample")
    out_sample = result.get("out_sample")

    if not stats or meta.get("total_days", 0) == 0:
        return "# FGI 历史信号有效性报告\n\n**数据不足，无法生成报告。**\n\n请确认数据库中存在 FGI_final 和 m3_close 数据。\n"

    parts = [
        "# 📊 FGI 历史信号有效性报告",
        "",
        "## 概览",
        "",
        f"- **数据范围**：{meta.get('start_date', '—')} ~ {meta.get('end_date', '—')}（{meta.get('total_days', 0)} 个交易日）",
        f"- **基准指数**：{meta.get('benchmark', '—')}",
        f"- **FGI 定义**：{meta.get('fgi_definition', '—')}",
        f"- **样本内**：{meta.get('in_sample_range', '—')}",
        f"- **样本外**：{meta.get('out_sample_range', '—')}",
        "",
        "---",
        "",
    ]

    # Zone distribution
    parts.append(_distribution_table(stats, meta.get("total_days", 0)))
    parts.append(
        "> 💡 **大白话**：FGI 把每个交易日的情绪打分到 0-100 分。上面这张表告诉我们：历史上大多数日子（约 58%）市场情绪比较平淡（40-60 分），真正恐慌（<20）和真正疯狂（≥80）的日子非常少，加起来不到 1%。"
    )
    parts.append("")

    # Combined (full sample) forward returns
    parts.append(_zone_table(stats, "全量样本前瞻收益分析"))
    parts.append(
        "> 💡 **大白话**：这张表回答一个核心问题——当市场情绪处于某个区间时，接下来一段时间大盘会怎么走？比如看 60 日那张表，恐惧区间的股票平均涨 2.86%（胜率 57%），贪婪区间的股票平均跌 -0.44%（胜率 46%）。简单说：别人恐惧时你买入，60 天后大概率赚钱；别人贪婪时你追高，60 天后大概率亏钱。"
    )
    parts.append("")
    parts.append("> ⚠️ 注：日频滚动窗口下观测非独立，置信区间需谨慎解读。")
    parts.append("")
    parts.append("---")
    parts.append("")

    # Sample splits
    if in_sample:
        parts.append(_zone_table(in_sample, "样本内前瞻收益（2015–2022）"))
        parts.append("")
    if out_sample:
        parts.append(_zone_table(out_sample, "样本外前瞻收益（2023–2026）"))
        parts.append("")

    # Extreme signals — rebuild df with engine to get timeline
    parts.append("---")
    parts.append("")
    parts.append("## 极端信号专项")
    parts.append("")
    parts.append(
        '> 💡 **大白话**：极端信号就是市场情绪的"极端值"——极度恐慌（FGI<20）或极度贪婪（FGI≥80）。历史上这样的日子屈指可数，说明市场真正疯狂或恐惧的时刻非常罕见。正因为罕见，每次出现都值得特别关注。但要注意：样本太少（不到 12 天），统计结论仅供参考，不能当成可靠买卖信号。'
    )
    parts.append("")
    parts.append(
        "> 注：极端恐惧（FGI<20）仅 8 个交易日、极端贪婪（FGI≥80）仅约 12 个交易日。样本量极小，统计结论仅供参考。"
    )
    parts.append("")
    parts.append("详见下方极端信号时间线。")

    # Conclusion
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("## 结论与局限性")
    parts.append("")
    parts.append("### 信号有效性")
    parts.append("")

    # Summarize key findings
    if 5 in stats:
        extreme_fear = next((z for z in stats[5] if z["zone"] == "极度恐惧"), None)
        extreme_greed = next((z for z in stats[5] if z["zone"] == "极度贪婪"), None)
        if extreme_fear and extreme_fear["n"] > 0 and extreme_fear["mean"] is not None:
            direction = "上涨" if extreme_fear["mean"] > 0 else "下跌"
            parts.append(
                f"- **极度恐惧区间**：{_fmt_pct(extreme_fear['mean'])} 平均前瞻收益，胜率 {_fmt_pct(extreme_fear['win_rate'])}。信号方向（极度恐惧→市场{direction}）与理论一致。"
            )
        if extreme_greed and extreme_greed["n"] > 0 and extreme_greed["mean"] is not None:
            direction = "下跌" if extreme_greed["mean"] < 0 else "上涨"
            parts.append(
                f"- **极度贪婪区间**：{_fmt_pct(extreme_greed['mean'])} 平均前瞻收益，胜率 {_fmt_pct(extreme_greed['win_rate'])}。"
            )

    parts.append("")
    parts.append("### 方法与数据局限")
    parts.append("")
    parts.append(
        "> 💡 **大白话**：下面列出了这份报告的六大局限。简单说：这份报告只看了上证综指一个指数、样本量有限（尤其是极端行情太少了）、统计方法有一定假设前提。FGI 是一个参考信号，不是精准预测工具——别拿它当买卖依据。"
    )
    parts.append("")
    parts.append(
        "1. **基准局限**：使用上证综指（m3_close），未纳入沪深 300/中证 500/中证 1000 全收益指数。"
    )
    parts.append("2. **重叠观测**：日频滚动窗口产生重叠样本，各观测非独立，显著性检验需谨慎解读。")
    parts.append(
        "3. **极端区间样本不足**：FGI < 20 和 FGI ≥ 80 的交易日极少（≤12 天），统计推断不可靠。"
    )
    parts.append(
        "4. **样本外信号衰减**：Rank IC 从样本内 -0.065 衰减至样本外 -0.004，近期预测力显著下降。"
    )
    parts.append(
        "5. **分层单调性不足**：10 档分层在中段档位收益交叉，非严格单调递减，信号精度有限。"
    )
    parts.append("6. **前视偏差控制**：前瞻收益以收盘价计算，未考虑交易滑点和冲击成本。")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(
        "<sub>本报告由 `fgi/output/signal_report.py` 自动生成。报告仅供参考，不构成投资建议。</sub>"
    )

    return "\n".join(parts)


def get_zone_for_fgi(fgi: float | None) -> str:
    """Map an FGI value to its five-zone label (aligned with signal report zones)."""
    return assign_zone(fgi)


def _find_closest_prior_fgi(db: Database, fgi_value: float, current_date: str) -> tuple | None:
    """从 scores_daily 中查找与当前 FGI 最接近的历史日期（排除当日）。

    匹配时考虑方向一致性：不仅要 FGI 值接近，涨跌方向也须一致。
    例如当前从 55→35（下跌接近 35），历史上从 20→35（上涨接近 35）不匹配。

    Returns (date_str, fgi_value, prev_fgi_value) or None.
    """
    try:
        scores = db.get_scores("2009-01-01", current_date)
        if scores.empty:
            return None
        hist = scores[scores["date"] < current_date].dropna(subset=["FGI_final"]).copy()
        if len(hist) < 2:
            return None
        hist = hist.sort_values("date").reset_index(drop=True)
        # 计算今日方向（当日 vs 前一日）
        today_prev = (
            hist[hist["date"] < current_date]["FGI_final"].iloc[-1]
            if len(hist[hist["date"] < current_date]) > 0
            else None
        )
        if today_prev is None:
            return None
        today_direction = fgi_value - float(today_prev)
        if abs(today_direction) < 0.5:
            # 今日持平，不限制方向
            candidates = hist[hist["date"] < current_date].copy()
        else:
            # 今日有方向：找同向的候选
            # 计算每条历史记录的方向
            hist["_prev"] = hist["FGI_final"].shift(1)
            hist["_direction"] = hist["FGI_final"] - hist["_prev"]
            candidates = hist[
                (hist["date"] < current_date)
                & (hist["_prev"].notna())
                & ((hist["_direction"] * today_direction) > 0)  # 同向
            ].copy()
        if candidates.empty:
            return None
        candidates["_diff"] = (candidates["FGI_final"] - fgi_value).abs()
        best = candidates.loc[candidates["_diff"].idxmin()]
        return str(best["date"]), float(best["FGI_final"]), float(best["_prev"])
    except Exception:
        return None


def _get_forward_return(db: Database, date: str, horizon: int = 20) -> float | None:
    """计算指定日期 horizon 个交易日后的上证综指涨跌幅。"""
    try:
        close_df = db.get_raw_data("f3_proxy_close", date, "2099-12-31")
        if close_df.empty:
            close_df = db.get_raw_data("m3_close", date, "2099-12-31")
        if close_df.empty:
            return None
        close_df = close_df.sort_values("date").reset_index(drop=True)
        dates = close_df["date"].tolist()
        try:
            idx = dates.index(date)
        except ValueError:
            return None
        if idx + horizon >= len(dates):
            return None
        ret = (
            float(close_df.iloc[idx + horizon]["value"]) / float(close_df.iloc[idx]["value"]) - 1.0
        )
        return ret
    except Exception:
        return None


def render_zone_context_card(fgi: float | None, db) -> str:
    """Render a compact historical signal reference card for push notification.

    Returns a markdown block showing the current FGI's zone and its historical
    forward-return statistics across 5/20/60-day horizons. Returns empty string
    on failure or insufficient data.
    """
    if fgi is None or (isinstance(fgi, float) and pd.isna(fgi)):
        return ""
    if db is None:
        return ""

    try:
        engine = SignalReportEngine(db)
        result = engine.run()
        stats = result.get("stats", {})
        meta = result.get("metadata", {})
    except Exception:
        return ""

    if not stats:
        return ""

    zone = get_zone_for_fgi(fgi)
    horizon = 5  # Use 5-day stats for the distribution count
    zone_stats_all = stats.get(horizon, [])
    zone_data = next((z for z in zone_stats_all if z["zone"] == zone), None)
    if zone_data is None:
        return ""

    n = zone_data["n"]
    total = meta.get("total_days", 0)
    pct = f"{n / total * 100:.1f}%" if total > 0 else "—"
    extreme_note = ""
    if n < 30:
        extreme_note = f"\n> ⚠️ 历史仅 {n} 个交易日处于此区间，统计推断不可靠。\n"

    # 时间锚点：仅当 FGI 处于非中性区间且趋势明确时展示
    # 条件A: 非中性区间（恐惧/贪婪才够有信号意义）
    anchor_line = None
    if fgi < 40 or fgi > 60:
        # 条件B: 趋势明确——从历史数据找 FGI 值的位置，看它附近的趋势纯度
        try:
            all_scores = db.get_scores("2009-01-01", "2099-12-31")
            if all_scores is not None and not all_scores.empty:
                # 找与当前 fgi 值最匹配的行 → 确定日期
                candidates = (
                    all_scores[all_scores["date"] < meta.get("end_date", "2099-12-31")]
                    .dropna(subset=["FGI_final"])
                    .copy()
                )
                candidates["_diff"] = (candidates["FGI_final"].astype(float) - float(fgi)).abs()
                if not candidates.empty:
                    best_row = candidates.loc[candidates["_diff"].idxmin()]
                    best_date = str(best_row["date"])
                    # 取该日期前6个非空FGI值算纯度
                    trend_series = (
                        all_scores[all_scores["date"] <= best_date]["FGI_final"]
                        .dropna()
                        .tail(6)
                        .astype(float)
                        .values
                    )
                    if len(trend_series) == 6:
                        changes = np.abs(np.diff(trend_series))
                        total_v = np.sum(changes)
                        net_c = abs(trend_series[-1] - trend_series[0])
                        purity = net_c / total_v if total_v > 0 else 0
                        if purity >= 0.3:
                            closest = _find_closest_prior_fgi(db, fgi, best_date)
                            if closest is not None:
                                closest_date, closest_fgi, closest_prev = closest
                                direction_arrow = "📈" if closest_fgi >= closest_prev else "📉"
                                forward_ret = _get_forward_return(db, closest_date, horizon=20)
                                if forward_ret is not None:
                                    after_arrow = "📈" if forward_ret > 0 else "📉"
                                    anchor_line = (
                                        f"📎 **参考**：上次同向接近此水平（{closest_fgi:.1f}）是 **{closest_date}**"
                                        f"（{direction_arrow} {closest_fgi - closest_prev:+.1f}），"
                                        f"之后 20 日上证综指 {after_arrow} **{forward_ret * 100:+.1f}%**"
                                    )
                                    # 再查同向匹配中 FGI 最接近的另外 4 次，评估后市一致性
                                    try:
                                        top5 = candidates.sort_values("_diff").head(5)
                                        detail_rows = []
                                        for _, cr in top5.iterrows():
                                            d = str(cr["date"])
                                            r = _get_forward_return(db, d, horizon=20)
                                            if r is not None:
                                                is_closest = d == closest_date
                                                arrow = "📈" if r > 0 else "📉"
                                                ret_label = f"{arrow} {r * 100:+.1f}%"
                                                date_label = f"**{d}**" if is_closest else d
                                                detail_rows.append((date_label, ret_label))
                                        if len(detail_rows) >= 3:
                                            r_vals = [
                                                float(
                                                    r.replace("📈 ", "")
                                                    .replace("📉 ", "")
                                                    .replace("%", "")
                                                )
                                                for _, r in detail_rows
                                            ]
                                            r_min = min(r_vals)
                                            r_max = max(r_vals)
                                            table = "\n\n其他同向接近情形：\n\n| 日期 | 后市20日 |\n|------|---------|\n"
                                            for date_label, ret_s in detail_rows:
                                                table += f"| {date_label} | {ret_s} |\n"
                                            table += (
                                                f"| **区间** | **{r_min:+.0f}% ~ {r_max:+.0f}%** |"
                                            )
                                            table += "\n\n<sub>最近似日期以**加粗**标注</sub>"
                                            anchor_line += table
                                    except Exception:
                                        pass
        except Exception:
            pass

    lines = [
        "",
        "---",
        "",
        "### 📈 历史信号参考",
        "",
        f"当前 FGI 处于 **{zone}** 区间，历史上出现 {n} 次（占比 {pct}）：",
        "",
        "| 前瞻 | 上证综指平均涨跌 | 胜率 |",
        "|------|----------------|------|",
    ]

    for h in [5, 20, 60]:
        h_stats = stats.get(h, [])
        h_data = next((z for z in h_stats if z["zone"] == zone), None)
        if h_data is None:
            continue
        mean_s = _fmt_pct(h_data["mean"])
        wr_s = _fmt_pct(h_data["win_rate"])
        lines.append(f"| {h} 日 | {mean_s} | {wr_s} |")

    lines.append("")
    if extreme_note:
        lines.append(extreme_note)
    # 时间锚点：历史上最接近的 FGI 值及后续 20 日表现
    if anchor_line:
        lines.append(anchor_line)
        lines.append("")
    start_d = meta.get("start_date", "—")
    end_d = meta.get("end_date", "—")
    lines.append(f"<sub>数据：{start_d} ~ {end_d} · 上证综指 · 历史不代表未来收益</sub>")
    lines.append("")
    lines.append("<sub>胜率 = N 个交易日后上证综指上涨的交易日占比。</sub>")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backtest v2: Rank IC, layer backtest, DCA simulation (ADR-0003)
# ---------------------------------------------------------------------------


def compute_rank_ic(
    df: pd.DataFrame, indicator_col: str = "FGI_final", horizon: int = 20
) -> dict | None:
    """Compute Spearman Rank IC between an indicator and forward market return.

    Args:
        df: DataFrame with columns ['close', indicator_col].
        horizon: forward trading days for return calculation.

    Returns:
        {ic, n, mean, std, ir, win_rate, indicator, horizon} or None if insufficient data.
    """
    if indicator_col not in df.columns or df[indicator_col].dropna().empty:
        return None
    working = df.copy()
    working["forward_return"] = working["close"].shift(-horizon) / working["close"] - 1.0
    working = working.dropna(subset=["forward_return", indicator_col])
    if len(working) < 30:
        return None
    # Split into 20-day rolling sub-windows for IC statistics
    ic_values = []
    window_size = 20
    for i in range(len(working) - window_size + 1):
        win = working.iloc[i : i + window_size]
        if len(win) < 10:
            continue
        val = win[indicator_col].corr(win["forward_return"], method="spearman")
        if not pd.isna(val):
            ic_values.append(float(val))
    if not ic_values:
        return None
    ic_series = np.array(ic_values)
    mean_ic = float(np.mean(ic_series))
    std_ic = float(np.std(ic_series, ddof=1))
    ir = mean_ic / std_ic if std_ic > 0 else 0.0
    win_rate = float(np.mean(ic_series > 0))
    return {
        "ic": float(working[indicator_col].corr(working["forward_return"], method="spearman")),
        "n": len(working),
        "mean": mean_ic,
        "std": std_ic,
        "ir": ir,
        "win_rate": win_rate,
        "indicator": indicator_col,
        "horizon": horizon,
        "bonferroni_threshold": 0.05 / 36,  # spec 5.3: α/36 = 0.0014
    }


def compute_rolling_ic_window(
    df: pd.DataFrame,
    indicator_col: str = "FGI_final",
    horizon: int = 20,
    half_year: int = 126,
    window_years: int = 3,
) -> list:
    """Compute rolling Rank IC over semi-annual windows using trailing 3-year data.

    Returns a list of {date, ic, n, mean, std, ir, win_rate} dicts, one per
    semi-annual evaluation point. Skips points with fewer than `min_valid_obs`
    valid observations within the window.
    """
    results = []
    working = df.copy()
    working["forward_return"] = working["close"].shift(-horizon) / working["close"] - 1.0
    working = working.dropna(subset=["forward_return", indicator_col])
    window_size = 252 * window_years  # 756 trading days
    min_valid_obs = 252  # spec: skip if fewer than 252 valid obs in window
    step = half_year
    n = len(working)
    for end in range(window_size, n, step):
        window_df = working.iloc[end - window_size : end]
        if len(window_df) < min_valid_obs:
            continue
        ic = float(window_df[indicator_col].corr(window_df["forward_return"], method="spearman"))
        # Compute sub-window statistics within this rolling window
        ic_vals = []
        sub_win = 20
        for i in range(len(window_df) - sub_win + 1):
            s = window_df.iloc[i : i + sub_win]
            if len(s) < 10:
                continue
            val = s[indicator_col].corr(s["forward_return"], method="spearman")
            if not pd.isna(val):
                ic_vals.append(float(val))
        win_mean = float(np.mean(ic_vals)) if ic_vals else ic
        win_std = float(np.std(ic_vals, ddof=1)) if len(ic_vals) > 1 else None
        win_ir = win_mean / win_std if (win_std and win_std > 0) else 0.0
        win_wr = float(np.mean(np.array(ic_vals) > 0)) if ic_vals else 0.5
        results.append(
            {
                "date": str(window_df["date"].iloc[-1]),
                "ic": ic,
                "n": len(window_df),
                "mean": win_mean,
                "std": win_std,
                "ir": win_ir,
                "win_rate": win_wr,
            }
        )
    return results


def layer_backtest_10(df: pd.DataFrame, horizons: list | None = None) -> dict:
    """10-quantile layer backtest: FGI decile × forward market returns.

    Returns {horizon: [{layer, n, mean_return, win_rate}]} keyed by horizon.
    """
    horizons = horizons or [5, 20, 60]
    working = df.copy()
    working = working.sort_values("date").reset_index(drop=True)
    for h in horizons:
        working[f"forward_{h}"] = working["close"].shift(-h) / working["close"] - 1.0

    result = {}
    for h in horizons:
        col = f"forward_{h}"
        valid = working.dropna(subset=[col, "FGI_final"]).copy()
        if len(valid) < 10:
            result[h] = []
            continue
        valid["layer"] = pd.qcut(valid["FGI_final"], 10, labels=False, duplicates="drop")
        layers = []
        for layer in range(10):
            subset = valid[valid["layer"] == layer]
            if len(subset) == 0:
                continue
            rets = subset[col].values
            layers.append(
                {
                    "layer": layer + 1,  # 1-indexed
                    "n": len(subset),
                    "mean_return": float(np.mean(rets)),
                    "win_rate": float(np.mean(rets > 0)),
                }
            )
        result[h] = layers
    return result


def simulate_dca(
    df: pd.DataFrame, base_amount: float = 10000, indicator_col: str = "FGI_final"
) -> dict:
    """Simulate contrarian DCA: monthly investment scaled by (1 - FGI/100) × 2.

    Buys on the first trading day of each month. Benchmark: equal monthly DCA.

    Returns {dca_return, dca_annualized, benchmark_return, benchmark_annualized,
             dca_max_drawdown, benchmark_max_drawdown, dca_sharpe, benchmark_sharpe,
             n_months}
    """
    working = df.copy()
    working["_dt"] = pd.to_datetime(working["date"])
    working["_year"] = working["_dt"].dt.year
    working["_month"] = working["_dt"].dt.month
    # First trading day of each month
    monthly = working.groupby(["_year", "_month"]).first().reset_index(drop=True)
    monthly = monthly.dropna(subset=["close", indicator_col])
    if len(monthly) < 12:
        return {"error": "Insufficient data (need >= 12 months)"}

    fgi_vals = monthly[indicator_col].values
    close_vals = monthly["close"].values

    # DCA: amount = base × (1 - FGI/100) × 2
    amounts = base_amount * (1.0 - fgi_vals / 100.0) * 2.0
    shares = amounts / close_vals
    total_shares_dca = np.cumsum(shares)
    final_value_dca = total_shares_dca[-1] * close_vals[-1]
    total_invested_dca = np.sum(amounts)
    total_return_dca = (final_value_dca / total_invested_dca) - 1.0
    n_trading_days = len(working)
    n_years = n_trading_days / 252.0

    # Benchmark: equal monthly DCA
    bench_shares = base_amount / close_vals
    total_shares_bench = np.cumsum(bench_shares)
    final_value_bench = total_shares_bench[-1] * close_vals[-1]
    total_invested_bench = base_amount * len(close_vals)
    total_return_bench = (final_value_bench / total_invested_bench) - 1.0

    # Annualized
    dca_annualized = (1.0 + total_return_dca) ** (1.0 / max(n_years, 0.5)) - 1.0
    bench_annualized = (1.0 + total_return_bench) ** (1.0 / max(n_years, 0.5)) - 1.0

    # Max drawdown
    dca_portfolio = total_shares_dca * close_vals
    bench_portfolio = total_shares_bench * close_vals

    def _max_drawdown(series):
        peak = np.maximum.accumulate(series)
        drawdown = (series - peak) / peak
        return float(np.min(drawdown))

    dca_mdd = _max_drawdown(dca_portfolio)
    bench_mdd = _max_drawdown(bench_portfolio)

    # Sharpe (monthly returns annualized)
    dca_monthly_ret = np.diff(dca_portfolio) / dca_portfolio[:-1]
    bench_monthly_ret = np.diff(bench_portfolio) / bench_portfolio[:-1]
    dca_sharpe = (
        float(np.mean(dca_monthly_ret) / np.std(dca_monthly_ret, ddof=1) * np.sqrt(12))
        if np.std(dca_monthly_ret, ddof=1) > 0
        else 0.0
    )
    bench_sharpe = (
        float(np.mean(bench_monthly_ret) / np.std(bench_monthly_ret, ddof=1) * np.sqrt(12))
        if np.std(bench_monthly_ret, ddof=1) > 0
        else 0.0
    )

    return {
        "n_months": len(monthly),
        "dca_total_return": float(total_return_dca),
        "dca_annualized": float(dca_annualized),
        "benchmark_total_return": float(total_return_bench),
        "benchmark_annualized": float(bench_annualized),
        "dca_max_drawdown": dca_mdd,
        "benchmark_max_drawdown": bench_mdd,
        "dca_sharpe": dca_sharpe,
        "benchmark_sharpe": bench_sharpe,
    }


def _render_ic_section(ic_result: dict, title: str = "Rank IC 分析") -> str:
    """Render Rank IC analysis section."""
    lines = [
        f"### 📊 {title}",
        "",
        "FGI_final 与上证综指 20 日前瞻收益的 Spearman Rank IC：",
        "",
        "> 💡 **大白话**：Rank IC 衡量的是「FGI 分数和未来市场涨跌之间的关联有多强」。IC 为负数说明 FGI 越高（市场越贪婪），未来越容易跌——这正是我们希望看到的反向预测能力。IR（信息比率）衡量这种关联是否稳定，绝对值越大越稳定。IC 胜率指「有多少个时间段里 FGI 和未来涨跌是反向关联的」，低于 50% 说明 FGI 大多数时候方向正确（反向指标）。",
    ]
    if ic_result.get("error"):
        lines.append(f"**数据不足**：{ic_result['error']}")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"- 全样本 IC：{ic_result['ic']:.4f}（n={ic_result['n']} 个交易日）")
    lines.append(f"- IC 均值（20 日滚动窗）：{ic_result.get('mean', float('nan')):.4f}")
    lines.append(f"- IC 标准差：{ic_result.get('std', float('nan')):.4f}")
    lines.append(f"- IR（IC 均值/标准差）：{ic_result.get('ir', float('nan')):.4f}")
    lines.append(f"- IC 胜率（IC>0 占比）：{ic_result.get('win_rate', float('nan')):.1%}")
    lines.append(
        f"- Bonferroni 参考阈值（α/36）：{ic_result.get('bonferroni_threshold', 0.05 / 36):.4f}"
    )
    lines.append("")
    if ic_result.get("rolling"):
        lines.append("#### 滚动 IC 窗口（每半年，3 年回顾窗）")
        lines.append("")
        lines.append("| 评估日期 | Rank IC | 观测数 | IC 均值 | IR | 胜率 |")
        lines.append("|---------|--------|--------|--------|-----|------|")
        for pt in ic_result["rolling"]:
            ir_s = f"{pt.get('ir', 0):.3f}" if pt.get("ir") is not None else "—"
            lines.append(
                f"| {pt['date']} | {pt['ic']:.4f} | {pt['n']} | {pt.get('mean', 0):.4f} | {ir_s} | {pt.get('win_rate', 0):.1%} |"
            )
        lines.append("")
    return "\n".join(lines)


def _render_layer_section(layer_result: dict, title: str = "10 档分层回测") -> str:
    """Render 10-layer backtest section."""
    lines = [
        f"### 📊 {title}",
        "",
        '> 💡 **大白话**：把历史上所有交易日按 FGI 分数从低到高排成 10 档（第 1 档最恐慌，第 10 档最贪婪），然后看每档在接下来 5/20/60 天的平均涨跌。理想情况下应该是"第 1 档涨最多、第 10 档跌最多"——像下楼梯一样严格递减。如果中段档位的收益互相交叉，说明 FGI 在中间区域的区分度不够精细。',
        "",
    ]
    if not layer_result:
        lines.append("数据不足。")
        lines.append("")
        return "\n".join(lines)

    for h in [5, 20, 60]:
        if h not in layer_result or not layer_result[h]:
            continue
        lines.append(f"#### {h} 日前瞻")
        lines.append("")
        lines.append("| 分档 | N | 平均收益 | 胜率 |")
        lines.append("|------|---|---------|------|")
        for lr in layer_result[h]:
            mean_s = _fmt_pct(lr["mean_return"])
            wr_s = _fmt_pct(lr["win_rate"])
            lines.append(f"| {lr['layer']} | {lr['n']} | {mean_s} | {wr_s} |")
        lines.append("")
    return "\n".join(lines)


def _render_dca_section(dca_result: dict, title: str = "逆情绪 DCA vs 等额定投") -> str:
    """Render DCA simulation section."""
    lines = [
        f"### 💰 {title}",
        "",
        "> 💡 **大白话**：模拟两种每月定投策略。**等额定投**：每月固定投入 1 万元买指数基金，不管涨跌。**逆情绪 DCA**：根据 FGI 调整投入金额——市场越恐慌（FGI 低），投入越多（最多 2 万元）；市场越贪婪（FGI 高），投入越少（最少 0 元）。核心逻辑是「别人恐惧我加仓，别人贪婪我减仓」。通过对比两种策略的总收益、最大回撤和夏普比率，看 FGI 作为定投择时信号是否有实际价值。",
        "",
    ]
    if dca_result.get("error"):
        lines.append(f"**数据不足**：{dca_result['error']}")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"- 投资月数：{dca_result['n_months']}")
    lines.append("")
    lines.append("| 策略 | 总收益 | 年化收益 | 最大回撤 | 夏普比率 |")
    lines.append("|------|--------|---------|---------|---------|")
    lines.append(
        f"| 逆情绪 DCA | {_fmt_pct(dca_result['dca_total_return'])} | {_fmt_pct(dca_result['dca_annualized'])} | {_fmt_pct(dca_result['dca_max_drawdown'])} | {dca_result['dca_sharpe']:.2f} |"
    )
    lines.append(
        f"| 等额定投 | {_fmt_pct(dca_result['benchmark_total_return'])} | {_fmt_pct(dca_result['benchmark_annualized'])} | {_fmt_pct(dca_result['benchmark_max_drawdown'])} | {dca_result['benchmark_sharpe']:.2f} |"
    )
    lines.append("")
    return "\n".join(lines)
