import socket
import time
import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus


# 默认 socket 超时（秒）。akshare 内部走 requests 不透传 timeout 时，
# socket.setdefaulttimeout 仍能兜底，避免单条 TCP 连接永久挂起。
DEFAULT_SOCKET_TIMEOUT = 30


def _retry(fn, retries=5, delay=3):
    """Retry with exponential backoff. socket.setdefaulttimeout bounds each call."""
    last_err = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(delay * (2 ** i))
    assert last_err is not None
    raise last_err


# 给 requests/socket 设全局默认超时，避免单条 TCP 连接永久挂起
socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT)


class AKShareSource(DataSource):
    def __init__(self, cache_ttl: float = 6 * 3600, cache_max: int = 500):
        self._client = None
        self._cache = {}
        self._cache_ttl = cache_ttl
        self._cache_max = cache_max

    def _get_client(self):
        if self._client is None:
            import akshare as ak
            self._client = ak
        return self._client

    def _cached(self, key, fn):
        """实例级缓存：TTL 过期、条数上限（超了清最旧）、失败/空结果不缓存。"""
        now = time.time()
        entry = self._cache.get(key)
        if entry is not None and now - entry[0] < self._cache_ttl:
            return entry[1]
        result = fn()
        if result is None:
            return None
        if isinstance(result, (pd.DataFrame, list, dict)) and len(result) == 0:
            return result
        self._cache[key] = (now, result)
        if len(self._cache) > self._cache_max:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        return result

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            full_symbol = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"
            df = self._cached(("index", full_symbol),
                              lambda: _retry(lambda: ak.stock_zh_index_daily(symbol=full_symbol)))
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
            df = self._cached(("index", symbol),
                              lambda: _retry(lambda: ak.stock_zh_index_daily(symbol=symbol)))
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
            df["net_buy"] = pd.to_numeric(df["net_buy"], errors="coerce")
            daily = df.groupby("date", as_index=False)["net_buy"].sum(min_count=1)
            mask = (daily["date"] >= start_date) & (daily["date"] <= end_date)
            daily = daily.loc[mask].copy()
            daily = daily.dropna(subset=["net_buy"])
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
        """创业板指数换手率（%）。东财 index_zh_a_hist 接口自带真实换手率字段，
        全区间一次拉取 + 本地切片（stock_zh_index_daily 无换手率字段）。"""
        try:
            ak = self._get_client()
            df = self._cached(("cyb",), lambda: _retry(lambda: ak.index_zh_a_hist(
                symbol="399006", period="daily", start_date="19900101", end_date="20500101"),
                retries=2, delay=1))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df = df.rename(columns={"日期": "date", "换手率": "turnover_rate"}).copy()
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df["turnover_rate"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
            df = df.dropna(subset=["turnover_rate"])
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            result_df = df.loc[mask, ["date", "turnover_rate"]].copy()
            if result_df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data in range")
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
            df = self._cached(("pe", "沪深300"),
                              lambda: _retry(lambda: ak.stock_index_pe_lg(symbol="沪深300")))
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

    def fetch_fund_position(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch 基金股票仓位 (weekly data, forward-filled to daily)."""
        try:
            ak = self._get_client()
            df = self._cached(("fund_position",),
                              lambda: _retry(lambda: ak.fund_stock_position_lg()))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No fund position data")
            df = df.rename(columns={"date": "date", "position": "position"})
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            if df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No fund position data in range")
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_industry_fund_flow(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch 行业资金流汇总 (daily, real-time)."""
        try:
            ak = self._get_client()
            df = _retry(lambda: ak.stock_fund_flow_industry(symbol="即时"))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No industry fund flow data")
            # Sum net flow across all industries
            df["净额"] = pd.to_numeric(df["净额"], errors="coerce")
            total_net = df["净额"].sum()
            result_df = pd.DataFrame([{
                "date": pd.Timestamp.now().strftime("%Y-%m-%d"),
                "net_flow": float(total_net),
            }])
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_market_cap(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch 上证总市值 (monthly, from macro_china_stock_market_cap)."""
        try:
            ak = self._get_client()
            df = self._cached(("market_cap",),
                              lambda: _retry(lambda: ak.macro_china_stock_market_cap()))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No market cap data")
            df["date"] = df["数据日期"].str.extract(r"(\d{4})年(\d{2})月份") \
                .apply(lambda x: f"{x[0]}-{x[1]}-01", axis=1)
            df["market_cap"] = pd.to_numeric(df["市价总值-上海"], errors="coerce")
            result_df = df[["date", "market_cap"]].dropna(subset=["market_cap"]).copy()
            mask = (result_df["date"] >= start_date) & (result_df["date"] <= end_date)
            result_df = result_df.loc[mask].copy()
            if result_df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No market cap data in range")
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_bond_yield(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch 中国10年期国债收益率 (daily, from bond_zh_us_rate)."""
        try:
            ak = self._get_client()
            df = self._cached(("bond_yield",),
                              lambda: _retry(lambda: ak.bond_zh_us_rate()))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No bond yield data")
            df["date"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
            df["yield_10y"] = pd.to_numeric(df["中国国债收益率10年"], errors="coerce")
            result_df = df[["date", "yield_10y"]].dropna(subset=["yield_10y"]).copy()
            mask = (result_df["date"] >= start_date) & (result_df["date"] <= end_date)
            result_df = result_df.loc[mask].copy()
            if result_df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No bond yield data in range")
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_zt_daily_summary(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch daily 涨停板 summary via levistock (supports today's date with per-day error handling)."""
        try:
            import levistock as lk
            import time
            dates = pd.date_range(start=start_date, end=end_date, freq="B")
            frames = []
            for i, d in enumerate(dates):
                ds = d.strftime("%Y-%m-%d")
                try:
                    if i % 20 == 0 and i > 0:
                        time.sleep(0.5)
                    emotion = self._cached(("mph", ds), lambda ds=ds: lk.market_emotion_kph(date=ds))
                    if not isinstance(emotion, dict):
                        continue
                    zt_count = emotion.get("sjzt", emotion.get("zt", 0))
                    zt_count = int(zt_count) if zt_count else 0

                    limit_up_list = self._cached(("zs", ds), lambda ds=ds: lk.limit_up_his_kph(date=ds))
                    seal_fund = 0.0
                    if isinstance(limit_up_list, list):
                        seal_fund = sum(item.get("seal_money", 0) for item in limit_up_list)

                    frames.append({
                        "date": ds,
                        "limit_up_count": int(zt_count),
                        "seal_fund_sum": float(seal_fund),
                    })
                except Exception:
                    continue
            if not frames:
                return DataSourceResult(None, DataSourceStatus.FAILED, "levistock", "No zt data for any date")
            result_df = pd.DataFrame(frames)
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "levistock")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "levistock", str(e))
