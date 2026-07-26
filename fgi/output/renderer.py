"""Pure rendering functions for FGI daily push notification.

No database access, no HTTP — pure data → Markdown/HTML transformation.
All data must be pre-fetched and passed in.
"""
from __future__ import annotations

from typing import Optional

from fgi.output.decision_matrix import QUADRANT_TABLE, QUADRANT_EMOJI

# --- Static mapping data ---

INDICATOR_NAMES = {
    "M1": "涨停板家数", "M2": "散户意愿", "M3": "偏离60日均线", "M4": "创业板成交活跃度",
    "S2": "股吧热度", "S3": "涨停封单量",
    "V1": "沪深300风险溢价", "V2": "ΔERP Z-score", "V4": "期权隐含波动率",
    "F1": "融资余额占比", "F2": "基金股票仓位", "F3": "主力资金板块偏好",
}

DIMENSION_NAMES = {
    "momentum": "动量", "sentiment": "情绪",
    "valuation": "估值", "volatility": "波动率", "funding": "资金",
}

DIMENSION_INDICATORS = {
    "momentum": ["M1", "M2", "M3", "M4"],
    "sentiment": ["S2", "S3"],
    "valuation": ["V1", "V2"],
    "volatility": ["V4"],
    "funding":  ["F1", "F2", "F3"],
}

_DIM_COLORS = {
    "momentum": "#E8F4FD",
    "sentiment": "#FDE8E8",
    "valuation": "#E8F5E9",
    "volatility": "#F3E8FD",
    "funding": "#FFF8E1",
}

STATUS_LABELS = {
    "normal": "",          # 不展示，默认就是好
    "degraded": "⚠️",       # 数据降级（2+天延迟）
    "missing":  "❌",       # 数据缺失
}

FGI_LEVELS = [
    (20, "极度恐惧"), (40, "恐惧"), (60, "中性"), (80, "贪婪"),
]

_CHANGE_DEFS = {
    "涨停板家数": "当日沪深两市涨停个股数量的5年滚动百分位。读数高=打板情绪火热；读数低=市场冷清。",
    "散户意愿": "融资余额占流通市值比例的5年滚动百分位，反映散户杠杆入市意愿。读数高=散户看多；读数低=散户离场。",
    "偏离60日均线": "收盘价与60日均线偏离度的5年滚动百分位。读数高=正偏离(趋势过强)；读数低=负偏离(超跌)。",
    "创业板成交活跃度": "创业板成交量占全市场成交量的5年滚动百分位。读数高=资金集中于创业板；读数低=资金流出。",
    "股吧热度": "东方财富个股吧发帖量的5年滚动百分位。读数高=讨论活跃情绪高涨；读数低=市场关注度低。",
    "涨停封单量": "当日涨停个股封单总金额的5年滚动百分位。读数高=封板意愿强；读数低=封板资金弱。",
    "沪深300风险溢价": "沪深300市盈率倒数减10年期国债收益率(ERP)的5年滚动百分位反向得分。读数高=股票性价比强；读数低=债券相对有吸引力。",
    "ΔERP Z-score": "ERP的日度Z-score，衡量股债性价比偏离历史均值的标准差倍数。正值=股票性价比偏强；负值=债券偏强。",
    "期权隐含波动率": "50ETF期权隐含波动率(QVIX)的5年滚动百分位反向得分。高VIX=恐慌；低VIX=平静。",
    "融资余额占比": "融资余额占全市场流通市值的5年滚动百分位。读数高=杠杆资金看好后市；读数低=杠杆资金收缩。",
    "基金股票仓位": "公募基金股票仓位百分比的5年滚动百分位。读数高=机构看多；读数低=机构减仓。",
    "主力资金板块偏好": "主力资金在行业板块间净流入的集中度百分位。读数高=集中布局某板块；读数低=分散或整体流出。",
}

_SUBSTITUTE_DESC = {
    "F3": "用上证指数当日涨跌幅 × 成交量估算主力资金净流向，替代 AKShare stock_market_fund_flow 的真实资金流数据",
}

_INDICATOR_DIM = {}
for _dim, _inds in DIMENSION_INDICATORS.items():
    for _name in _inds:
        _INDICATOR_DIM[_name] = DIMENSION_NAMES[_dim]


# --- Pure rendering functions ---

def fgi_level(fgi: float) -> str:
    for threshold, label in FGI_LEVELS:
        if fgi < threshold:
            return label
    return "极度贪婪"


def score_bar(score: float, width: int = 8) -> str:
    """mini bar chart for score (0-100)."""
    filled = max(1, round(score / 100 * width))
    return "█" * filled + "░" * (width - filled)


