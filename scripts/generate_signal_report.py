#!/usr/bin/env python3
"""Generate an FGI historical signal validation report.

Usage:
    python scripts/generate_signal_report.py
    python scripts/generate_signal_report.py --start 2020-01-01 --end 2025-12-31
    python scripts/generate_signal_report.py --output reports/custom.md
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from fgi.output.signal_report import (
    SignalReportEngine,
    _render_dca_section,
    _render_ic_section,
    _render_layer_section,
    compute_rank_ic,
    compute_rolling_ic_window,
    layer_backtest_10,
    render_markdown,
    simulate_dca,
)
from fgi.storage.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate FGI signal validation report")
    parser.add_argument("--output", type=str, default=None, help="Output markdown file path")
    args = parser.parse_args()

    output_dir = Path("reports")
    output_dir.mkdir(exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = Path(args.output) if args.output else output_dir / f"fgi_signal_validation_{date_str}.md"

    print("Generating FGI signal validation report...")
    with Database() as db:
        engine = SignalReportEngine(db)
        result = engine.run()
        df_full = engine.load_data()

    df_full["_year"] = pd.to_datetime(df_full["date"]).dt.year
    df_in = df_full[df_full["_year"] <= 2022]
    df_out = df_full[df_full["_year"] >= 2023]

    report_sections = [render_markdown(result)]

    # Backtest v2: Rank IC analysis
    print("  Computing Rank IC...")
    ic_full = compute_rank_ic(df_full)
    if ic_full:
        ic_full["rolling"] = compute_rolling_ic_window(df_full)
        report_sections.append("")
        report_sections.append("---")
        report_sections.append("")
        report_sections.append(_render_ic_section(ic_full))

    # Rank IC split: in-sample vs out-of-sample
    ic_in = compute_rank_ic(df_in)
    ic_out = compute_rank_ic(df_out)
    if ic_in and ic_out:
        ic_in["rolling"] = compute_rolling_ic_window(df_in)
        ic_out["rolling"] = compute_rolling_ic_window(df_out)
        report_sections.append("")
        report_sections.append(_render_ic_section(ic_in, title="Rank IC 分析（样本内 2015-2022）"))
        report_sections.append("")
        report_sections.append(_render_ic_section(ic_out, title="Rank IC 分析（样本外 2023-2026）"))

    # Backtest v2: 10-layer backtest
    print("  Computing 10-layer backtest...")
    layer_result = layer_backtest_10(df_full)
    report_sections.append("")
    report_sections.append("---")
    report_sections.append("")
    report_sections.append(_render_layer_section(layer_result))
    # Layer split
    layer_in = layer_backtest_10(df_in)
    layer_out = layer_backtest_10(df_out)
    if layer_in and layer_out:
        report_sections.append("")
        report_sections.append(_render_layer_section(layer_in))
        report_sections.append("")
        report_sections.append(_render_layer_section(layer_out))

    # Backtest v2: DCA simulation
    print("  Computing DCA simulation...")
    dca_full = simulate_dca(df_full)
    dca_in = simulate_dca(df_in)
    dca_out = simulate_dca(df_out)
    report_sections.append("")
    report_sections.append("---")
    report_sections.append("")
    report_sections.append(_render_dca_section(dca_full, title="逆情绪 DCA vs 等额定投（全样本）"))
    if "error" not in dca_in and "error" not in dca_out:
        report_sections.append("")
        report_sections.append(_render_dca_section(dca_in, title="逆情绪 DCA vs 等额定投（样本内 2015-2022）"))
        report_sections.append("")
        report_sections.append(_render_dca_section(dca_out, title="逆情绪 DCA vs 等额定投（样本外 2023-2026）"))

    md = "\n".join(report_sections)
    out_path.write_text(md, encoding="utf-8")

    print(f"Report saved to {out_path}")
    print(f"  Data range: {result['metadata'].get('start_date')} ~ {result['metadata'].get('end_date')}")
    print(f"  Total days: {result['metadata'].get('total_days')}")


if __name__ == "__main__":
    main()
