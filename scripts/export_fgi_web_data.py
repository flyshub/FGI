#!/usr/bin/env python3
"""Export FGI database data to JSON files for the static web frontend.

Usage:
    python scripts/export_fgi_web_data.py                  # latest date only
    python scripts/export_fgi_web_data.py --date 2026-07-24  # specific date
    python scripts/export_fgi_web_data.py --full              # all files
    python scripts/export_fgi_web_data.py --output-dir docs/data
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from fgi.common.utils import extract_indicator_score
from fgi.output.decision_matrix import compute_decision_matrix
from fgi.output.renderer import (
    DIMENSION_INDICATORS,
    DIMENSION_NAMES,
    INDICATOR_NAMES,
    fgi_level,
)
from fgi.output.signal_report import (
    SignalReportEngine,
    _find_closest_prior_fgi,
    _get_forward_return,
    assign_zone,
    compute_rank_ic,
    layer_backtest_10,
    simulate_dca,
)
from fgi.storage.database import Database

logger = logging.getLogger(__name__)

OUTPUT_DIR_DEFAULT = Path("docs/data")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_prev_scores(db: Database, date_str: str) -> dict | None:
    """Get the previous trading day's scores from scores_daily."""
    try:
        prev = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        return db.get_score_on_date(prev)
    except Exception:
        return None


def _get_fgi_percentile(db: Database, fgi: float) -> tuple[float, str, str]:
    """Return (pct, label, extreme_note)."""
    try:
        below = db.count_scores_below(fgi)
        total = db.count_scores_with_data()
        if total == 0:
            return 0.0, "无历史数据", ""
        pct = below / total * 100
        tiers = [
            (10,   f"低于历史上 {100-pct:.0f}% 的日子（极低）", "⚠️ 处于历史极低区间"),
            (25,   f"低于历史上 {100-pct:.0f}% 的日子（偏低）", ""),
            (40,   f"位于历史中下区域（{pct:.0f}%分位）", ""),
            (60,   f"位于历史中部（{pct:.0f}%分位）", ""),
            (75,   f"位于历史中上区域（{pct:.0f}%分位）", ""),
            (90,   f"高于历史上 {pct:.0f}% 的日子（偏高）", ""),
            (100,  f"高于历史上 {pct:.0f}% 的日子（极高）", "⚠️ 处于历史极高区间"),
        ]
        for limit, label, note in tiers:
            if pct <= limit:
                return pct, label, note
        return pct, "暂无历史参考", ""
    except Exception:
        return 0.0, "暂无历史参考", ""


def _get_trend(fgi: float, prev: dict | None) -> tuple[str, float | None]:
    """Return (arrow, delta)."""
    prev_fgi = prev.get("FGI_final") if prev else None
    if prev_fgi is None:
        return "→", None
    delta = fgi - prev_fgi
    if abs(delta) < 0.5:
        return "→", delta
    return ("↑" if delta > 0 else "↓"), round(delta, 2)


def _get_extreme_signals(scores: dict) -> dict:
    """Detect indicators >=85 or <=15."""
    high, low = [], []
    for name, label in INDICATOR_NAMES.items():
        s = scores.get(name)
        if s is not None and not pd.isna(s):
            if s >= 85:
                high.append((name, label, round(s, 1)))
            elif s <= 15:
                low.append((name, label, round(s, 1)))
    return {"high": high, "low": low}


def _zone_context(stats: dict, zone_name: str, total_days: int | None) -> dict | None:
    """Extract current zone's signal reference from engine stats (string keys after JSON serialization)."""
    if not stats:
        return None
    total = total_days or stats.get("metadata", {}).get("total_days", 0)
    h5 = stats.get("5", stats.get(5, []))
    zone_data = next((z for z in h5 if z["zone"] == zone_name), None)
    if zone_data is None:
        return None
    horizons = {}
    for h in ["5", "20", "60"]:
        h_stats = stats.get(h, [])
        hd = next((z for z in h_stats if z["zone"] == zone_name), None)
        if hd:
            horizons[h] = {
                "mean": hd["mean"],
                "win_rate": hd["win_rate"],
            }
    return {
        "zone": zone_name,
        "n": zone_data["n"],
        "total": total,
        "pct": round(zone_data["n"] / total * 100, 1) if total > 0 else 0,
        "horizons": horizons,
    }


