from datetime import datetime

import pandas as pd

from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.config.settings import PERCENTILE_WINDOW_YEARS
from fgi.storage.database import Database


class V4Calculator:
    """V3.8.7: V4 = 50ETF 期权隐含波动率（QVIX）反向滚动百分位。

    数据源：ak.index_option_50etf_qvix()，历史 2015-02-09 起。
    方向：VIX 高（恐慌）→ 得分低；VIX 低（平静）→ 得分高。
    反映期权市场对未来波动的预期，与 V1/V2（估值）维度互补，
    归属波动率（volatility）维度。
    """

    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "v4_qvix", "fetch_qvix", start_date, end_date,
        )

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["close"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        # 反向：高百分位（VIX 历史高位）→ 低分 = 恐慌
        return (1.0 - percentile) * 100

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = (pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)).strftime("%Y-%m-%d")

        # DB-first：从 raw_data 读历史，缺失时从源拉取并持久化
        db_data = self._db.get_raw_data("v4_qvix", start_date, end_date)
        today_in_db = not db_data.empty and date in db_data["date"].values
        fetched_freshly = False
        if not today_in_db:
            result = self.fetch_data(start_date, end_date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                fetched_freshly = True
                for _, row in result.data.iterrows():
                    d = row["date"]
                    d = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
                    self._db.upsert_raw_data(d, "v4_qvix", float(row["close"]))
                self._db.commit()
                db_data = self._db.get_raw_data("v4_qvix", start_date, end_date)

        if db_data.empty:
            self._db.upsert_status(date, "v4", "missing", "akshare", "No QVIX data")
            return {"v4": None, "status": "missing"}

        df = pd.DataFrame({"date": db_data["date"], "close": db_data["value"]})
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df = self.calculate_percentile(df)

        today = df[df["date"] == pd.Timestamp(date)]
        if today.empty:
            self._db.upsert_status(date, "v4", "missing", "akshare", "No data for date")
            return {"v4": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "v4", "missing", "akshare", "Insufficient data")
            return {"v4": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "v4_percentile", float(percentile))
        self._db.upsert_score(date, {"V4": score})
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        source = "akshare" if fetched_freshly else "database"
        self._db.upsert_status(date, "v4", "normal", source, f"fetched_at={ts}")

        return {"v4": score, "status": "normal", "percentile": percentile}
