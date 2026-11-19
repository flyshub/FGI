from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from fgi.storage.database import Database

from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.indicators.registry import (
    CHAIN_METHODS,
    INDEX_DAILY_CHAINS,
    METHOD_RAW,
)

logger = logging.getLogger(__name__)


COOLDOWN = 300  # 失败后冷却秒数
MAX_FAILURES = 5  # 连续失败达到此次数进入长禁用
LONG_COOLDOWN = 3600  # 长禁用秒数


class FallbackChain:
    def __init__(self, sources: list[DataSource]):
        self._sources = list(sources)
        self._status: dict[int, DataSourceStatus] = {}
        self._failures: dict[int, int] = {}
        self._last_failure: dict[int, float] = {}
        self._unsupported: set[int] = set()
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
                logger.info(
                    "Source %s does not implement %s, removed from chain",
                    type(source).__name__,
                    method,
                )
                continue
            try:
                result = func(*args, **kwargs)
                if result.status == DataSourceStatus.HEALTHY:
                    self._record_success(i)
                    return result
                self._record_failure(i, result.status)
                if (
                    result.status == DataSourceStatus.DEGRADED
                    and result.data is not None
                    and degraded is None
                ):
                    degraded = result
            except Exception:
                self._record_failure(i, DataSourceStatus.FAILED)
                continue
        if degraded is not None:
            return degraded
        return DataSourceResult(
            None, DataSourceStatus.FAILED, "fallback_chain", "All sources failed"
        )

    def health_check(self) -> dict[int, DataSourceStatus]:
        statuses = {}
        for i, source in enumerate(self._sources):
            statuses[i] = source.health_check()
            self._status[i] = statuses[i]
        return statuses


class DataSourceManager:
    def __init__(self):
        self._chains: dict[str, FallbackChain] = {}
        self._sources: dict[str, DataSource] = {}
        self._db: Database | None = None  # 可选：注入 Database 用于 offline 重构

    def register_source(self, name: str, source: DataSource):
        self._sources[name] = source

    def set_db(self, db: Database) -> None:
        """注入 Database 实例，用于 FGI_OFFLINE 模式下从 raw_data 重构 DataFrame。"""
        self._db = db

    def has_source(self, name: str) -> bool:
        return name in self._sources

    def can_serve(self, name: str, method: str | None) -> bool:
        """该数据源是否实现了指定取数方法（method 为 None 时不作要求）。"""
        source = self._sources.get(name)
        if source is None:
            return False
        return method is None or callable(getattr(source, method, None))

    def configure_chain(self, indicator: str, source_names: list[str]):
        """装配一条链，并在配置期剔除「声明了但根本不能服务该链」的数据源。

        此前这类组合会被静默接受，直到运行时才在 FallbackChain 里被剔除——
        等于你写了一个兜底、以为它在工作，其实从来没生效（P2-5）。
        """
        method = CHAIN_METHODS.get(indicator)
        sources = []
        for name in source_names:
            source = self._sources.get(name)
            if source is None:
                continue
            if method and not callable(getattr(source, method, None)):
                logger.warning(
                    "%s cannot serve chain %s (no %s) — dropped from configuration",
                    name,
                    indicator,
                    method,
                )
                continue
            sources.append(source)
        self._chains[indicator] = FallbackChain(sources)

    def _offline_reconstruct(
        self, indicator: str, method: str, start_date: str, end_date: str
    ) -> DataSourceResult | None:
        """从 raw_data 重构 DataFrame；找不到映射或无数据返回 None。"""
        if self._db is None:
            return None
        mapping = METHOD_RAW.get(method)
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
                merged: pd.DataFrame | None = None
                for key, name in zip(raw_keys, field_names, strict=False):
                    sub = self._db.get_raw_data(key, start_date, end_date)
                    sub = sub.rename(columns={"value": name})
                    merged = sub if merged is None else merged.merge(sub, on="date", how="outer")
                df = merged
                if df is None or df.empty:
                    return None
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "database")
        except Exception as e:
            logger.warning("offline reconstruct for %s failed: %s", method, e)
            return None

    def fetch(self, indicator: str, method: str, *args, **kwargs) -> DataSourceResult:
        # FGI_OFFLINE 是 recompute_scores 内部机制，不为 daily_run / 常规使用设计。
        # 从 raw_data 重构 DataFrame 绕过所有 API 调用，数据缺失时静默 FAILED。
        # 不要在生产推送中使用，否则会跳过实时数据采集导致 FGI 基于陈旧数据。
        if os.environ.get("FGI_OFFLINE") == "1":
            if args and len(args) >= 2:
                start_date, end_date = args[-2], args[-1]
                reconstructed = self._offline_reconstruct(indicator, method, start_date, end_date)
                if reconstructed is not None:
                    return reconstructed
            return DataSourceResult(
                None,
                DataSourceStatus.FAILED,
                "offline",
                f"offline mode: no raw data for {indicator}/{method}",
            )
        if indicator not in self._chains:
            return DataSourceResult(
                None, DataSourceStatus.FAILED, "manager", f"No chain for {indicator}"
            )
        return self._chains[indicator].fetch(method, *args, **kwargs)

    def health_check(self) -> dict[str, dict[int, DataSourceStatus]]:
        return {name: chain.health_check() for name, chain in self._chains.items()}
