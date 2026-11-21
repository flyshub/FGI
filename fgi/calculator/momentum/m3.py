import pandas as pd

from fgi.calculator.common import lookback_start, mark_success, missing, save_score
from fgi.calculator.status import StatusRecorder
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.config.settings import PERCENTILE_WINDOW_YEARS
from fgi.indicators.registry import raw_keys
from fgi.storage.database import Database

K = raw_keys("m3")


class M3Calculator:
    """M3 = 上证指数相对 60 日均线偏离度的五年滚动百分位。

    DB-first（ADR-0005）：历史从 raw_data 的 m3_close 读，只用数据源补最近 30 天。
    主源 Fuyao 只覆盖近 5 自然年，若每次都整窗重取会把 2884 根历史截成 1211 根，
    使百分位窗口退化。拼接后窗口仍从 2015 年起，分数零偏差。
    """

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
            "m3_index", "fetch_index_daily", "sh000001", start_date, end_date
        )

    def calculate_deviation(self, df: pd.DataFrame) -> pd.DataFrame:
        df["ma60"] = df["close"].rolling(window=60).mean()
        df["deviation"] = (df["close"] - df["ma60"]) / df["ma60"]
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.Series:
        df["percentile"] = rolling_percentile(df["deviation"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def _persist_source_data(self, result: DataSourceResult) -> None:
        if result.data is None:
            return
        for _, row in result.data.iterrows():
            d = row["date"]
            d = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            self._db.upsert_raw_data(d, K["close"], float(row["close"]))
        self._db.commit()

    def run(self, date: str, lookback_days: int = None) -> dict:
        self._status.begin(date)
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = lookback_start(date, lookback_days)

        db_data = self._db.get_raw_data(K["close"], start_date, end_date)
        fetched_freshly = False

        # today_in_db 必须同时满足 date 存在 AND value 非 NULL：
        # 历史 NULL 行（数据中断或清理后的污染行）不能骗过当天 fetch。
        if not db_data.empty and date in db_data["date"].values:
            today_val = db_data[db_data["date"] == date]["value"].iloc[0]
            today_in_db = pd.notna(today_val) and today_val > 0
        else:
            today_in_db = False

        if not today_in_db:
            recent_start = (pd.Timestamp(date) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            result = self.fetch_data(recent_start, end_date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                fetched_freshly = True
                self._persist_source_data(result)
                db_data = self._db.get_raw_data(K["close"], start_date, end_date)

        # 历史不足一个窗口（新部署 / DB 恢复不完整）：整窗重取补齐。
        # 不足 252 行时 rolling_percentile 恒为 NaN（指标 missing）；252~1259 行
        # 虽能出分但窗口被截短，会偏 0.9~1.5 分。数据源本身只有 1211 根时补不满，
        # 所以下面把实际窗口长度记进 status，别让截断静默发生。
        if len(db_data) < self._window:
            result = self.fetch_data(start_date, end_date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                fetched_freshly = True
                self._persist_source_data(result)
                db_data = self._db.get_raw_data(K["close"], start_date, end_date)
            if db_data.empty:
                return missing(self._status, "m3", result.error, result.source)

        df = pd.DataFrame({"date": db_data["date"], "close": db_data["value"]})
        df = df.sort_values("date").reset_index(drop=True)
        df = self.calculate_deviation(df)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            return missing(self._status, "m3", "No data for date", "database")

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            return missing(self._status, "m3", "Insufficient data", "database")

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, K["deviation"], today["deviation"].iloc[0])
        save_score(
            self._db,
            date,
            "m3",
            score,
            {K["percentile"]: percentile},
        )
        note = None
        if len(df) < self._window:
            note = f"short history: {len(df)}/{self._window} rows"
        mark_success(self._status, "m3", "database", fetched_freshly, extra_note=note)

        return {"m3": score, "status": "normal", "percentile": percentile}
