from __future__ import annotations

import pandas as pd

from fgi.calculator.common import lookback_start, mark_success, missing, save_score
from fgi.calculator.status import StatusRecorder
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.config.settings import PERCENTILE_WINDOW_YEARS
from fgi.storage.database import Database


class S3Calculator:
    """V3.8: 涨停封单量 (formerly S4) - levistock/AKShare zt_daily_summary"""

    def __init__(
        self,
        data_manager: DataSourceManager,
        db: Database,
        status: StatusRecorder | None = None,
    ):
        self._data_manager = data_manager
        self._db = db
        self._status = status if status is not None else StatusRecorder(db)
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "s3_zt_daily", "fetch_zt_daily_summary", start_date, end_date
        )

    def calculate_zt_ratio(self, df: pd.DataFrame) -> pd.DataFrame:
        # 统一单位为亿元（与 raw_data 的 s3_seal_fund 一致）
        df["zt_ratio"] = pd.to_numeric(df["seal_fund_sum"], errors="coerce") / 1e8
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        # Audit 2026-07-24: S3 raw=0 (and denormalized floats like 1e-142) are
        # data-source outages mis-stored as 0.0, not genuine zero-limit-up days.
        # All 476 S3=0 rows in production DB had same-day M1>0, confirming outage.
        # These pollution values sit at the bottom of the rolling window and inflate
        # every later percentile by +8 to +36 points.
        #
        # Replace pollution with NaN (rather than removing rows) so the rolling
        # percentile array position stays aligned. rolling_percentile skips NaN
        # in its window computation (fgi/common/utils.py:43).
        clean_zt = df["zt_ratio"].where(
            df["zt_ratio"].isna() | (df["zt_ratio"] > 1e-100), other=float("nan")
        )
        df["percentile"] = rolling_percentile(clean_zt, window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def _try_fetch_from_source(self, start_date: str, end_date: str) -> pd.DataFrame | None:
        result = self.fetch_data(start_date, end_date)
        if (
            result.status == DataSourceStatus.HEALTHY
            and result.data is not None
            and not result.data.empty
        ):
            for _, row in result.data.iterrows():
                # raw_data 可能含 NULL（数据中断），跳过不写
                seal_fund = row.get("seal_fund_sum")
                if seal_fund is None or pd.isna(seal_fund):
                    continue
                self._db.upsert_raw_data(str(row["date"]), "s3_seal_fund", float(seal_fund) / 1e8)
            self._db.commit()
            df = result.data
            df["zt_ratio"] = pd.to_numeric(df["seal_fund_sum"], errors="coerce") / 1e8
            df = df[df["date"] >= start_date].copy()
            return df
        return None

    def run(self, date: str, lookback_days: int | None = None) -> dict:
        self._status.begin(date)
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = lookback_start(date, lookback_days)

        db_data = self._db.get_raw_data("s3_seal_fund", start_date, end_date)

        fetched_freshly = False
        # today_in_db 必须同时满足 date 存在 AND value 非 NULL。
        # 否则历史 NULL 行（数据中断或清理后的污染行）会欺骗 calc 跳过当天 fetch。
        today_row = None
        if not db_data.empty and date in db_data["date"].values:
            today_row = db_data[db_data["date"] == date]
            today_val = today_row["value"].iloc[0]
            today_in_db = pd.notna(today_val) and today_val > 1e-100
        else:
            today_in_db = False
        if not today_in_db:
            recent_start = (pd.Timestamp(date) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            result = self.fetch_data(recent_start, date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                valid = result.data[result.data["seal_fund_sum"].fillna(0).astype(float) > 0]
                if not valid.empty:
                    fetched_freshly = True
                    for _, row in valid.iterrows():
                        self._db.upsert_raw_data(
                            str(row["date"]), "s3_seal_fund", float(row["seal_fund_sum"]) / 1e8
                        )
                    self._db.commit()
                    db_data = self._db.get_raw_data("s3_seal_fund", start_date, end_date)

        if db_data.empty:
            df = self._try_fetch_from_source(start_date, end_date)
            if df is None:
                return missing(self._status, "s3", "No data collected", "database")
        else:
            df = pd.DataFrame(
                {
                    "date": db_data["date"],
                    "zt_ratio": pd.to_numeric(db_data["value"], errors="coerce"),
                }
            )
            df = df[df["date"] >= start_date].copy()
            if len(df) < 252:
                full_df = self._try_fetch_from_source(start_date, end_date)
                if full_df is not None:
                    df = full_df
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            return missing(self._status, "s3", "No data for date", "database")

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            return missing(self._status, "s3", "Insufficient data", "database")

        raw_value = today["zt_ratio"].iloc[0]
        if pd.isna(raw_value):
            return missing(self._status, "s3", "Raw value is NaN", "database")
        if raw_value <= 1e-100:
            # S3 raw=0 (or denormalized float) is always a data-source outage,
            # never a genuine zero-limit-up day (audit: 0 such days in 8 years).
            return missing(self._status, "s3", "Raw value is zero (data outage)", "database")

        score = self.calculate_score(percentile)

        save_score(
            self._db,
            date,
            "s3",
            score,
            {"s3_zt_ratio": today["zt_ratio"].iloc[0], "s3_percentile": percentile},
        )
        mark_success(self._status, "s3", "database", fetched_freshly)

        return {"s3": score, "status": "normal", "percentile": percentile}
