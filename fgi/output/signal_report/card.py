"""推送用的历史信号参考卡片：需要少量 DB 查询（最近似日期、后市收益）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fgi.output.signal_report.analysis import SignalReportEngine, get_zone_for_fgi
from fgi.output.signal_report.render import _fmt_pct
from fgi.storage.database import Database


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
