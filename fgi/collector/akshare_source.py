import time
import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus


def _retry(fn, retries=5, delay=3):
    """Retry decorator with exponential backoff."""
    last_err = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(delay * (2 ** i))
    raise last_err


class AKShareSource(DataSource):
    _shared_cache = {}

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            import akshare as ak
            self._client = ak
        return self._client

    def _cached(self, key, fn):
        if key in AKShareSource._shared_cache:
            return AKShareSource._shared_cache[key]
        result = fn()
        AKShareSource._shared_cache[key] = result
        return result

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = _retry(lambda: ak.stock_zh_index_daily(
                symbol=f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = _retry(lambda: ak.stock_zh_index_daily(symbol=symbol))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_margin_data(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = _retry(lambda: ak.stock_margin_sse(
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", "")))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df = df.rename(columns={"信用交易日期": "date"})
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_northbound_data(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            frames = []
            for symbol in ["沪股通", "深股通"]:
                try:
                    sub = _retry(lambda s=symbol: ak.stock_hsgt_hist_em(symbol=s))
                    if sub is not None and not sub.empty:
                        sub = sub.rename(columns={"日期": "date", "当日成交净买额": "net_buy"})
                        sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")
                        sub["channel"] = symbol
                        frames.append(sub)
                except Exception:
                    continue

            if not frames:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data for any channel")

            df = pd.concat(frames, ignore_index=True)
            daily = df.groupby("date", as_index=False)["net_buy"].sum()
            mask = (daily["date"] >= start_date) & (daily["date"] <= end_date)
            daily = daily.loc[mask].copy()
            if daily.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data in date range")
            return DataSourceResult(daily, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_zt_pool(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch 涨停板池. Iterates each date in range (API only supports single date)."""
        try:
            ak = self._get_client()
            dates = pd.date_range(start=start_date, end=end_date, freq="B")
            frames = []
            for d in dates:
                ds = d.strftime("%Y%m%d")
                df = self._cached(("zt", ds), lambda ds=ds: _retry(lambda ds=ds: ak.stock_zt_pool_em(date=ds)))
                if df is not None and not df.empty:
                    df = df.rename(columns={"代码": "symbol", "名称": "name"})
                    df["date"] = d.strftime("%Y-%m-%d")
                    frames.append(df)
            if not frames:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data for any date")
            result = pd.concat(frames, ignore_index=True)
            return DataSourceResult(result, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_market_overview(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            import levistock as lk
            dates = pd.date_range(start=start_date, end=end_date, freq="B")
            frames = []
            for d in dates:
                ds = d.strftime("%Y-%m-%d")
                data = self._cached(("kph", ds), lambda ds=ds: _retry(lambda ds=ds: lk.market_emotion_kph(date=ds), retries=2, delay=2))
                if data and "rise_num" in data and "fall_num" in data:
                    frames.append({
                        "date": ds,
                        "bullish_count": int(data["rise_num"]),
                        "bearish_count": int(data["fall_num"]),
                    })
            if not frames:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data for any date")
            result_df = pd.DataFrame(frames)
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_cyb_daily(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = _retry(lambda: ak.stock_zh_index_daily(symbol="sz399006"), retries=3, delay=2)
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            if df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data in range")
            df["turnover_rate"] = df["volume"].astype(float)
            result_df = df[["date", "turnover_rate"]].copy()
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_sentiment_data(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch 市场情绪数据 (涨跌家数) using levistock."""
        try:
            import levistock as lk
            data = lk.market_emotion_cls()
            if not data or "up_down_dis" not in data:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No sentiment data")

            up_down = data["up_down_dis"]
            result_df = pd.DataFrame([{
                "date": pd.Timestamp.now().strftime("%Y-%m-%d"),
                "rise_num": int(up_down.get("rise_num", 0)),
                "fall_num": int(up_down.get("fall_num", 0)),
                "flat_num": int(up_down.get("flat_num", 0)),
                "up_num": int(up_down.get("up_num", 0)),
                "down_num": int(up_down.get("down_num", 0)),
            }])
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "levistock")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "levistock", str(e))

    def fetch_zt_daily_summary(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch daily 涨停板 summary (count + seal fund sum) via stock_zt_pool_em."""
        try:
            ak = self._get_client()
            dates = pd.date_range(start=start_date, end=end_date, freq="B")
            frames = []
            for d in dates:
                ds = d.strftime("%Y%m%d")
                df = self._cached(("zts", ds), lambda ds=ds: _retry(lambda ds=ds: ak.stock_zt_pool_em(date=ds), retries=2, delay=2))
                if df is not None and not df.empty:
                    frames.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "limit_up_count": len(df),
                        "seal_fund_sum": float(df["封板资金"].sum()),
                    })
            if not frames:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No zt data for any date")
            result_df = pd.DataFrame(frames)
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_option_volume(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            effective_start = max(pd.Timestamp(start_date), pd.Timestamp(end_date) - pd.Timedelta(days=500))
            dates = pd.date_range(start=effective_start, end=end_date, freq="B")
            frames = []
            for d in dates:
                ds = d.strftime("%Y%m%d")
                df = self._cached(("opt", ds), lambda ds=ds: _retry(lambda ds=ds: ak.option_daily_stats_sse(date=ds), retries=2, delay=2))
                if df is not None and not df.empty:
                    row = df[df["合约标的代码"] == "510050"]
                    if not row.empty:
                        frames.append({
                            "date": d.strftime("%Y-%m-%d"),
                            "call_volume": float(row["认购成交量"].iloc[0]),
                            "put_volume": float(row["认沽成交量"].iloc[0]),
                        })
            if not frames:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No option data for any date")
            result_df = pd.DataFrame(frames)
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_pe_data(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = _retry(lambda: ak.stock_index_pe_lg(symbol="沪深300"))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No PE data")
            df = df.rename(columns={"日期": "date"})
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            if df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No PE data in range")
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def health_check(self) -> DataSourceStatus:
        try:
            ak = self._get_client()
            _retry(lambda: ak.stock_zh_index_daily(symbol="sh000001"))
            return DataSourceStatus.HEALTHY
        except Exception:
            return DataSourceStatus.FAILED
