from datetime import datetime, timedelta
from typing import List, Optional
from fgi.config.settings import LOOKBACK_START
from fgi.storage.database import Database
from fgi.collector.fallback import DataSourceManager
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.mock_source import MockSource
from fgi.collector.zzshare_source import ZZShareSource
from fgi.collector.base import DataSourceStatus


def setup_data_manager() -> DataSourceManager:
    manager = DataSourceManager()
    manager.register_source("akshare", AKShareSource())
    manager.register_source("mock", MockSource())
    zzshare_ok = False
    try:
        manager.register_source("zzshare", ZZShareSource())
        zzshare_ok = True
    except Exception:
        pass
    for indicator in ["m1_zt_stats", "m2_sentiment", "m3_index", "m4_cyb_turnover",
                       "s1_sentiment_zz", "s3_sentiment", "s4_zt_daily",
                       "v1_pe", "v1_bond", "v2_index",
                       "f1_margin", "f1_market_cap", "f2_fund_position", "f3_index", "f3_industry_flow"]:
        sources = []
        if indicator in ("s1_sentiment_zz", "m2_sentiment", "s3_sentiment") and zzshare_ok:
            sources.append("zzshare")
        sources.append("akshare")
        sources.append("mock")
        if sources:
            manager.configure_chain(indicator, sources)
    return manager


def get_trading_days(start_date: str, end_date: str) -> List[str]:
    import akshare as ak
    try:
        df = ak.tool_trade_date_hist_sina()
        df = df[df["trade_date"] >= start_date]
        df = df[df["trade_date"] <= end_date]
        return sorted(df["trade_date"].tolist())
    except Exception:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        dates = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return dates


def store_indicator_data(db, indicator_key, df, value_col, date_col="date"):
    """Store rows from a DataFrame into raw_data."""
    stored = 0
    for _, row in df.iterrows():
        date_str = str(row.get(date_col, ""))
        if not date_str or date_str in ("NaT", "nan", ""):
            continue
        val = row.get(value_col)
        if val is not None:
            try:
                db.upsert_raw_data(date_str, indicator_key, float(val))
                stored += 1
            except (ValueError, TypeError):
                pass
    db.commit()
    return stored


