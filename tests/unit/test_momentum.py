import pytest
import tempfile
from pathlib import Path
from fgi.calculator.momentum.m1 import M1Calculator
from fgi.calculator.momentum.m2 import M2Calculator
from fgi.calculator.momentum.m4 import M4Calculator
from fgi.collector.fallback import DataSourceManager
from fgi.collector.mock_source import MockSource
from fgi.common.utils import clear_percentile_cache
from fgi.storage.database import Database
import pandas as pd


@pytest.fixture(autouse=True)
def _clear_percentile_cache():
    """Module-level _PERCENTILE_CACHE causes cross-test pollution when two tests
    produce series with identical (length, hash) keys but different semantics.
    Clear before each test to guarantee a clean computation."""
    clear_percentile_cache()
    yield
    clear_percentile_cache()


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        path = Path(tmp.name)
    database = Database(path)
    with database:
        database.init_schema()
        yield database
    path.unlink(missing_ok=True)


@pytest.fixture
def data_manager():
    manager = DataSourceManager()
    mock = MockSource("mock", healthy=True)
    manager.register_source("mock", mock)
    # 为每个指标配置 chain
    manager.configure_chain("m1_zt_stats", ["mock"])
    manager.configure_chain("m2_market_overview", ["mock"])
    manager.configure_chain("m4_cyb_volume", ["mock"])
    return manager


@pytest.fixture
def m1_calculator(data_manager, db):
    return M1Calculator(data_manager, db)


@pytest.fixture
def m2_calculator(data_manager, db):
    return M2Calculator(data_manager, db)


@pytest.fixture
def m4_calculator(data_manager, db):
    return M4Calculator(data_manager, db)


