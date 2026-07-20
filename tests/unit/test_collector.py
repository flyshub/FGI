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
