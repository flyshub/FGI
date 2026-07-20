from typing import List, Dict, Type
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus


class FallbackChain:
    def __init__(self, sources: List[DataSource]):
        self._sources = sources
        self._status: Dict[int, DataSourceStatus] = {}

    def fetch(self, method: str, *args, **kwargs) -> DataSourceResult:
        for i, source in enumerate(self._sources):
            if self._status.get(i) == DataSourceStatus.FAILED:
                continue
            try:
                func = getattr(source, method)
                result = func(*args, **kwargs)
                if result.status == DataSourceStatus.HEALTHY:
                    return result
                self._status[i] = result.status
            except Exception as e:
                self._status[i] = DataSourceStatus.FAILED
                continue
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

    def configure_chain(self, indicator: str, source_names: List[str]):
        sources = [self._sources[name] for name in source_names if name in self._sources]
        self._chains[indicator] = FallbackChain(sources)

    def fetch(self, indicator: str, method: str, *args, **kwargs) -> DataSourceResult:
        if indicator not in self._chains:
            return DataSourceResult(None, DataSourceStatus.FAILED, "manager", f"No chain for {indicator}")
        return self._chains[indicator].fetch(method, *args, **kwargs)

    def health_check(self) -> Dict[str, Dict[int, DataSourceStatus]]:
        return {name: chain.health_check() for name, chain in self._chains.items()}
