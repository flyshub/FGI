import logging
import time
from typing import Dict, List, Set
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus

logger = logging.getLogger(__name__)


COOLDOWN = 300          # 失败后冷却秒数
MAX_FAILURES = 5        # 连续失败达到此次数进入长禁用
LONG_COOLDOWN = 3600    # 长禁用秒数


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

    def register_source(self, name: str, source: DataSource):
        self._sources[name] = source

    def has_source(self, name: str) -> bool:
        return name in self._sources

    def configure_chain(self, indicator: str, source_names: List[str]):
        sources = [self._sources[name] for name in source_names if name in self._sources]
        self._chains[indicator] = FallbackChain(sources)

    def fetch(self, indicator: str, method: str, *args, **kwargs) -> DataSourceResult:
        if indicator not in self._chains:
            return DataSourceResult(None, DataSourceStatus.FAILED, "manager", f"No chain for {indicator}")
        return self._chains[indicator].fetch(method, *args, **kwargs)

    def health_check(self) -> Dict[str, Dict[int, DataSourceStatus]]:
        return {name: chain.health_check() for name, chain in self._chains.items()}
