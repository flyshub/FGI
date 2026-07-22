"""Resume FGI recompute with progress display."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fgi.storage.database import Database
from fgi.output.backfill import setup_data_manager
from fgi.calculator.fgi import FGICalculator
from fgi.output.status import record_indicator_status
from fgi.collector.trading_calendar import resolve_trading_days
from datetime import datetime

db = Database()
db.connect()
db.init_schema()

end = datetime.now().strftime("%Y-%m-%d")
dates = resolve_trading_days("2015-01-01", end, db=db)

done = set()
for row in db._conn.execute("SELECT date FROM scores_daily WHERE FGI_final IS NOT NULL"):
    done.add(row[0])

remaining = [d for d in dates if d not in done]
total = len(dates)
done_n = len(done)
print(f"DONE {done_n}/{total} ({100*done_n//total}%)  REMAINING {len(remaining)}  RANGE {dates[0]} → {dates[-1]}")
print()

dm = setup_data_manager()
dm.set_db(db)
calc = FGICalculator(dm, db)

t0 = time.time()
ok = miss = err = 0
for i, d in enumerate(remaining):
    try:
        r = calc.run(d)
        record_indicator_status(db, d, r.get("indicator_results", {}))
        fgi = r.get("fgi_final")
        if isinstance(fgi, (int, float)):
            ok += 1
        else:
            miss += 1
    except Exception as e:
        err += 1
        if err <= 3:
            print(f"  ERR {d}: {e}")

    if (i + 1) % 100 == 0 or i == 0 or i == len(remaining) - 1:
        current = done_n + i + 1
        elapsed = time.time() - t0
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        eta = (len(remaining) - i - 1) / rate if rate > 0 else 0
        pct = 100 * current // total
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        fgi_str = f"FGI={fgi:.1f}" if isinstance(fgi, (int, float)) else "FGI=None"
        print(f"  [{current:5d}/{total}] {d} {fgi_str:>10s} |{bar}| {pct:3d}% ETA {eta/60:4.1f}m ok={ok} err={err}", flush=True)

    if (i + 1) % 500 == 0:
        db.commit()

db.commit()

n = db._conn.execute("SELECT COUNT(*) FROM scores_daily").fetchone()[0]
nonnull = db._conn.execute("SELECT COUNT(*) FROM scores_daily WHERE FGI_final IS NOT NULL").fetchone()[0]
status_n = db._conn.execute("SELECT COUNT(*) FROM daily_status").fetchone()[0]
elapsed = time.time() - t0
print(f"\nDONE in {elapsed:.0f}s  ok={ok} miss={miss} err={err}")
print(f"scores_daily: {n} rows  FGI non-null: {nonnull}  daily_status: {status_n}")
db.close()
