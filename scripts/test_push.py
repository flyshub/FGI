"""Test PushPlus push with 2026-07-21 data.

Uses daily_run flow (real fetch, not offline) to compute 2026-07-21 FGI,
then sends via PushPlus.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fgi.output.daily_run import setup_data_manager
from fgi.calculator.fgi import FGICalculator
from fgi.output.pushplus import send_fgi_report
from fgi.storage.database import Database
from fgi.config.settings import DB_PATH

TARGET_DATE = "2026-07-21"


def main():
    if not os.getenv("FGI_PUSHPLUS_TOKEN"):
        print("ERROR: FGI_PUSHPLUS_TOKEN not set")
        sys.exit(1)

    print(f"Computing FGI for {TARGET_DATE} (real fetch, not offline)")
    data_manager = setup_data_manager()

    with Database(DB_PATH) as db:
        db.init_schema()
        calc = FGICalculator(data_manager, db)
        result = calc.run(TARGET_DATE)

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
        date_str=TARGET_DATE,
    )
    print(f"PushPlus: {'OK' if ok else 'FAILED'}")


if __name__ == "__main__":
    main()