def backfill_raw_all(db, data_manager: DataSourceManager):
    """Fetch and store raw data for ALL indicators in one pass."""
    today = datetime.now().strftime("%Y-%m-%d")
    full_start = "2015-01-01"

    indicators_config = [
        ("M3 上证指数", "m3_index", "fetch_index_daily", ("sh000001", full_start, today), "close", "date", "m3_close"),
        ("M4 创业板", "m4_cyb_turnover", "fetch_cyb_daily", (full_start, today), "turnover_rate", "date", "m4_turnover"),
        ("V1 PE-TTM", "v1_pe", "fetch_pe_data", (full_start, today), "滚动市盈率", "date", "v1_pe_ttm"),
        ("V1 国债收益", "v1_bond", "fetch_bond_yield", (full_start, today), "yield_10y", "date", "v1_bond_yield"),
        ("F1 融资余额", "f1_margin", "fetch_margin_data", (full_start, today), "融资余额", "信用交易日期", "f1_margin_balance"),
        ("F1 总市值", "f1_market_cap", "fetch_market_cap", (full_start, today), "market_cap", "date", "f1_market_cap"),
        ("F2 基金仓位", "f2_fund_position", "fetch_fund_position", (full_start, today), "position", "date", "f2_position"),
        ("F3 上证代理", "f3_index", "fetch_index_daily", ("sh000001", full_start, today), "close", "date", "f3_proxy_close"),
        ("F3 成交额", "f3_index", "fetch_index_daily", ("sh000001", full_start, today), "volume", "date", "f3_proxy_volume"),
    ]

    for label, chain, method, args, val_col, date_col, key in indicators_config:
        print(f"  [{label}] {chain}...", end=" ")
        try:
            result = data_manager.fetch(chain, method, *args)
            if result.status != DataSourceStatus.HEALTHY or result.data is None:
                print(f"FAIL ({result.error})")
                continue
            n = store_indicator_data(db, key, result.data, val_col, date_col)
            print(f"OK ({n} records)")
        except Exception as e:
            print(f"ERR: {e}")

    # M1/S3 涨停板 (levistock, DB-first, do per-date)
    print(f"\n  [M1/S3 涨停板] s4_zt_daily (levistock, multi-day by calculator)...", end=" ")
    try:
        result = data_manager.fetch("s4_zt_daily", "fetch_zt_daily_summary", "2020-01-01", today)
        if result.status == DataSourceStatus.HEALTHY and result.data is not None:
            for _, row in result.data.iterrows():
                ds = str(row["date"])
                db.upsert_raw_data(ds, "m1_zt_count", float(row["limit_up_count"]))
                db.upsert_raw_data(ds, "s3_seal_fund", float(row["seal_fund_sum"]) / 1e8)
            db.commit()
            print(f"OK ({len(result.data)} records) **")
        else:
            print(f"FAIL ({result.error})")
    except Exception as e:
        print(f"ERR: {e}")

    # Sentiment indicators from zzshare
    print(f"\n  [S1/M2 sentiment] zzshare...", end=" ")
    try:
        result = data_manager.fetch("s1_sentiment_zz", "fetch_open_sentiment", "2020-01-01", today)
        if result.status == DataSourceStatus.HEALTHY and result.data is not None:
            for _, row in result.data.iterrows():
                ds = str(row["date"])
                up = float(row.get("up_num", 0))
                dn = float(row.get("down_num", 0))
                db.upsert_raw_data(ds, "m2_up_num", up)
                db.upsert_raw_data(ds, "m2_down_num", dn)
                db.upsert_raw_data(ds, "s1_up_num", up)
                db.upsert_raw_data(ds, "s1_down_num", dn)
            db.commit()
            print(f"OK ({len(result.data)} records)")
        else:
            print(f"FAIL ({result.error})")
    except Exception as e:
        print(f"ERR: {e}")

    # S2 股吧热度
    print(f"  [S2 股吧热度] zzshare...", end=" ")
    try:
        result = data_manager.fetch("s3_sentiment", "fetch_market_hot_sentiment", "2020-01-01", today)
        if result.status == DataSourceStatus.HEALTHY and result.data is not None:
            n = store_indicator_data(db, "s2_heat", result.data, "p_close", "date")
            print(f"OK ({n} records)")
        else:
            print(f"FAIL ({result.error})")
    except Exception as e:
        print(f"ERR: {e}")

    db.commit()
    print(f"\n  Raw data summary:")
    count = db._conn.execute("SELECT COUNT(*) FROM raw_data").fetchone()[0]
    print(f"  Total raw_data records: {count}")


def compute_fgi_daily(calculator, db, dates: List[str]):
    total = len(dates)
    success = 0
    failed = 0
    for i, date in enumerate(dates):
        try:
            result = calculator.run(date)
            fgi = result.get("fgi_final", None)
            if isinstance(fgi, (int, float)):
                print(f"[{i+1}/{total}] {date}: FGI={fgi:.1f}")
            else:
                print(f"[{i+1}/{total}] {date}: skipped (FGI={fgi})")
            success += 1
        except Exception as e:
            print(f"[{i+1}/{total}] {date}: ERR - {e}")
            failed += 1
    return success, failed


def backfill(start_date: Optional[str] = None, end_date: Optional[str] = None):
    data_manager = setup_data_manager()
    db = Database()
    db.connect()
    db.init_schema()

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if start_date is None:
        start_date = LOOKBACK_START

    print(f"=== FGI V3.8 Backfill: {start_date} → {end_date} ===\n")

    print("--- Phase 1: Raw indicator data → DB ---")
    backfill_raw_all(db, data_manager)

    print(f"\n--- Phase 2: FGI daily computation ---")
    dates = get_trading_days(start_date, end_date)
    print(f"Trading days: {len(dates)}")

    from fgi.calculator.fgi import FGICalculator
    calculator = FGICalculator(data_manager, db)

    success, failed = compute_fgi_daily(calculator, db, dates)

    count = db._conn.execute("SELECT COUNT(*) FROM scores_daily").fetchone()[0]
    print(f"\n=== Done: {success} ok, {failed} failed, {count} scores ===")
    db.close()


if __name__ == "__main__":
    import sys
    start = sys.argv[1] if len(sys.argv) > 1 else None
    end = sys.argv[2] if len(sys.argv) > 2 else None
    backfill(start, end)