def data_cell(source_date: str, status: str) -> str:
    """Format data column with optional annotation for filled/proxied data."""
    if not source_date:
        return ""
    note = ""
    if status == "degraded":
        note = '<span style="color:#999;font-size:0.85em">（前向填充）</span>'
    elif status == "substituted":
        note = '<span style="color:#999;font-size:0.85em">（替代指标）</span>'
    return f'{source_date}{note}'


def fgi_trend(fgi: float, prev_scores: dict | None) -> str:
    prev_fgi = prev_scores.get("FGI_final") if prev_scores else None
    if prev_fgi is None:
        return ""
    delta = fgi - prev_fgi
    if abs(delta) < 0.5:
        return f"→ {prev_fgi:.1f} → {fgi:.1f}（持平）"
    arrow = "🔼" if delta > 0 else "🔽"
    return f"{arrow} {delta:+.1f}（昨日: {prev_fgi:.1f} · 今日: {fgi:.1f}）"


def fgi_header(
    fgi: float, health: float, date_str: str,
    indicator_results: dict | None = None,
    prev_scores: dict | None = None,
    hist_fgi_percentile: tuple[str, str] | None = None,
    movers: list | None = None,
) -> str:
    level = fgi_level(fgi)
    bar = score_bar(fgi, 20)
    pos, extreme_note = hist_fgi_percentile or ("暂无历史参考", "")
    trend = fgi_trend(fgi, prev_scores)

    issues = []
    if indicator_results:
        for name, r in indicator_results.items():
            st = r.get("status") if isinstance(r, dict) else None
            if st == "degraded":
                issues.append(f"{INDICATOR_NAMES.get(name, name)} 前向填充")
            elif st == "missing":
                issues.append(f"{INDICATOR_NAMES.get(name, name)} 缺失")
            elif st == "substituted":
                issues.append(f"{INDICATOR_NAMES.get(name, name)} 替代估算")

    from fgi.config.settings import HEALTHY_THRESHOLD
    health_label = f"**{health:.0f}** / 100"
    if issues:
        health_label += "（" + " · ".join(issues) + "）"
    if health < HEALTHY_THRESHOLD:
        health_label += " ⚠️ 数据质量异常，仅供参考"

    rows = [
        f"| 当前情绪 | **{level}** |",
    ]
    if trend:
        rows.append(f"| 趋势 | {trend} |")
    rows += [
        f"| 数据健康度 | {health_label} |",
        f"| 历史位置 | {pos} |",
    ]
    if extreme_note:
        rows.append(f"| 注意 | {extreme_note} |")

    return "\n".join([
        f"## 📊 A股恐贪指数 · {date_str}",
        "",
        f"### FGI: {fgi:.1f}",
        "",
        f"`{bar} `",
        "",
        f"| 项目 | 值 |",
        f"|------|----|",
        *rows,
    ])


def decision_matrix_section(dm: dict) -> str:
    """决策矩阵块：3×3 网格 + 软性建议。"""
    fgi = dm.get("fgi")
    sent = dm.get("sentiment_tier", "")
    val = dm.get("valuation_tier", "")
    val_pct = dm.get("valuation_pct")
    pe_pct = dm.get("pe_pct")
    pb_pct = dm.get("pb_pct")
    quadrant = dm.get("quadrant", "")
    advice = dm.get("advice", "")

    fgi_str = f"{fgi:.1f}" if fgi is not None else "—"
    val_pct_str = f"{val_pct*100:.0f}%" if val_pct is not None else "—"
    pe_pct_str = f"{pe_pct*100:.0f}%" if pe_pct is not None else "—"
    pb_pct_str = f"{pb_pct*100:.0f}%" if pb_pct is not None else "—"

    emoji = QUADRANT_EMOJI.get(quadrant, "❓")

    # 3x3 矩阵，当前象限高亮
    def cell(s: str, v: str, hl: bool) -> str:
        bg = "#FFE082" if hl else "#Fff"
        s_str = f"<strong>{s}</strong>" if hl else s
        v_str = f"<strong>{v}</strong>" if hl else v
        return f'<td style="padding:6px 10px;border:1px solid #e0e0e0;background:{bg};text-align:center;color:#222">{s_str}<br><span style="font-size:0.85em;color:#444">{v_str}</span></td>'

    cur_sent = sent
    cur_val = val
    sents_raw = ["恐惧", "中性", "贪婪"]
    sents_display = ["恐惧(<35)", "中性(35-65)", "贪婪(>65)"]
    vals = ["低估", "合理", "高估"]
    html = ['<table style="width:100%">',
            '<tr style="background:#ececec"><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#222;font-weight:700">情绪＼估值<sup>沪深300</sup></th>'
            '<th style="padding:6px 10px;border:1px solid #e0e0e0;color:#222;font-weight:700">低估(&lt;25%)</th>'
            '<th style="padding:6px 10px;border:1px solid #e0e0e0;color:#222;font-weight:700">合理(25-75%)</th>'
            '<th style="padding:6px 10px;border:1px solid #e0e0e0;color:#222;font-weight:700">高估(&gt;75%)</th></tr>']
    for si, s in enumerate(sents_raw):
        cells = [f'<td style="padding:6px 10px;border:1px solid #e0e0e0;background:#ececec;font-weight:700;color:#222">{sents_display[si]}</td>']
        for v in vals:
            q, _ = QUADRANT_TABLE.get((s, v), ("?", ""))
            cells.append(cell(q, "", s == cur_sent and v == cur_val))
        html.append("<tr>" + "".join(cells) + "</tr>")
    html.append("</table>")

    lines = [
        "### 🎯 情绪-估值决策矩阵",
        "",
        "\n".join(html),
        "",
        f"- 当前象限：{emoji} **{quadrant}**（情绪 {sent} · 估值 {val}）",
        f"- 情绪 FGI：{fgi_str}",
        f"- 估值分位（沪深300）：{val_pct_str}（PE {pe_pct_str} · PB {pb_pct_str}）",
        f"- 建议：{advice}",
        "",
        "<sub>※ 决策矩阵为情绪-估值辅助工具，软性建议不构成投资指令</sub>",
    ]
    return "\n".join(lines)


