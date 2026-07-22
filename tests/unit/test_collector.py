import pandas as pd
import pytest
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import FallbackChain, DataSourceManager
from fgi.collector.mock_source import MockSource


class TestDataSourceResult:
    def test_healthy(self):
        result = DataSourceResult(None, DataSourceStatus.HEALTHY, "test")
        assert result.status == DataSourceStatus.HEALTHY

    def test_failed(self):
        result = DataSourceResult(None, DataSourceStatus.FAILED, "test", "error")
        assert result.status == DataSourceStatus.FAILED
        assert result.error == "error"


class TestFallbackChain:
    def test_first_source_healthy(self):
        source1 = MockSource("source1", healthy=True)
        chain = FallbackChain([source1])
        result = chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.status == DataSourceStatus.HEALTHY
        assert result.source == "source1"

    def test_first_source_fails_second_succeeds(self):
        source1 = MockSource("source1", healthy=False)
        source2 = MockSource("source2", healthy=True)
        chain = FallbackChain([source1, source2])
        result = chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.status == DataSourceStatus.HEALTHY
        assert result.source == "source2"

    def test_all_sources_fail(self):
        source1 = MockSource("source1", healthy=False)
        source2 = MockSource("source2", healthy=False)
        chain = FallbackChain([source1, source2])
        result = chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED


class TestFallbackChainRecovery:
    def test_failed_source_retried_after_cooldown(self):
        source1 = MockSource("source1", healthy=False)
        source2 = MockSource("source2", healthy=True)
        chain = FallbackChain([source1, source2])
        chain._cooldown = 0
        result = chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.source == "source2"
        source1._healthy = True
        result = chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.source == "source1"

    def test_source_skipped_during_cooldown(self):
        source1 = MockSource("source1", healthy=False)
        source2 = MockSource("source2", healthy=True)
        chain = FallbackChain([source1, source2])
        chain._cooldown = 9999
        chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        source1._healthy = True
        result = chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.source == "source2"

    def test_long_disable_after_max_failures(self):
        source1 = MockSource("source1", healthy=False)
        source2 = MockSource("source2", healthy=True)
        chain = FallbackChain([source1, source2])
        chain._cooldown = 0
        chain._max_failures = 2
        chain._long_cooldown = 9999
        for _ in range(2):
            chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        source1._healthy = True
        result = chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.source == "source2"

    def test_missing_method_pruned_not_failed(self):
        class PartialSource(DataSource):
            def fetch_daily(self, *args, **kwargs):
                return DataSourceResult(None, DataSourceStatus.HEALTHY, "partial")

            def fetch_index_daily(self, *args, **kwargs):
                return self.fetch_daily(*args, **kwargs)

            def health_check(self):
                return DataSourceStatus.HEALTHY

        partial = PartialSource()
        mock = MockSource("mock", healthy=True)
        chain = FallbackChain([partial, mock])
        result = chain.fetch("fetch_zt_pool", "2024-01-01", "2024-01-10")
        assert result.status == DataSourceStatus.HEALTHY
        assert result.source == "mock"
        assert chain._failures.get(0, 0) == 0
        assert 0 in chain._unsupported

    def test_degraded_result_returned_when_nothing_healthy(self):
        class DegradedSource(DataSource):
            def fetch_daily(self, *args, **kwargs):
                df = pd.DataFrame({"date": ["2024-01-01"], "close": [1.0]})
                return DataSourceResult(df, DataSourceStatus.DEGRADED, "deg")

            def fetch_index_daily(self, *args, **kwargs):
                return self.fetch_daily(*args, **kwargs)

            def health_check(self):
                return DataSourceStatus.DEGRADED

        chain = FallbackChain([DegradedSource()])
        result = chain.fetch("fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.status == DataSourceStatus.DEGRADED
        assert result.data is not None


class TestDataSourceManager:
    def test_register_and_configure(self):
        manager = DataSourceManager()
        source = MockSource("mock")
        manager.register_source("mock", source)
        manager.configure_chain("test_indicator", ["mock"])
        assert "test_indicator" in manager._chains

    def test_fetch(self):
        manager = DataSourceManager()
        source = MockSource("mock", healthy=True)
        manager.register_source("mock", source)
        manager.configure_chain("test_indicator", ["mock"])
        result = manager.fetch("test_indicator", "fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.status == DataSourceStatus.HEALTHY

    def test_fetch_no_chain(self):
        manager = DataSourceManager()
        result = manager.fetch("nonexistent", "fetch_daily", "000001", "2024-01-01", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED

    def test_has_source(self):
        manager = DataSourceManager()
        manager.register_source("s1", MockSource("s1", healthy=True))
        assert manager.has_source("s1") is True
        assert manager.has_source("s2") is False
