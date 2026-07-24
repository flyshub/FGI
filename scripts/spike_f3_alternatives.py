"""F3 候选数据源稳定性 Spike（一次性评估脚本）。

目的：为 F3 "主力资金板块偏好"寻找更真实的主力资金代理。
当前实现用上证 price_change × volume（无散户/机构区分）。本脚本评估 4 个候选源：
1. 沪市大单汇总 (ak.stock_sse_summary)
2. 全市场主力资金净流入 (ak.stock_market_fund_flow) — 已知 120 天历史
3. 行业资金流 (ak.stock_fund_flow_industrial)
4. 当前 proxy 对照 (ak.stock_zh_index_daily sh000001)

每个候选连测 N 次，记录延迟、异常、shape、字段、日期覆盖、与 proxy 的相关性。
输出：stdout Markdown 报告 + /tmp/f3_spike_report.md
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak

RETRIES = 5
REPORT_PATH = Path("/tmp/f3_spike_report.md")


def time_call(fn, n: int = RETRIES):
    """Call fn n times, return list of (ok, latency_s, error_or_shape)."""
    results = []
    for i in range(n):
        t0 = time.perf_counter()
        try:
            df = fn()
            elapsed = time.perf_counter() - t0
            shape = df.shape if df is not None else None
            cols = list(df.columns) if df is not None and hasattr(df, "columns") else None
            results.append((True, elapsed, None, shape, cols))
        except Exception as e:
            elapsed = time.perf_counter() - t0
            err = f"{type(e).__name__}: {str(e)[:120]}"
            results.append((False, elapsed, err, None, None))
    return results


def summarize(name: str, results: list, df_sample=None) -> dict:
    oks = [r for r in results if r[0]]
    fails = [r for r in results if not r[0]]
    latencies = [r[1] for r in oks]
    p50 = sorted(latencies)[len(latencies) // 2] if latencies else None
    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else None
    return {
        "name": name,
        "ok_rate": f"{len(oks)}/{len(results)}",
        "p50_s": f"{p50:.2f}" if p50 else "—",
        "p95_s": f"{p95:.2f}" if p95 else "—",
        "errors": list({r[2] for r in fails}),
        "shape": oks[-1][3] if oks else None,
        "cols": oks[-1][4] if oks else None,
        "df_sample": df_sample,
    }


def main():
    print(f"F3 候选接口稳定性 Spike — {datetime.now().isoformat()}")
    print(f"每个候选连测 {RETRIES} 次\n", flush=True)

    # ===== 候选 1: 沪市大单汇总 =====
    print("[1/4] ak.stock_sse_summary() — 沪市逐笔大单", flush=True)
    r1 = time_call(lambda: ak.stock_sse_summary())
    df1 = None
    try:
        df1 = ak.stock_sse_summary()
        if df1 is not None and not df1.empty:
            print(f"  shape={df1.shape}, cols={list(df1.columns)}")
            print(df1.tail(2).to_string()[:300])
    except Exception as e:
        print(f"  sample fetch failed: {e}")
    s1 = summarize("stock_sse_summary", r1, df1)
    print(f"  ok={s1['ok_rate']} p50={s1['p50_s']}s p95={s1['p95_s']}s errors={s1['errors']}\n", flush=True)

    # ===== 候选 2: 全市场主力资金净流入 =====
    print("[2/4] ak.stock_market_fund_flow() — 全市场主力资金（120 天历史）", flush=True)
    r2 = time_call(lambda: ak.stock_market_fund_flow())
    df2 = None
    try:
        df2 = ak.stock_market_fund_flow()
        if df2 is not None and not df2.empty:
            print(f"  shape={df2.shape}, cols={list(df2.columns)}")
            print(df2.tail(2).to_string()[:300])
    except Exception as e:
        print(f"  sample fetch failed: {e}")
    s2 = summarize("stock_market_fund_flow", r2, df2)
    print(f"  ok={s2['ok_rate']} p50={s2['p50_s']}s p95={s2['p95_s']}s errors={s2['errors']}\n", flush=True)

    # ===== 候选 3: 行业资金流 =====
    print("[3/4] ak.stock_fund_flow_industrial(symbol='即时') — 行业资金流", flush=True)
    r3 = time_call(lambda: ak.stock_fund_flow_industrial(symbol="即时"))
    df3 = None
    try:
        df3 = ak.stock_fund_flow_industrial(symbol="即时")
        if df3 is not None and not df3.empty:
            print(f"  shape={df3.shape}, cols={list(df3.columns)}")
            print(df3.head(2).to_string()[:300])
    except Exception as e:
        print(f"  sample fetch failed: {e}")
    s3 = summarize("stock_fund_flow_industrial", r3, df3)
    print(f"  ok={s3['ok_rate']} p50={s3['p50_s']}s p95={s3['p95_s']}s errors={s3['errors']}\n", flush=True)

    # ===== 候选 4: 当前 proxy 对照 =====
    print("[4/4] ak.stock_zh_index_daily(sh000001) — 当前 proxy 基线", flush=True)
    r4 = time_call(lambda: ak.stock_zh_index_daily(symbol="sh000001"))
    df4 = None
    try:
        df4 = ak.stock_zh_index_daily(symbol="sh000001")
        if df4 is not None and not df4.empty:
            print(f"  shape={df4.shape}, cols={list(df4.columns)}")
    except Exception as e:
        print(f"  sample fetch failed: {e}")
    s4 = summarize("stock_zh_index_daily (proxy)", r4, df4)
    print(f"  ok={s4['ok_rate']} p50={s4['p50_s']}s p95={s4['p95_s']}s errors={s4['errors']}\n", flush=True)

    # ===== 相关性分析（如候选 2 与 proxy 可对齐日期）=====
    correlation_block = "**相关性分析**：候选 2（stock_market_fund_flow）与 proxy 在可对齐窗口内的相关性\n\n"
    if df2 is not None and df4 is not None and not df2.empty and not df4.empty:
        try:
            # 候选 2: 主力净流入-净额
            col_flow = "主力净流入-净额" if "主力净流入-净额" in df2.columns else df2.columns[0]
            df2b = df2.copy()
            date_col2 = "日期" if "日期" in df2b.columns else df2b.columns[0]
            df2b[date_col2] = pd.to_datetime(df2b[date_col2]).dt.strftime("%Y-%m-%d")
            df2b = df2b.rename(columns={date_col2: "date", col_flow: "net_flow"})
            df2b["net_flow"] = pd.to_numeric(df2b["net_flow"], errors="coerce")

            # proxy
            df4b = df4.copy()
            df4b["date"] = pd.to_datetime(df4b["date"]).dt.strftime("%Y-%m-%d")
            df4b["price_change"] = df4b["close"].diff()
            df4b["proxy"] = df4b["price_change"] * df4b["volume"]

            merged = pd.merge(df2b[["date", "net_flow"]], df4b[["date", "proxy"]], on="date").dropna()
            if len(merged) > 10:
                pearson = merged["net_flow"].corr(merged["proxy"])
                spearman = merged["net_flow"].corr(merged["proxy"], method="spearman")
                correlation_block += f"- 对齐 {len(merged)} 天，Pearson={pearson:.3f}，Spearman={spearman:.3f}\n"
                correlation_block += f"- Pearson>0.7 视为方向有效 → **{'✓ 通过' if pearson > 0.7 else '✗ 未通过'}**\n"
            else:
                correlation_block += f"- 对齐样本不足（{len(merged)} 天）\n"
        except Exception as e:
            correlation_block += f"- 相关性计算失败: {e}\n"
    else:
        correlation_block += "- 数据获取失败，无法计算\n"

    # ===== Markdown 报告 =====
    md = f"""# F3 候选接口稳定性 Spike 报告

