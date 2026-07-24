from __future__ import annotations

from datetime import datetime
import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class F2Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        # 周频仓位 ffill 到日频后按日频窗口做滚动百分位
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "f2_fund_position",
            "fetch_fund_position",
            start_date,
            end_date
        )

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        if "fund_position" in df.columns:
            df["fund_position"] = pd.to_numeric(df["fund_position"], errors="coerce")
        else:
            df["fund_position"] = pd.to_numeric(df["position"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        # 周频 → 日频（交易日）前向填充后再做滚动百分位
        daily = pd.DataFrame({
            "date": pd.date_range(df["date"].min(), df["date"].max(), freq="B")
        })
        daily = daily.merge(df[["date", "fund_position"]], on="date", how="left")
        daily["fund_position"] = daily["fund_position"].ffill()
        daily["percentile"] = rolling_percentile(daily["fund_position"], window=self._window)
        daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
        return daily

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def _try_fetch_from_source(self, start_date: str, end_date: str, target_date: str) -> pd.DataFrame | None:
        """Fetch all fund position data from source without date filtering."""
        # Use a wide enough range to get all historical data
        result = self.fetch_data("2017-01-01", end_date)
        if result.status == DataSourceStatus.HEALTHY and result.data is not None and not result.data.empty:
            for _, row in result.data.iterrows():
                self._db.upsert_raw_data(str(row["date"]), "f2_fund_position", float(row["position"]))
            self._db.commit()
            df = result.data
            df["fund_position"] = df["position"]
            return df
        return None

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        # First, try to get data from database
        db_data = self._db.get_raw_data("f2_fund_position", start_date, end_date)

        fetched_freshly = False
        # F2 是周频数据：today_in_db 不检查 today 是否存在，
        # 而是检查最近 7 天内是否有有效 raw 值（覆盖本周发布日）。
        # 若 7 天内无数据，触发 fetch；否则直接 forward-fill（spec 设计）。
        if not db_data.empty:
            recent_mask = (
                (db_data["date"] >= (pd.Timestamp(date) - pd.Timedelta(days=7)).strftime("%Y-%m-%d"))
                & (db_data["date"] <= date)
                & db_data["value"].notna()
            )
            today_in_db = recent_mask.any()
        else:
            today_in_db = False
        if not today_in_db:
            fetched_freshly = True
            # Fetch recent data (last 30 days) to handle weekly frequencies
            recent_start = pd.Timestamp(date) - pd.Timedelta(days=30)
            recent_start = recent_start.strftime("%Y-%m-%d")
            result = self.fetch_data(recent_start, date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                for _, row in result.data.iterrows():
                    self._db.upsert_raw_data(str(row["date"]), "f2_fund_position", float(row["position"]))
                self._db.commit()
                db_data = self._db.get_raw_data("f2_fund_position", start_date, end_date)

        if db_data.empty:
            df = self._try_fetch_from_source(start_date, end_date, date)
            if df is None:
                self._db.upsert_status(date, "f2", "missing", "database", "No data collected")
                return {"f2": None, "status": "missing"}
        else:
            df = pd.DataFrame({
                "date": db_data["date"],
                "fund_position": db_data["value"],
            })
            df = df[df["date"] >= start_date].copy()
            if len(df) < 260:  # Need at least 5 years of weekly data
                full_df = self._try_fetch_from_source(start_date, end_date, date)
                if full_df is not None:
                    df = full_df

        df = self.calculate_percentile(df)

        # Forward-fill: find latest available date <= target date (weekly data may not have exact date)
        available = df[df["date"] <= date]
        if available.empty:
            self._db.upsert_status(date, "f2", "missing", "database", "No data for date")
            return {"f2": None, "status": "missing"}

        today = available.iloc[[-1]]  # Latest available row
        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "f2", "missing", "database", "Insufficient data")
            return {"f2": None, "status": "missing"}

        score = self.calculate_score(percentile)

        # #50: F2 是周频数据。ffill 到日频后多数日期是重复值（spec 设计），
        # 但 status 应诚实反映：最近 raw_data 距 target_date 超过 7 个日历日
        # 说明周频尚未更新，标 'degraded' 而非 'normal'，避免 health_score 失真。
        latest_raw_date = pd.to_datetime(today["date"].iloc[0])
        target_dt = pd.to_datetime(date)
        staleness_days = (target_dt - latest_raw_date).days
        is_degraded = staleness_days > 7

        # 不把"最近一周值"以当日日期写回 f2_fund_position，避免污染自身百分位窗口
        self._db.upsert_raw_data(date, "f2_percentile", percentile)
        self._db.upsert_score(date, {"F2": score})
        status = "degraded" if is_degraded else "normal"
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        if fetched_freshly:
            source_note = f"fetched_at={ts}"
            if is_degraded:
                source_note += f"; ffill from {today['date'].iloc[0]} (staleness={staleness_days}d)"
            self._db.upsert_status(date, "f2", status, "database", source_note)
        else:
            source_note = f"ffill from {today['date'].iloc[0]} (staleness={staleness_days}d)" if is_degraded else "database"
            self._db.upsert_status_keep_source(date, "f2", status, source_note)

        return {"f2": score, "status": status, "percentile": percentile}