import logging

import pandas as pd

from fgi.calculator.common import lookback_start, mark_success, missing, save_score
from fgi.calculator.status import StatusRecorder
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.config.settings import PERCENTILE_WINDOW_YEARS
from fgi.indicators.registry import raw_keys
from fgi.storage.database import Database

K = raw_keys("f3")

logger = logging.getLogger(__name__)


class F3Calculator:
    """F3 = 上证 ``price_change × volume`` 的五年滚动百分位（资金流代理）。

    DB-first（ADR-0005）：历史从 raw_data 的 f3_proxy_close / f3_proxy_volume 读，
    只用数据源补最近 30 天。主源 Fuyao 只覆盖近 5 自然年，整窗重取会把历史截断。
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

    def _fetch_index(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "f3_index",
            "fetch_index_daily",
            "sh000001",
            start_date,
            end_date,
        )

    def _persist_source_data(self, result: DataSourceResult) -> None:
        if result.data is None:
            return
        for _, row in result.data.iterrows():
            d = row["date"]
            d = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            if pd.notna(row.get("close")):
                self._db.upsert_raw_data(d, K["close"], float(row["close"]))
            if pd.notna(row.get("volume")):
                self._db.upsert_raw_data(d, K["volume"], float(row["volume"]))
        self._db.commit()

    def _load_history(self, start_date: str, end_date: str) -> pd.DataFrame:
        close_df = self._db.get_raw_data(K["close"], start_date, end_date)
        volume_df = self._db.get_raw_data(K["volume"], start_date, end_date)
        if close_df.empty or volume_df.empty:
            return pd.DataFrame()
        merged = close_df.rename(columns={"value": "close"}).merge(
            volume_df.rename(columns={"value": "volume"}), on="date"
        )
        return merged.dropna(subset=["close", "volume"]).reset_index(drop=True)

    def _bootstrap_history(
        self, start_date: str, end_date: str, history: pd.DataFrame
    ) -> tuple[pd.DataFrame, bool, DataSourceResult]:
        """不变式 8 的唯一例外：DB 不足一个窗口时整窗取数引导。

        只应在新部署或 DB 恢复不完整时触发，所以要留下 warning。
        数据源本身深度不足（如 Fuyao 1211 根）时补不满，由 status 里的
        `short history` 记录实际窗口长度。
        """
        logger.warning(
            "f3: raw_data has %d rows (< %d window), bootstrapping full window from source",
            len(history),
            self._window,
        )
        result = self._fetch_index(start_date, end_date)
        fetched = False
        if result.status == DataSourceStatus.HEALTHY and result.data is not None:
            fetched = True
            self._persist_source_data(result)
            history = self._load_history(start_date, end_date)
        return history, fetched, result

    def run(self, date: str, lookback_days: int = None) -> dict:
        self._status.begin(date)
        if lookback_days is None:
            lookback_days = self._window + 60

        start_date = lookback_start(date, lookback_days)

        history = self._load_history(start_date, date)
        fetched_freshly = False

        if history.empty or date not in history["date"].values:
            recent_start = (pd.Timestamp(date) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            result = self._fetch_index(recent_start, date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                fetched_freshly = True
                self._persist_source_data(result)
                history = self._load_history(start_date, date)

        # 历史不足一个窗口（新部署 / DB 恢复不完整）：走引导路径整窗取数补齐。
        # 这是不变式 8 的唯一例外，触发时 `_bootstrap_history` 会打 warning。
        if len(history) < self._window:
            history, bootstrapped, result = self._bootstrap_history(start_date, date, history)
            fetched_freshly = fetched_freshly or bootstrapped
            if history.empty:
                return missing(self._status, "f3", result.error or "No data", result.source)

        df = history[["date", "close", "volume"]].copy()
        df["price_change"] = df["close"].diff()
        df["flow_magnitude"] = df["price_change"] * df["volume"]

        df = df[["date", "flow_magnitude"]].dropna()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        df["percentile"] = rolling_percentile(df["flow_magnitude"], window=self._window)

        today = df[df["date"] == pd.Timestamp(date)]
        if today.empty:
            return missing(self._status, "f3", "No data for date", "database")

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            return missing(self._status, "f3", "Insufficient data", "database")

        score = percentile * 100

        save_score(self._db, date, "f3", score, {K["percentile"]: percentile})
        note = None
        if len(df) < self._window:
            note = f"short history: {len(df)}/{self._window} rows"
        mark_success(self._status, "f3", "database", fetched_freshly, extra_note=note)

        return {"f3": score, "status": "normal", "percentile": percentile}
