"""同花顺（Fuyao）金融数据源适配器。

只服务三条链（见 ``registry.CHAIN_METHODS``）：

- ``m3_index`` / ``f3_index`` → ``fetch_index_daily``
- ``m4_cyb_volume``          → ``fetch_cyb_daily``

接入理由与实测数据见 ``docs/adr/0005-fuyao-primary-source.md``。两个硬约束：

1. 接口只覆盖「近 5 自然年」，更早的 ``start`` 返回 ``code=1003``。
   这里把起始日钳到 5 年边界；历史深度由计算器的 DB-first 拼接补齐。
2. 成交量单位与 akshare 一致（股），**不做任何换算**。
   任何「除以 100」都会把 F3 的 ``close × volume`` 打偏 100 倍。
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus

BASE_URL = "https://fuyao.aicubes.cn"
TZ = ZoneInfo("Asia/Shanghai")

# 「近 5 自然年」的实测边界：365*5 天会越界，留一天余量。
MAX_HISTORY_DAYS = 365 * 5 - 1

# 指数历史 K 线端点
_INDEX_HISTORY = "/api/a-share-index/prices/historical"
_SNAPSHOT = "/api/a-share/prices/snapshot"

# 创业板指（m4）固定标的，与 akshare 的 sz399006 对齐
_CYB_THSCODE = "399006.SZ"

# 券商前缀 → thscode 交易所后缀
_SUFFIX_BY_PREFIX = {"sh": "SH", "sz": "SZ", "bj": "BJ"}


class FuyaoError(RuntimeError):
    """业务错误（``code != 0``）或重试耗尽。"""


def to_thscode(symbol: str) -> str:
    """``sh000001`` → ``000001.SH``；已是 ``000001.SH`` 则原样返回。"""
    s = symbol.strip().upper()
    if "." in s:
        return s
    prefix = s[:2].lower()
    if len(s) > 2 and prefix in _SUFFIX_BY_PREFIX:
        return f"{s[2:]}.{_SUFFIX_BY_PREFIX[prefix]}"
    # 裸代码：6 开头归上海，其余归深圳（与 tencent_source 的推断一致）
    return f"{s}.SH" if s.startswith("6") else f"{s}.SZ"


def _to_ms(day: str) -> int:
    return int(datetime.combine(date.fromisoformat(day), dt_time.min, tzinfo=TZ).timestamp() * 1000)


def _ms_to_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).date().isoformat()


class FuyaoSource(DataSource):
    MAX_RETRIES = 2

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        raw = api_key if api_key is not None else os.getenv("FUYAO_API_KEY", "")
        key = raw.strip()
        if not key:
            raise ValueError("FUYAO_API_KEY is not set")
        self._session = requests.Session()
        self._session.headers.update({"X-api-key": key})
        self._timeout = timeout

    # ---------- HTTP ----------

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = self._session.get(BASE_URL + path, params=params, timeout=self._timeout)
            except requests.RequestException as exc:
                raise FuyaoError(f"{type(exc).__name__}: {exc}") from exc

            if resp.status_code == 429:
                if attempt < self.MAX_RETRIES:
                    time.sleep(float(attempt + 1))
                    continue
                raise FuyaoError("rate limited (http 429)")

            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            code = body.get("code")
            if code == 0:
                return body
            if code == 4001 and attempt < self.MAX_RETRIES:
                time.sleep(float(attempt + 1))
                continue
            raise FuyaoError(f"code={code} {body.get('message')}")
        raise FuyaoError("retries exhausted")

    # ---------- 数据 ----------

    def _clamp_start(self, start_date: str) -> str:
        limit = datetime.now(TZ).date() - timedelta(days=MAX_HISTORY_DAYS)
        try:
            start = date.fromisoformat(start_date)
        except ValueError:
            return limit.isoformat()
        return max(start, limit).isoformat()

    def _fetch_bars(self, thscode: str, start_date: str, end_date: str) -> pd.DataFrame:
        start = self._clamp_start(start_date)
        end = min(end_date, datetime.now(TZ).date().isoformat())
        if start > end:
            return pd.DataFrame()

        body = self._get(
            _INDEX_HISTORY,
            {"thscode": thscode, "interval": "1d", "start": _to_ms(start), "end": _to_ms(end)},
        )
        items = (body.get("data") or {}).get("item") or []
        rows = [
            {
                "date": _ms_to_date(item["date_ms"]),
                "open": item.get("open_price"),
                "close": item.get("close_price"),
                "high": item.get("high_price"),
                "low": item.get("low_price"),
                "volume": item.get("volume"),
            }
            for item in items
        ]
        df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume"])
        for col in ("open", "close", "high", "low", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 仍按调用方请求的区间裁剪：钳位只是为了不触发 1003，不该扩大返回范围
        mask = (df["date"] >= start_date) & (df["date"] <= end_date)
        return df.loc[mask].reset_index(drop=True)

    def _result(self, df: pd.DataFrame, end_date: str) -> DataSourceResult:
        if df.empty:
            return DataSourceResult(None, DataSourceStatus.FAILED, "fuyao", "No data in range")
        last = str(df["date"].max())
        if last < end_date:
            return DataSourceResult(
                df, DataSourceStatus.DEGRADED, "fuyao", f"last bar {last} < end {end_date}"
            )
        return DataSourceResult(df, DataSourceStatus.HEALTHY, "fuyao")

    # ---------- DataSource 协议 ----------

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        return self.fetch_index_daily(symbol, start_date, end_date)

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            df = self._fetch_bars(to_thscode(symbol), start_date, end_date)
            return self._result(df, end_date)
        except Exception as exc:
            return DataSourceResult(None, DataSourceStatus.FAILED, "fuyao", str(exc))

    def fetch_cyb_daily(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            df = self._fetch_bars(_CYB_THSCODE, start_date, end_date)
            if not df.empty:
                df = df[["date", "volume"]]
            return self._result(df, end_date)
        except Exception as exc:
            return DataSourceResult(None, DataSourceStatus.FAILED, "fuyao", str(exc))

    def health_check(self) -> DataSourceStatus:
        try:
            body = self._get(_SNAPSHOT, {"thscodes": "600519.SH"})
            return DataSourceStatus.HEALTHY if body.get("code") == 0 else DataSourceStatus.FAILED
        except Exception:
            return DataSourceStatus.FAILED
