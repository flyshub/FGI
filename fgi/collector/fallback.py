import logging
import os
import time
from typing import Dict, List, Optional, Set
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus

logger = logging.getLogger(__name__)


COOLDOWN = 300          # 失败后冷却秒数
MAX_FAILURES = 5        # 连续失败达到此次数进入长禁用
LONG_COOLDOWN = 3600    # 长禁用秒数


# FGI_OFFLINE 模式下从 raw_data 重构 DataFrame 的映射表。
# 每个 method → (raw_key(s), 重构字段名(s))
# 单字段: 直接从 value 列映射
# 多字段: 从多个 raw_key 拼接，按 date 对齐
# 未列入的 method 在 offline 模式下保持原 FAILED 行为。
OFFLINE_RAW_MAPPING: Dict[str, tuple] = {
    "fetch_margin_data": (("f1_margin_balance",), ("融资余额",)),
    "fetch_market_cap": (("f1_market_cap",), ("market_cap",)),
    "fetch_fund_position": (("f2_fund_position",), ("position",)),
    "fetch_market_hot_sentiment": (("s2_heat",), ("p_close",)),
    "fetch_zt_daily_summary": (("s3_seal_fund",), ("seal_fund_sum",)),
    "fetch_pe_data": (("v1_pe_ttm",), ("滚动市盈率",)),
    "fetch_bond_yield": (("v1_bond_yield",), ("yield_10y",)),
    "fetch_open_sentiment": (("m2_up_num", "m2_down_num"), ("up_num", "down_num")),
}

# fetch_index_daily 被 m3/v2/f3 共用，按 indicator 名分流到不同的 raw_key
INDEX_DAILY_CHAINS: Dict[str, tuple] = {
    "m3_index": (("m3_close",), ("close",)),
    "f3_index": (("f3_proxy_close", "f3_proxy_volume"), ("close", "volume")),
}

# Indicator → raw_key 单一来源（用于 forward-fill 溯源到 raw_data 真实日期）。
# 必须与 OFFLINE_RAW_MAPPING / INDEX_DAILY_CHAINS 的 raw_key 保持一致 — 改 raw_key 时同步。
INDICATOR_RAW_KEY: Dict[str, str] = {
    "M1": "m1_zt_count",
    "M2": "m2_up_num",
    "M3": "m3_close",
    "M4": "m4_volume",
    "S2": "s2_heat",
    "S3": "s3_seal_fund",
    "V1": "v1_pe_ttm",
    "V2": "v1_erp",
    "F1": "f1_margin_ratio",
    "F2": "f2_fund_position",
    "F3": "f3_industry_net_flow",
}


class FallbackChain:
    def __init__(self, sources: List[DataSource]):
        self._sources = list(sources)
        self._status: Dict[int, DataSourceStatus] = {}
        self._failures: Dict[int, int] = {}
        self._last_failure: Dict[int, float] = {}
        self._unsupported: Set[int] = set()
        self._cooldown = COOLDOWN
        self._max_failures = MAX_FAILURES
        self._long_cooldown = LONG_COOLDOWN

    def _in_cooldown(self, i: int) -> bool:
        failures = self._failures.get(i, 0)
        if failures <= 0:
            return False
        wait = self._long_cooldown if failures >= self._max_failures else self._cooldown
        return (time.time() - self._last_failure.get(i, 0)) < wait

    def _record_failure(self, i: int, status: DataSourceStatus):
        self._status[i] = status
        self._failures[i] = self._failures.get(i, 0) + 1
        self._last_failure[i] = time.time()

    def _record_success(self, i: int):
        self._status[i] = DataSourceStatus.HEALTHY
        self._failures[i] = 0

    def fetch(self, method: str, *args, **kwargs) -> DataSourceResult:
        degraded = None
        for i, source in enumerate(self._sources):
            if i in self._unsupported or self._in_cooldown(i):
                continue
            func = getattr(source, method, None)
            if func is None:
                # 不支持该方法的源直接从链里剔除，不计入失败次数
                self._unsupported.add(i)
                logger.info("Source %s does not implement %s, removed from chain",
                            type(source).__name__, method)
                continue
            try:
                result = func(*args, **kwargs)
                if result.status == DataSourceStatus.HEALTHY:
                    self._record_success(i)
                    return result
                self._record_failure(i, result.status)
                if result.status == DataSourceStatus.DEGRADED and result.data is not None:
                    if degraded is None:
                        degraded = result
            except Exception:
                self._record_failure(i, DataSourceStatus.FAILED)
                continue
        if degraded is not None:
            return degraded
        return DataSourceResult(None, DataSourceStatus.FAILED, "fallback_chain", "All sources failed")

    def health_check(self) -> Dict[int, DataSourceStatus]:
        statuses = {}
        for i, source in enumerate(self._sources):
            statuses[i] = source.health_check()
            self._status[i] = statuses[i]
        return statuses


