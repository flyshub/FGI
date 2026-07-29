"""Detect and backfill late-released indicator data, then recompute affected FGI scores.

Strategy:
1. Scan daily_status for recent 'degraded' entries (forward-filled data).
2. Re-run the FGICalculator for each affected indicator + date range.
   (M1/S3 now use range fetch — past 30 days — so missing data gets pulled.)
3. Recompute scores_daily for affected dates.
4. Re-export web data.

Usage:
    python scripts/backfill_late_data.py              # default: look back 3 trading days
    python scripts/backfill_late_data.py --days 5      # look back 5 trading days
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fgi.calculator.fgi import FGICalculator
from fgi.collector.trading_calendar import resolve_trading_days
from fgi.config.settings import DB_PATH
from fgi.output.daily_run import setup_data_manager
from fgi.output.status import record_indicator_status
from fgi.storage.database import Database

logger = logging.getLogger(__name__)

# Indicators that can have T+1 or later data release delays
LATE_INDICATORS = {"s3", "f1", "m1", "m4"}  # m4 may also have delays


def _setup_manager():
    """Set up data sources (same as daily_run)."""
    return setup_data_manager()


def _find_forward_filled_dates(
    db: Database, lookback_days: int, trading_days: list[str], today: str
) -> dict[str, set[str]]:
    """Find dates where each indicator was forward-filled in recent trading days.

    Returns {indicator_name: {date_str, ...}}.
    """
    if not trading_days:
        return {}

    # Only look at dates up to today (trading_days may extend far into future)
    recent = [d for d in trading_days if d <= today]
    if not recent:
        return {}
    start = recent[0] if len(recent) <= lookback_days else recent[-lookback_days]
    rows = db.get_degraded_dates(start, today)

    result: dict[str, set[str]] = {}
    for row in rows:
        ind = row[1].lower()
        if ind in LATE_INDICATORS:
            result.setdefault(ind, set()).add(row[0])
    return result


def main():
    parser = argparse.ArgumentParser(description="Backfill late-released indicator data")
    parser.add_argument("--days", type=int, default=3, help="Look back N trading days (default 3)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    data_manager = _setup_manager()
    trading_days = resolve_trading_days("2000-01-01", "2099-12-31")
    today = datetime.now().strftime("%Y-%m-%d")

    if not trading_days:
        logger.warning("No trading days available, cannot backfill")
        return

    with Database(DB_PATH) as db:
        db.init_schema()

        # Step 1: Find degraded dates
        degraded = _find_forward_filled_dates(db, args.days, trading_days, today)
        if not degraded:
            logger.info("No degraded indicators found in recent %d trading days", args.days)
            return

        logger.info("Found degraded indicators: %s", {k: len(v) for k, v in degraded.items()})

        calculator = FGICalculator(data_manager, db)
        affected_dates: set[str] = set()

        # Step 2: Re-fetch each indicator for its degraded dates
        for indicator, dates in sorted(degraded.items()):
            # Map indicator short name to uppercase calculator name
            calc_name = indicator.upper()
            if calc_name not in calculator._calculators:
                continue

            calc = calculator._calculators[calc_name]
            logger.info("Re-fetching %s for %d dates...", indicator, len(dates))
            for date_str in sorted(dates):
                try:
                    result = calc.run(date_str)
                    if result.get("status") in ("normal", "degraded"):
                        affected_dates.add(date_str)
                        logger.info(
                            "  %s: %s → score=%s",
                            indicator,
                            date_str,
                            result.get(calc_name.lower()),
                        )
                    else:
                        logger.info(
                            "  %s: %s → still missing (status=%s)",
                            indicator,
                            date_str,
                            result.get("status"),
                        )
                except Exception as e:
                    logger.warning("  %s: %s → error: %s", indicator, date_str, e)

        db.commit()

        if not affected_dates:
            logger.info("No affected dates to recompute")
            return

        # Step 3: Recompute FGI for affected dates
        logger.info("Recomputing FGI for %d dates...", len(affected_dates))
        for date_str in sorted(affected_dates):
            try:
                # Clear old scores for this date
                db.clear_table_range("scores_daily", date_str, date_str)
                db.clear_table_range("daily_status", date_str, date_str)

                result = calculator.run(date_str)
                record_indicator_status(db, date_str, result.get("indicator_results", {}))
                logger.info(
                    "  FGI %s: raw=%.1f final=%.1f health=%.0f",
                    date_str,
                    result.get("fgi_raw") or 0,
                    result.get("fgi_final") or 0,
                    result.get("health_score") or 0,
                )
            except Exception as e:
                logger.warning("  FGI %s: recompute error: %s", date_str, e)

        db.commit()

    # Step 4: Export web data
    logger.info("Re-exporting web data...")
    os.chdir(Path(__file__).resolve().parent.parent)
    from scripts.export_fgi_web_data import main as export_main

    try:
        # trick: set sys.argv for export
        old_argv = sys.argv
        sys.argv = ["export_fgi_web_data.py", "--full"]
        export_main()
        sys.argv = old_argv
    except Exception as e:
        logger.warning("Web data export failed: %s", e)

    logger.info("Backfill complete.")


if __name__ == "__main__":
    main()
