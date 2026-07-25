#!/usr/bin/env python3
"""Generate an FGI historical signal validation report.

Usage:
    python scripts/generate_signal_report.py
    python scripts/generate_signal_report.py --start 2020-01-01 --end 2025-12-31
    python scripts/generate_signal_report.py --output reports/custom.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from fgi.storage.database import Database
from fgi.output.signal_report import (SignalReportEngine, render_markdown,
    compute_rank_ic, compute_rolling_ic_window, layer_backtest_10, simulate_dca,
    _render_ic_section, _render_layer_section, _render_dca_section)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate FGI signal validation report")
    parser.add_argument("--output", type=str, default=None, help="Output markdown file path")
    args = parser.parse_args()

    output_dir = Path("reports")
    output_dir.mkdir(exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = Path(args.output) if args.output else output_dir / f"fgi_signal_validation_{date_str}.md"

    print(f"Generating FGI signal validation report...")
    with Database() as db:
        engine = SignalReportEngine(db)
        result = engine.run()
        df = engine.load_data()

    report_sections = [render_markdown(result)]

    # Backtest v2: Rank IC analysis
    print("  Computing Rank IC...")
    ic_full = compute_rank_ic(df)
    if ic_full:
        ic_full["bonferroni_threshold"] = 0.05 / 36  # spec 5.3
        ic_full["rolling"] = compute_rolling_ic_window(df)
        report_sections.append("")
        report_sections.append("---")
        report_sections.append("")
        report_sections.append(_render_ic_section(ic_full))

    # Backtest v2: 10-layer backtest
    print("  Computing 10-layer backtest...")
    layer_result = layer_backtest_10(df)
    report_sections.append("")
    report_sections.append("---")
    report_sections.append("")
    report_sections.append(_render_layer_section(layer_result))

    # Backtest v2: DCA simulation
    print("  Computing DCA simulation...")
    dca_result = simulate_dca(df)
    report_sections.append("")
    report_sections.append("---")
    report_sections.append("")
    report_sections.append(_render_dca_section(dca_result))

    md = "\n".join(report_sections)
    out_path.write_text(md, encoding="utf-8")

    print(f"Report saved to {out_path}")
    print(f"  Data range: {result['metadata'].get('start_date')} ~ {result['metadata'].get('end_date')}")
    print(f"  Total days: {result['metadata'].get('total_days')}")


if __name__ == "__main__":
    main()
