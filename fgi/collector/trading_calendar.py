"""真实交易日历：akshare tool_trade_date_hist_sina，内存 + 磁盘缓存。"""
from pathlib import Path
from typing import List, Optional
import pandas as pd


class TradingCalendar:
    def __init__(self, cache_dir: Optional[Path] = None):
        if cache_dir is None:
            from fgi.config.settings import DATA_DIR
            cache_dir = DATA_DIR / "cache"
        self._cache_dir = Path(cache_dir)
        self._days: Optional[List[str]] = None

    def load(self) -> Optional[List[str]]:
        """返回全部交易日（升序）；akshare 与磁盘缓存均不可用时返回 None。"""
        if self._days is not None:
            return self._days
        days = self._fetch_akshare()
        if days:
            self._days = days
            self._save_disk(days)
            return days
        days = self._load_disk()
        if days:
            self._days = days
            return days
        return None

    def trading_days(self, start_date: str, end_date: str) -> Optional[List[str]]:
        days = self.load()
        if days is None:
            return None
        return [d for d in days if start_date <= d <= end_date]

    def _fetch_akshare(self) -> Optional[List[str]]:
        try:
            import akshare as ak
            df = ak.tool_trade_date_hist_sina()
            if df is None or df.empty:
                return None
            col = "trade_date" if "trade_date" in df.columns else df.columns[0]
            return sorted(pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d").tolist())
        except Exception:
            return None

    def _cache_path(self) -> Path:
        return self._cache_dir / "trade_calendar.csv"

    def _save_disk(self, days: List[str]):
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"trade_date": days}).to_csv(self._cache_path(), index=False)
        except Exception:
            pass

    def _load_disk(self) -> Optional[List[str]]:
        try:
            df = pd.read_csv(self._cache_path(), dtype={"trade_date": str})
            days = sorted(df["trade_date"].dropna().tolist())
            return days or None
        except Exception:
            return None


def resolve_trading_days(start_date: str, end_date: str, db=None,
                         calendar: Optional[TradingCalendar] = None) -> List[str]:
    """真实交易日历优先；失败时回退 raw_data 中 m3_close 已有日期；最后回退工作日。"""
    calendar = calendar or TradingCalendar()
    days = calendar.trading_days(start_date, end_date)
    if days:
        return days
    if db is not None:
        try:
            m3 = db.get_raw_data("m3_close", start_date, end_date)
            if not m3.empty:
                return sorted(m3["date"].tolist())
        except Exception:
            pass
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start=start_date, end=end_date, freq="B")]
