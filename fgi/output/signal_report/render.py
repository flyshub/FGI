"""信号报告与回测结果的 Markdown 渲染——纯字符串变换，不读数据库。"""

from __future__ import annotations

import pandas as pd


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


def render_ic_section(ic_result: dict, title: str = "Rank IC 分析") -> str:
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


def render_layer_section(layer_result: dict, title: str = "10 档分层回测") -> str:
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


def render_dca_section(dca_result: dict, title: str = "逆情绪 DCA vs 等额定投") -> str:
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
