import pandas as pd

from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager, FallbackChain
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
        result = manager.fetch(
            "test_indicator", "fetch_daily", "000001", "2024-01-01", "2024-01-10"
        )
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


class TestChainCapabilityValidation:
    """P2-5：写进链里但根本不能服务该链的数据源，必须在配置期就暴露。"""

    def test_incapable_source_is_dropped_with_warning(self, caplog):
        class OnlyDaily(DataSource):
            def fetch_daily(self, symbol, start_date, end_date):
                return DataSourceResult(None, DataSourceStatus.FAILED, "only")

            def fetch_index_daily(self, symbol, start_date, end_date):
                return self.fetch_daily(symbol, start_date, end_date)

            def health_check(self):
                return DataSourceStatus.HEALTHY

        manager = DataSourceManager()
        manager.register_source("only_daily", OnlyDaily())
        for a in ("alpha", "beta"):
            manager.register_source(a, MockSource(a))

        with caplog.at_level("WARNING"):
            manager.configure_chain("m1_zt_stats", ["only_daily", "alpha"])

        # only_daily 没有 fetch_zt_daily_summary → 不进链
        chain = manager._chains["m1_zt_stats"]
        assert [type(s).__name__ for s in chain._sources] == ["MockSource"]
        assert any("cannot serve chain m1_zt_stats" in r.getMessage() for r in caplog.records)

    def test_capable_sources_are_kept(self):
        manager = DataSourceManager()
        manager.register_source("mock", MockSource("mock"))
        manager.configure_chain("m1_zt_stats", ["mock"])
        assert len(manager._chains["m1_zt_stats"]._sources) == 1

    def test_unknown_chain_has_no_method_requirement(self):
        manager = DataSourceManager()
        manager.register_source("mock", MockSource("mock"))
        manager.configure_chain("ad_hoc_chain", ["mock"])
        assert len(manager._chains["ad_hoc_chain"]._sources) == 1


class TestConfigureManager:
    class _IndexOnly(DataSource):
        """只实现指数日线——mootdx/tencent 的真实能力形状。"""

        def fetch_daily(self, symbol, start_date, end_date):
            return DataSourceResult(None, DataSourceStatus.FAILED, "index_only")

        def fetch_index_daily(self, symbol, start_date, end_date):
            return self.fetch_daily(symbol, start_date, end_date)

        def health_check(self):
            return DataSourceStatus.HEALTHY

    def _manager(self):
        manager = DataSourceManager()
        manager.register_source("akshare", MockSource("akshare"))
        manager.register_source("index_only", self._IndexOnly())
        return manager

    def test_extra_fallbacks_only_land_where_supported(self):
        """mootdx/tencent 过去被追加到全部 14 条链，其中 12 条是死配置。"""
        from fgi.collector.chains import configure_manager

        manager = self._manager()
        configure_manager(manager, extra_fallbacks=["index_only"])

        indexed = [name for name, c in manager._chains.items() if len(c._sources) > 1]
        assert sorted(indexed) == ["f3_index", "m3_index"]

    def test_warns_when_primary_source_cannot_serve(self, caplog):
        from fgi.collector.chains import configure_manager

        manager = DataSourceManager()
        manager.register_source("akshare", MockSource("akshare"))

        class NoZt(DataSource):
            def fetch_daily(self, symbol, start_date, end_date):
                return DataSourceResult(None, DataSourceStatus.FAILED, "nozt")

            def fetch_index_daily(self, symbol, start_date, end_date):
                return self.fetch_daily(symbol, start_date, end_date)

            def health_check(self):
                return DataSourceStatus.HEALTHY

        manager.register_source("zzshare", NoZt())
        # 人为把 zzshare 写进 m1 链（历史上的死配置）
        import fgi.collector.chains as chains_module

        original = chains_module.DEFAULT_CHAINS["m1_zt_stats"]
        chains_module.DEFAULT_CHAINS["m1_zt_stats"] = ["zzshare", "akshare"]
        try:
            with caplog.at_level("WARNING"):
                configure_manager(manager)
        finally:
            chains_module.DEFAULT_CHAINS["m1_zt_stats"] = original

        assert any(
            "m1_zt_stats" in r.getMessage() and "zzshare" in r.getMessage() for r in caplog.records
        )