**日期**：{datetime.now().strftime("%Y-%m-%d %H:%M")}
**目的**：评估 F3 替代数据源，为未来切换积累数据。当前 proxy 实证分布健康（mean=49，范围 0-100）。

## 候选源对比

| # | 候选 | 成功率 | 延迟 p50 | 延迟 p95 | Shape | 备注 |
|---|------|--------|---------|---------|-------|------|
| 1 | stock_sse_summary（沪市大单） | {s1['ok_rate']} | {s1['p50_s']}s | {s1['p95_s']}s | {s1['shape']} | {s1['errors'] or '—'} |
| 2 | stock_market_fund_flow（全市场主力） | {s2['ok_rate']} | {s2['p50_s']}s | {s2['p95_s']}s | {s2['shape']} | {s2['errors'] or '—'} |
| 3 | stock_fund_flow_industrial（行业即时） | {s3['ok_rate']} | {s3['p50_s']}s | {s3['p95_s']}s | {s3['shape']} | {s3['errors'] or '—'} |
| 4 | stock_zh_index_daily（当前 proxy 基线） | {s4['ok_rate']} | {s4['p50_s']}s | {s4['p95_s']}s | {s4['shape']} | {s4['errors'] or '—'} |

## 字段详情

### 候选 1: stock_sse_summary
- 字段：{s1['cols']}
- 最后样本：``{df1.tail(2).to_dict('records') if df1 is not None and not df1.empty else 'N/A'}``

### 候选 2: stock_market_fund_flow
- 字段：{s2['cols']}
- 最后样本：``{df2.tail(2).to_dict('records') if df2 is not None and not df2.empty else 'N/A'}``

### 候选 3: stock_fund_flow_industrial
- 字段：{s3['cols']}
- 前两行：``{df3.head(2).to_dict('records') if df3 is not None and not df3.empty else 'N/A'}``

## {correlation_block}

## 结论与推荐

- **当前 proxy 是否需要切换**：根据 V3.8.5 全量 recompute 实测，F3 分布健康（mean=49，范围 0-100），方向性正确（暴跌日→低分、暴涨日→高分），**暂不需要切换**。
- **候选源可用性**：见上表成功率 + 延迟。延迟 p95 > 5s 或成功率 < 5/5 视为不稳定。
- **切换触发条件**：未来如需切换，候选 2（stock_market_fund_flow）是最接近"主力资金"语义的稳定源（120 天历史）。
"""
    REPORT_PATH.write_text(md, encoding="utf-8")
    print(f"\n报告已写入 {REPORT_PATH}")
    print("\n" + "=" * 60)
    print(md)


if __name__ == "__main__":
    main()