def _compute_anchor(db: Database, fgi: float, date_str: str) -> dict | None:
    """Compute the time anchor — closest prior FGI with same direction."""
    if fgi is None:
        return None
    try:
        all_scores = db.get_scores("2009-01-01", "2099-12-31")
        if all_scores is None or all_scores.empty:
            return None

        # Trend purity: check if the CURRENT trend is directional enough
        trend_series = all_scores[all_scores["date"] <= date_str]["FGI_final"].dropna().tail(6).astype(float).values
        if len(trend_series) == 6:
            changes = np.abs(np.diff(trend_series))
            total_v = np.sum(changes)
            net_c = abs(trend_series[-1] - trend_series[0])
            purity = net_c / total_v if total_v > 0 else 0
            # Web frontend uses lower threshold than PushPlus (0.15 vs 0.30)
            if purity < 0.15:
                return None

        closest = _find_closest_prior_fgi(db, fgi, date_str)
        if closest is None:
            return None
        closest_date, closest_fgi, closest_prev = closest
        forward_ret = _get_forward_return(db, closest_date, horizon=20)

        # Top-5 detail table: find other close historical values
        detail_table = []
        try:
            hist = all_scores[all_scores["date"] < date_str].dropna(subset=["FGI_final"]).copy()
            hist["_diff"] = (hist["FGI_final"].astype(float) - float(fgi)).abs()
            top5 = hist.sort_values("_diff").head(5)
            for _, cr in top5.iterrows():
                d = str(cr["date"])
                r = _get_forward_return(db, d, horizon=20)
                if r is not None:
                    detail_table.append({
                        "date": d,
                        "fgi": round(float(cr["FGI_final"]), 1),
                        "forward_20": round(r * 100, 1),
                    })
        except Exception:
            pass

        return {
            "closest_date": closest_date,
            "closest_fgi": round(closest_fgi, 1),
            "delta": round(closest_fgi - closest_prev, 1),
            "forward_20d_return": round(forward_ret * 100, 1) if forward_ret is not None else None,
            "detail_table": detail_table[:5] if detail_table else [],
        }
    except Exception:
        return None


def _get_dimension_scores(scores: dict) -> dict:
    """Compute 5 dimension averages from indicator scores."""
    result = {}
    for dim, indicators in DIMENSION_INDICATORS.items():
        vals = [scores.get(n) for n in indicators if scores.get(n) is not None and not pd.isna(scores.get(n))]
        result[dim] = round(sum(vals) / len(vals), 1) if vals else None
    return result


# ---------------------------------------------------------------------------
# Export functions
# ---------------------------------------------------------------------------