def build_fgi_markdown(
    fgi_raw: float, dimension_scores: dict, indicator_results: dict,
    health: float, date_str: str,
    prev_scores: dict | None = None,
    fgi_percentile_result: tuple[str, str] | None = None,
    movers: list | None = None,
    decision_matrix: Optional[dict] = None,
    signal_card: str = "",
) -> str:
    """主渲染函数：接收预计算数据，返回完整 Markdown 内容。"""
    parts = [fgi_header(fgi_raw, health, date_str, indicator_results, prev_scores, fgi_percentile_result, movers), "", "---", ""]

    # --- 决策矩阵 ---
    if decision_matrix:
        parts.append(decision_matrix_section(decision_matrix))
        parts.append("")

    # --- 历史信号参考 ---
    if signal_card:
        parts.append(signal_card)

    parts.append("")
    parts.append("---")
    parts.append("")

    # --- 指标明细 (HTML table with colored rows) ---
    parts.append("### 🔍 各维度指标明细")
    parts.append("")

    html = ['<table style="width:100%">', '<tr style="background:#ececec"><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">维度</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">名称</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">得分</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">数据</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">状态</th></tr>']
    from fgi.common.utils import extract_indicator_score
    for dim, ilist in DIMENSION_INDICATORS.items():
        bg = f' style="background:{_DIM_COLORS[dim]}"'
        dim_label = DIMENSION_NAMES[dim]
        for i, name in enumerate(ilist):
            r = indicator_results.get(name, {})
            score = extract_indicator_score(r, name)
            s_str = f"{score:.0f}" if score is not None else '<span style="color:#999">—</span>'
            status = r.get("status", "?")
            src_date = r.get("source_date") or date_str
            tag = STATUS_LABELS.get(status, "")
            dim_cell = f"<b>{dim_label}</b>" if i == 0 else ""
            data_cell_val = data_cell(src_date, status)
            html.append(f'<tr{bg}><td style="padding:6px 10px;border:1px solid #e0e0e0;font-weight:700;color:#222">{dim_cell}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#333">{INDICATOR_NAMES[name]}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;font-weight:600;color:#222;white-space:nowrap">{s_str}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-size:0.9em">{data_cell_val}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center">{tag}</td></tr>')
    html.append("</table>")
    parts.append("\n".join(html))

    # --- 维度汇总 ---
    parts.append("")
    parts.append("### 📐 维度汇总")
    parts.append("")
    dhtml = ['<table style="width:100%">', '<tr style="background:#ececec"><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">维度</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">得分</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">权重</th></tr>']
    for dim in DIMENSION_INDICATORS:
        bg = f' style="background:{_DIM_COLORS[dim]}"'
        score = dimension_scores.get(dim)
        s_str = f"{score:.1f}" if score is not None else '<span style="color:#999">—</span>'
        dhtml.append(f'<tr{bg}><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#333;font-weight:700">{DIMENSION_NAMES[dim]}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;font-weight:600;color:#222;white-space:nowrap">{s_str}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;color:#333">20%</td></tr>')
    dhtml.append("</table>")
    parts.append("\n".join(dhtml))

    # --- 极端信号 + 说明 ---
    extreme_high = []
    extreme_low = []
    for name, label in INDICATOR_NAMES.items():
        s = extract_indicator_score(indicator_results.get(name, {}), name)
        if s is not None:
            if s >= 85:
                extreme_high.append((name, label, s))
            elif s <= 15:
                extreme_low.append((name, label, s))

    if extreme_high or extreme_low:
        parts.append("")
        parts.append("### ⚡ 极端信号")
        parts.append("")
        if extreme_high:
            parts.append("🔴 **极度贪婪（≥85）**: " + " · ".join(f"{l}（{s:.0f}）" for _, l, s in extreme_high))
        if extreme_low:
            parts.append("🟢 **极度恐惧（≤15）**: " + " · ".join(f"{l}（{s:.0f}）" for _, l, s in extreme_low))
        parts.append("")
        parts.append("**说明：** " + "；".join([
            "、".join(f"{l}" for _, l, _ in extreme_high) + "高于 85 分阈值，属于" + "、".join(sorted(set(_INDICATOR_DIM[n] for n, _, _ in extreme_high))) + "历史高位区间" if extreme_high else "",
            "、".join(f"{l}" for _, l, _ in extreme_low) + "低于 15 分阈值，属于" + "、".join(sorted(set(_INDICATOR_DIM[n] for n, _, _ in extreme_low))) + "历史低位区间" if extreme_low else "",
        ]))

    # --- 最大变动 ---
    if movers:
        parts.append("")
        parts.append("### 📈 最大变动")
        parts.append("")
        mhtml = ['<table style="width:100%">', '<tr style="background:#ececec"><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">指标</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">变动</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">昨日→今日</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">口径</th></tr>']
        for diff, _name, label, delta, yesterday, today in movers:
            arrow = "🔼" if delta > 0 else "🔽"
            defn = _CHANGE_DEFS.get(label, "")
            mhtml.append(f'<tr style="background:#fff"><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#333">{label}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;color:#222;font-weight:600;white-space:nowrap">{arrow} {diff:.0f}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;color:#333;white-space:nowrap">{yesterday:.0f}→{today:.0f}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-size:0.9em">{defn}</td></tr>')
        mhtml.append("</table>")
        parts.append("\n".join(mhtml))

    # --- 当日总结 ---
    level = fgi_level(fgi_raw)
    pos_label, _ = fgi_percentile_result or ("暂无历史参考", "")

    dim_avgs = {}
    for dim in DIMENSION_INDICATORS:
        vals = [extract_indicator_score(indicator_results.get(n, {}), n) for n in DIMENSION_INDICATORS[dim]]
        vals_clean = [v for v in vals if v is not None]
        dim_avgs[dim] = sum(vals_clean) / len(vals_clean) if vals_clean else None

    parts.append("")
    parts.append("### 📝 当日总结")
    parts.append("")
    dim_line = " · ".join(f"{DIMENSION_NAMES[d]} {dim_avgs[d]:.0f}" for d in DIMENSION_INDICATORS if dim_avgs[d] is not None)
    parts.append(f"- FGI {fgi_raw:.1f}（{level}），{pos_label}")
    parts.append(f"- 维度：{dim_line}")

    if movers:
        mover_str = " · ".join(f"{_name} {'🔼' if d>0 else '🔽'}{abs(d):.0f}" for _, _name, _, d, _, _ in movers)
        parts.append(f"- 最大变动：{mover_str}")

    parts.append(f"- 极端指标：🔴极度贪婪 {len(extreme_high)}个 · 🟢极度恐惧 {len(extreme_low)}个")

    degraded_inds = [(n, indicator_results.get(n, {})) for n in INDICATOR_NAMES]
    degraded = [(INDICATOR_NAMES[n], r.get("source_date", "")) for n, r in degraded_inds if r.get("status") == "degraded"]
    if degraded:
        for name, sd in degraded:
            parts.append(f"- 前向填充：{name}（源数据至 {sd}）")

    substituted = [(INDICATOR_NAMES[n], n) for n, r in degraded_inds if r.get("status") == "substituted"]
    if substituted:
        for name, code in substituted:
            desc = _SUBSTITUTE_DESC.get(code, "代理估算")
            parts.append(f"- 替代指标：{name}：{desc}")

    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("📊 查看历史走势与详细图表：[https://flyshub.github.io/FGI/](https://flyshub.github.io/FGI/)")

    return "\n".join(parts)
