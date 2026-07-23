"""Test PushPlus push with a given date's data.

Uses daily_run flow (real fetch, not offline) to compute FGI for the target date,
then sends via PushPlus. Default target date is yesterday (T+1 convention).
"""
import argparse
import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fgi.output.daily_run import setup_data_manager
from fgi.calculator.fgi import FGICalculator
from fgi.output.pushplus import send_fgi_report
from fgi.storage.database import Database
from fgi.config.settings import DB_PATH


def main():
    parser = argparse.ArgumentParser(description="Push a test FGI report via PushPlus.")
    parser.add_argument(
        "--date",
        default=(date.today() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="Target date (YYYY-MM-DD); default yesterday (T+1 convention).",
    )
    args = parser.parse_args()
    target_date = args.date

    if not os.getenv("FGI_PUSHPLUS_TOKEN"):
        print("ERROR: FGI_PUSHPLUS_TOKEN not set")
        sys.exit(1)

    print(f"Computing FGI for {target_date} (real fetch, not offline)")
    data_manager = setup_data_manager()

    with Database(DB_PATH) as db:
        db.init_schema()
        calc = FGICalculator(data_manager, db)
        result = calc.run(target_date)

    print(f"FGI_raw:    {result['fgi_raw']}")
    print(f"FGI_final:  {result['fgi_final']}")
    print(f"Health:     {result['health_score']:.1f}")
    print(f"Dimensions: {result['dimension_scores']}")
    print(f"Indicators:")
    for name, r in sorted(result["indicator_results"].items()):
        s = r.get("score") or r.get(name.lower())
        st = r.get("status", "?")
        print(f"  {name}: score={s} status={st}")

    print("\nSending PushPlus...")
    ok = send_fgi_report(
        result["fgi_raw"],
        result["dimension_scores"],
        result["indicator_results"],
        result["health_score"],
        date_str=target_date,
    )
    print(f"PushPlus: {'OK' if ok else 'FAILED'}")


if __name__ == "__main__":
    main()