def export_latest(db: Database, date_str: str | None = None, stats: dict | None = None) -> dict:
    """Export the latest (or specified) date's full data."""
    if date_str is None:
        date_str = db.get_latest_score_date()
    if date_str is None:
        return {"error": "no_data", "message": "No data in scores_daily"}

    scores = db.get_score_on_date(date_str)
    if scores is None:
        return {"error": "no_data", "message": f"No data for {date_str}"}

    fgi_final = scores.get("FGI_final")
    fgi_raw = scores.get("FGI_raw")
    health = scores.get("health_score")
    prev = _get_prev_scores(db, date_str)
    arrow, delta = _get_trend(fgi_final, prev)
    pct, pct_label, extreme_note = _get_fgi_percentile(db, fgi_final)
    zone = assign_zone(fgi_final)
    dm = compute_decision_matrix(db, date_str, fgi_final)
    extreme = _get_extreme_signals(scores)

    # Indicator scores
    indicator_scores = {k: scores.get(k) for k in INDICATOR_NAMES}

    # Dimension scores
    dimension_scores = _get_dimension_scores(scores)

    # Statuses from daily_status
    statuses = {}
    try:
        st_df = db.get_status(date_str)
        for _, r in st_df.iterrows():
            statuses[r["indicator"].upper()] = r["status"]
    except Exception:
        pass

    # Zone context
    zone_ctx = _zone_context(stats, zone, stats.get("metadata", {}).get("total_days")) if stats else None

    # Anchor
    anchor = _compute_anchor(db, fgi_final, date_str)

    result = {
        "date": date_str,
        "fgi_final": round(fgi_final, 2) if fgi_final is not None else None,
        "fgi_raw": round(fgi_raw, 2) if fgi_raw is not None else None,
        "health_score": round(health, 1) if health is not None else None,
        "zone": zone,
        "trend": arrow,
        "delta": delta,
        "prev_fgi": round(prev.get("FGI_final"), 2) if prev else None,
        "percentile": round(pct, 1),
        "percentile_label": pct_label,
        "extreme_note": extreme_note,
        "scores": {k: (round(v, 1) if v is not None else None) for k, v in indicator_scores.items()},
        "dimensions": dimension_scores,
        "statuses": statuses,
        "decision_matrix": dm.to_dict() if dm else None,
        "extreme_signals": extreme,
        "zone_context": zone_ctx,
        "anchor": anchor,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return result


def export_history(db: Database) -> list[dict]:
    """Export all historical FGI data + 上证综指 close price."""
    df = db.get_scores("2000-01-01", "2099-12-31")
    if df.empty:
        return []
    df = df.dropna(subset=["FGI_final"]).sort_values("date")

    # Attach close price from raw_data
    try:
        close_df = db.get_raw_data("m3_close", "2000-01-01", "2099-12-31")
        if not close_df.empty:
            close_map = dict(zip(close_df["date"], close_df["value"]))
            df["close"] = df["date"].map(close_map)
        else:
            df["close"] = None
    except Exception:
        df["close"] = None

    records = []
    for _, r in df.iterrows():
        records.append({
            "date": str(r["date"]),
            "FGI_final": round(float(r["FGI_final"]), 2) if pd.notna(r.get("FGI_final")) else None,
            "FGI_raw": round(float(r["FGI_raw"]), 2) if pd.notna(r.get("FGI_raw")) else None,
            "health_score": round(float(r["health_score"]), 1) if pd.notna(r.get("health_score")) else None,
            "close": round(float(r["close"]), 2) if pd.notna(r.get("close")) else None,
        })
    return records


def export_signal_report(db: Database) -> dict:
    """Export full signal report data (zone stats, IC, layer backtest, DCA)."""
    engine = SignalReportEngine(db)
    result = engine.run()
    df_full = engine.load_data()

    if not result.get("stats"):
        return {"error": "insufficient_data", "message": "Signal report engine returned no stats"}

    df_full["_year"] = pd.to_datetime(df_full["date"]).dt.year
    df_in = df_full[df_full["_year"] <= 2022]
    df_out = df_full[df_full["_year"] >= 2023]

    # Rank IC
    ic_full = compute_rank_ic(df_full)
    if ic_full:
        ic_full["rolling"] = compute_rolling_ic_window(df_full)
    ic_in = compute_rank_ic(df_in)
    ic_out = compute_rank_ic(df_out)
    if ic_in:
        ic_in["rolling"] = compute_rolling_ic_window(df_in)
    if ic_out:
        ic_out["rolling"] = compute_rolling_ic_window(df_out)

    # Layer backtest
    layer_result = layer_backtest_10(df_full)
    layer_in = layer_backtest_10(df_in)
    layer_out = layer_backtest_10(df_out)

    # DCA
    dca_full = simulate_dca(df_full)

    return {
        "metadata": result.get("metadata"),
        "zone_stats": result.get("stats"),
        "in_sample": result.get("in_sample"),
        "out_sample": result.get("out_sample"),
        "rank_ic": {"full": ic_full, "in_sample": ic_in, "out_sample": ic_out},
        "layer_backtest": {"full": layer_result, "in_sample": layer_in, "out_sample": layer_out},
        "dca": dca_full,
    }


def export_indicators_history(db: Database) -> dict:
    """Export all 12 indicator + close price historical series."""
    df = db.get_scores("2000-01-01", "2099-12-31")
    if df.empty:
        return {}
    df = df.sort_values("date")

    # Add close price
    try:
        close_df = db.get_raw_data("m3_close", "2000-01-01", "2099-12-31")
        if not close_df.empty:
            close_map = dict(zip(close_df["date"], close_df["value"]))
            df["close"] = df["date"].map(close_map)
    except Exception:
        df["close"] = None

    indicators = list(INDICATOR_NAMES.keys())

    def _series(col: str) -> list[dict]:
        out = []
        for _, r in df.iterrows():
            v = r.get(col)
            if v is not None and pd.notna(v):
                out.append({"date": str(r["date"]), "value": round(float(v), 2)})
        return out

    result = {
        "indicators": {ind: _series(ind) for ind in indicators},
        "close": _series("close"),
    }
    return result


def export_all_dates(db: Database, stats: dict | None = None) -> list[dict]:
    """Pre-compute full data for every historical date with FGI_final.

    Returns a list of per-date dicts (same shape as export_latest minus generated_at).
    This is the canonical datasource for the 'switch date' feature.
    """
    df = db.get_scores("2000-01-01", "2099-12-31")
    if df.empty:
        return []
    df = df.dropna(subset=["FGI_final"]).sort_values("date")
    all_dates = []

    # Pre-fetch all scores for fast prev lookup
    all_scores_list = []
    for _, r in df.iterrows():
        all_scores_list.append({k: r[k] for k in r.index})
    scores_by_date = {s["date"]: s for s in all_scores_list}

    total_with_data = db.count_scores_with_data()
    dates_list = list(scores_by_date.keys())

    for date_str in dates_list:
        scores = scores_by_date[date_str]
        fgi_final = float(scores["FGI_final"])
        fgi_raw = float(scores.get("FGI_raw", fgi_final))
        health = float(scores["health_score"]) if pd.notna(scores.get("health_score")) else None

        # Prev & trend
        prev = None
        for pdate in reversed(dates_list):
            if pdate < date_str:
                prev = scores_by_date.get(pdate)
                break
        arrow, delta = _get_trend(fgi_final, prev)

        # Percentile
        pct, pct_label, extreme_note = 0.0, "无历史数据", ""
        try:
            below = sum(1 for s in all_scores_list if s["date"] < date_str and s["FGI_final"] is not None and s["FGI_final"] < fgi_final)
            total_before = sum(1 for s in all_scores_list if s["date"] < date_str and s["FGI_final"] is not None)
            if total_before > 0:
                pct = below / total_before * 100
                tiers = [
                    (10,   f"低于历史上 {100-pct:.0f}% 的日子（极低）", "⚠️ 处于历史极低区间"),
                    (25,   f"低于历史上 {100-pct:.0f}% 的日子（偏低）", ""),
                    (40,   f"位于历史中下区域（{pct:.0f}%分位）", ""),
                    (60,   f"位于历史中部（{pct:.0f}%分位）", ""),
                    (75,   f"位于历史中上区域（{pct:.0f}%分位）", ""),
                    (90,   f"高于历史上 {pct:.0f}% 的日子（偏高）", ""),
                    (100,  f"高于历史上 {pct:.0f}% 的日子（极高）", "⚠️ 处于历史极高区间"),
                ]
                for limit, label, note in tiers:
                    if pct <= limit:
                        pct_label, extreme_note = label, note
                        break
        except Exception:
            pass

        zone = assign_zone(fgi_final)
        dm = compute_decision_matrix(db, date_str, fgi_final)
        extreme = _get_extreme_signals(scores)
        indicator_scores = {k: (float(scores.get(k)) if pd.notna(scores.get(k)) else None) for k in INDICATOR_NAMES}
        indicator_scores_clean = {}
        for k, v in indicator_scores.items():
            indicator_scores_clean[k] = round(v, 1) if v is not None else None
        dimension_scores = _get_dimension_scores(scores)

        # Statuses
        statuses = {}
        try:
            st_df = db.get_status(date_str)
            for _, r in st_df.iterrows():
                statuses[r["indicator"].upper()] = r["status"]
        except Exception:
            pass

        # Zone context
        zone_ctx = _zone_context(stats, zone, total_with_data) if stats else None

        # Anchor
        anchor = _compute_anchor(db, fgi_final, date_str)

        all_dates.append({
            "date": date_str,
            "fgi_final": round(fgi_final, 2),
            "fgi_raw": round(fgi_raw, 2),
            "health_score": round(health, 1) if health is not None else None,
            "zone": zone,
            "trend": arrow,
            "delta": delta,
            "prev_fgi": round(prev.get("FGI_final"), 2) if prev else None,
            "percentile": round(pct, 1),
            "percentile_label": pct_label,
            "extreme_note": extreme_note,
            "scores": indicator_scores_clean,
            "dimensions": dimension_scores,
            "statuses": statuses,
            "decision_matrix": dm.to_dict() if dm else None,
            "extreme_signals": extreme,
            "zone_context": zone_ctx,
            "anchor": anchor,
        })
    return all_dates


def export_anchors_history(db: Database) -> list[dict]:
    """Pre-compute anchors for every historical date with non-neutral FGI."""
    result = []
    df = db.get_scores("2015-01-01", "2099-12-31")
    if df.empty:
        return result
    df = df.dropna(subset=["FGI_final"]).sort_values("date")

    for _, r in df.iterrows():
        date_str = str(r["date"])
        fgi = float(r["FGI_final"])
        if 40 <= fgi <= 60:
            continue  # skip neutral zone
        closest = _find_closest_prior_fgi(db, fgi, date_str)
        if closest is None:
            continue
        fr = _get_forward_return(db, closest[0], horizon=20)
        result.append({
            "date": date_str,
            "fgi": round(fgi, 1),
            "anchor_date": closest[0],
            "anchor_fgi": round(closest[1], 1),
            "anchor_delta": round(closest[1] - closest[2], 1),
            "forward_20": round(fr * 100, 1) if fr is not None else None,
        })
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Import here to avoid circular issues at module level
from fgi.output.signal_report import compute_rolling_ic_window  # noqa: E402


def write_json(data, path: Path) -> None:
    """Write JSON with Chinese support, creating directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %s  (%d bytes)", path, path.stat().st_size)


def main():
    parser = argparse.ArgumentParser(description="Export FGI data for web frontend")
    parser.add_argument("--date", type=str, default=None, help="Target date (YYYY-MM-DD)")
    parser.add_argument("--full", action="store_true", help="Export all JSON files")
    parser.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR_DEFAULT))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    with Database() as db:
        # Signal report is needed for zone_context in latest
        signal_report = None
        try:
            signal_report = export_signal_report(db)
        except Exception as e:
            logger.warning("Signal report export failed (non-fatal): %s", e)

        # 1. Latest
        latest = export_latest(db, date_str=args.date, stats=signal_report or None)
        write_json(latest, output_dir / "fgi_latest.json")

        if args.full or args.date is None:
            # 2. History (full rebuild)
            history = export_history(db)
            write_json(history, output_dir / "fgi_history.json")

            # 3. Signal report
            if signal_report:
                write_json(signal_report, output_dir / "fgi_signal_report.json")
            else:
                logger.warning("Skipping signal_report.json due to earlier failure")

            # 4. Indicators history
            ind_hist = export_indicators_history(db)
            write_json(ind_hist, output_dir / "fgi_indicators_history.json")

            # 5. Anchors history
            anchors = export_anchors_history(db)
            write_json(anchors, output_dir / "fgi_anchors_history.json")

            # 6. All dates pre-computed data (for date switching)
            all_dates = export_all_dates(db, signal_report if signal_report and not signal_report.get("error") else None)
            write_json(all_dates, output_dir / "fgi_all_dates.json")

    logger.info("Done.")


if __name__ == "__main__":
    main()