class DataSourceManager:
    def __init__(self):
        self._chains: Dict[str, FallbackChain] = {}
        self._sources: Dict[str, DataSource] = {}
        self._db = None  # 可选：注入 Database 用于 offline 重构

    def register_source(self, name: str, source: DataSource):
        self._sources[name] = source

    def set_db(self, db) -> None:
        """注入 Database 实例，用于 FGI_OFFLINE 模式下从 raw_data 重构 DataFrame。"""
        self._db = db

    def has_source(self, name: str) -> bool:
        return name in self._sources

    def configure_chain(self, indicator: str, source_names: List[str]):
        sources = [self._sources[name] for name in source_names if name in self._sources]
        self._chains[indicator] = FallbackChain(sources)

    def _offline_reconstruct(self, indicator: str, method: str, start_date: str, end_date: str) -> Optional[DataSourceResult]:
        """从 raw_data 重构 DataFrame；找不到映射或无数据返回 None。"""
        if self._db is None:
            return None
        mapping = OFFLINE_RAW_MAPPING.get(method)
        if mapping is None and method == "fetch_index_daily":
            mapping = INDEX_DAILY_CHAINS.get(indicator)
        if mapping is None:
            return None
        raw_keys, field_names = mapping
        try:
            if len(raw_keys) == 1:
                df = self._db.get_raw_data(raw_keys[0], start_date, end_date)
                if df.empty:
                    return None
                df = df.rename(columns={"value": field_names[0]})
            else:
                # 多字段：按 date 对齐拼接
                merged = None
                for key, name in zip(raw_keys, field_names):
                    sub = self._db.get_raw_data(key, start_date, end_date)
                    sub = sub.rename(columns={"value": name})
                    if merged is None:
                        merged = sub
                    else:
                        merged = merged.merge(sub, on="date", how="outer")
                df = merged
                if df is None or df.empty:
                    return None
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "database")
        except Exception as e:
            logger.warning("offline reconstruct for %s failed: %s", method, e)
            return None

    def fetch(self, indicator: str, method: str, *args, **kwargs) -> DataSourceResult:
        if os.environ.get("FGI_OFFLINE") == "1":
            # offline: 先尝试从 raw_data 重构，找不到则 FAILED
            if args and len(args) >= 2:
                # 末尾两个参数是 start_date/end_date（部分 calculator 在前面传 symbol 等位置参数）
                start_date, end_date = args[-2], args[-1]
                reconstructed = self._offline_reconstruct(indicator, method, start_date, end_date)
                if reconstructed is not None:
                    return reconstructed
            return DataSourceResult(None, DataSourceStatus.FAILED, "offline",
                                    f"offline mode: no raw data for {indicator}/{method}")
        if indicator not in self._chains:
            return DataSourceResult(None, DataSourceStatus.FAILED, "manager", f"No chain for {indicator}")
        return self._chains[indicator].fetch(method, *args, **kwargs)

    def health_check(self) -> Dict[str, Dict[int, DataSourceStatus]]:
        return {name: chain.health_check() for name, chain in self._chains.items()}