class TestM1Calculator:
    def test_calculate_zt_count(self, m1_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "limit_up_count": [10] * 100
        })
        result = m1_calculator.calculate_zt_count(df)
        assert "zt_count" in result.columns
        assert result["zt_count"].iloc[0] == 10

    def test_calculate_score(self, m1_calculator):
        score = m1_calculator.calculate_score(0.5)
        assert score == 50.0

        score = m1_calculator.calculate_score(0.0)
        assert score == 0.0

        score = m1_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, m1_calculator, db):
        result = m1_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["m1"] is not None
        assert 0 <= result["m1"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["M1"] == result["m1"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"

    def test_null_today_triggers_refetch(self, m1_calculator, db, monkeypatch):
        """当 db_data 里 today 的 value 是 NULL 时，calc 应该触发 fetch 而非跳过"""
        from fgi.collector.base import DataSourceResult, DataSourceStatus

        # 灌入 500 天历史有效值（不含 today），再为 today 写 NULL（模拟清理后状态）
        dates = pd.bdate_range("2022-01-03", "2023-12-28")
        for i, d in enumerate(dates):
            db.upsert_raw_data(d.strftime("%Y-%m-%d"), "m1_zt_count",
                               float(20 + (i % 50)))
        db.upsert_raw_data("2023-12-28", "m1_zt_count", None)
        db.commit()

        fetched = {"called": False}
        def mock_fetch(start_date, end_date):
            fetched["called"] = True
            if start_date == "2023-12-28":
                df = pd.DataFrame({
                    "date": ["2023-12-28"],
                    "limit_up_count": [30],
                })
                return DataSourceResult(df, DataSourceStatus.HEALTHY, "mock")
            return DataSourceResult(None, DataSourceStatus.FAILED, "mock")
        monkeypatch.setattr(m1_calculator, "fetch_data", mock_fetch)

        result = m1_calculator.run("2023-12-28", lookback_days=400)
        assert fetched["called"], "NULL today must trigger fetch_data"
        assert result["status"] == "normal", f"got {result['status']}"
        assert result["m1"] is not None


class TestM2Calculator:
    def test_calculate_sentiment_ratio(self, m2_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "up_num": [100] * 100,
            "down_num": [50] * 100
        })
        result = m2_calculator.calculate_sentiment_ratio(df)
        assert "bullish_ratio" in result.columns
        assert result["bullish_ratio"].iloc[0] == 100 / (100 + 50)

    def test_calculate_score(self, m2_calculator):
        score = m2_calculator.calculate_score(0.5)
        assert score == 50.0

        score = m2_calculator.calculate_score(0.0)
        assert score == 0.0

        score = m2_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, m2_calculator, db):
        result = m2_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["m2"] is not None
        assert 0 <= result["m2"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["M2"] == result["m2"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestM4Calculator:
    """V3.8 2.1: M4 = 创业板换手率 60 日 Z-score 的滚动百分位"""

    def test_calculate_volume_zscore(self, m4_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "volume": [1e9 + (i % 10) * 1e7 for i in range(100)]
        })
        result = m4_calculator.calculate_volume_zscore(df)
        assert "volume_zscore" in result.columns
        # 前 59 行不足 60 日窗口为 NaN，之后为有效 Z-score
        assert pd.isna(result["volume_zscore"].iloc[58])
        assert not pd.isna(result["volume_zscore"].iloc[-1])

    def test_calculate_score(self, m4_calculator):
        score = m4_calculator.calculate_score(0.5)
        assert score == 50.0

        score = m4_calculator.calculate_score(0.0)
        assert score == 0.0

        score = m4_calculator.calculate_score(1.0)
        assert score == 100.0

    def _seed_volume_history(self, db, end_date="2024-01-10"):
        dates = pd.bdate_range("2022-01-03", end_date)
        for i, d in enumerate(dates):
            db.upsert_raw_data(d.strftime("%Y-%m-%d"), "m4_volume",
                               float(1e9 + (i % 20) * 1e7))
        db.commit()

    def test_run_with_db_volume(self, m4_calculator, db):
        """raw_data 有足量 m4_volume（成交量）时正常计算"""
        self._seed_volume_history(db)
        result = m4_calculator.run("2024-01-10", lookback_days=600)
        assert result["status"] == "normal"
        assert result["m4"] is not None
        assert 0 <= result["m4"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["M4"] == result["m4"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"

        z = db.get_raw_data("m4_zscore", "2024-01-10", "2024-01-10")
        assert len(z) == 1

    def test_run_constant_volume_missing(self, m4_calculator, db):
        """mock 常数成交量 std=0 → Z-score 无效 → missing，不得编造得分"""
        result = m4_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "missing"
        assert result["m4"] is None

    def test_degraded_status_not_overwritten(self, m4_calculator, db, monkeypatch):
        """#46: last-good-value 触发的 degraded 状态不应被 run() 末尾的 normal 覆写"""
        # 灌入历史 volume（不含当日，让 today_in_db=False）
        dates = pd.bdate_range("2022-01-03", "2024-01-09")
        for i, d in enumerate(dates):
            db.upsert_raw_data(d.strftime("%Y-%m-%d"), "m4_volume",
                               float(1e9 + (i % 20) * 1e7))
        db.commit()
        # 让 fetch_data 返回失败（触发 db_data.empty → last-good-value 路径）
        from fgi.collector.base import DataSourceResult, DataSourceStatus

        def fake_fetch(*args, **kwargs):
            return DataSourceResult(
                status=DataSourceStatus.FAILED, data=None, source="mock", error="simulated"
            )
        monkeypatch.setattr(m4_calculator, "fetch_data", fake_fetch)
        # 让 _get_last_good_volume 返回有效值（绕过 db_data.empty 但触发 last-good）
        monkeypatch.setattr(m4_calculator, "_get_last_good_volume", lambda date: 1.5e9)
        # 让 db_data 第一次为空（触发 fetch 路径），但写入 last-good 后再次 read 仍有历史
        # 直接 patch 两次连续调用：第一次返回空（让 db_data.empty=True），第二次返回历史
        original_get = db.get_raw_data
        call_count = {"n": 0}

        def patched_get(indicator, start, end):
            call_count["n"] += 1
            # 第 1 次（M4.run 初始 db_data）返回空 → 进入 fetch 分支
            if call_count["n"] == 1:
                import pandas as _pd
                return _pd.DataFrame(columns=["date", "value"])
            return original_get(indicator, start, end)
        monkeypatch.setattr(db, "get_raw_data", patched_get)
        monkeypatch.setattr(m4_calculator._db, "get_raw_data", patched_get)

        result = m4_calculator.run("2024-01-10", lookback_days=600)
        # last-good-value 应触发 degraded
        assert result["status"] == "degraded", f"expected degraded, got {result['status']}"
        # DB 中状态必须是 degraded，不能被覆写为 normal
        status = db.get_status("2024-01-10")
        m4_status = status[status["indicator"] == "m4"]
        assert len(m4_status) == 1
        assert m4_status.iloc[0]["status"] == "degraded", \
            f"#46 regression: degraded overwritten to {m4_status.iloc[0]['status']}"

    def test_null_today_triggers_refetch(self, m4_calculator, db, monkeypatch):
        """当 db_data 里 today 的 value 是 NULL 时，calc 应该触发 fetch 而非跳过"""
        from fgi.collector.base import DataSourceResult, DataSourceStatus

        # 灌入 500 天历史 volume（不含 today），再为 today 写 NULL
        dates = pd.bdate_range("2022-01-03", "2023-12-28")
        for i, d in enumerate(dates):
            db.upsert_raw_data(d.strftime("%Y-%m-%d"), "m4_volume",
                               float(1e9 + (i % 30) * 1e7))
        db.upsert_raw_data("2023-12-28", "m4_volume", None)
        db.commit()

        fetched = {"called": False}
        def mock_fetch(start_date, end_date):
            fetched["called"] = True
            if end_date == "2023-12-28":
                df = pd.DataFrame({
                    "date": ["2023-12-28"],
                    "volume": [1.5e9],
                })
                return DataSourceResult(df, DataSourceStatus.HEALTHY, "mock")
            return DataSourceResult(None, DataSourceStatus.FAILED, "mock")
        monkeypatch.setattr(m4_calculator, "fetch_data", mock_fetch)

        result = m4_calculator.run("2023-12-28", lookback_days=400)
        assert fetched["called"], "NULL today must trigger fetch_data"
        assert result["status"] == "normal", f"got {result['status']}"
        assert result["m4"] is not None