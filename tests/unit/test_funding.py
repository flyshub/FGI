import pytest
import tempfile
from pathlib import Path
from fgi.calculator.funding.f1 import F1Calculator
from fgi.calculator.funding.f2 import F2Calculator
from fgi.calculator.funding.f3 import F3Calculator
from fgi.collector.fallback import DataSourceManager
from fgi.collector.mock_source import MockSource
from fgi.storage.database import Database
import pandas as pd


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
    manager.configure_chain("f1_margin", ["mock"])
    manager.configure_chain("f1_market_cap", ["mock"])
    manager.configure_chain("f2_fund_position", ["mock"])
    manager.configure_chain("f3_industry_flow", ["mock"])
    manager.configure_chain("f3_index", ["mock"])
    return manager


@pytest.fixture
def f1_calculator(data_manager, db):
    return F1Calculator(data_manager, db)


@pytest.fixture
def f2_calculator(data_manager, db):
    return F2Calculator(data_manager, db)


@pytest.fixture
def f3_calculator(data_manager, db):
    return F3Calculator(data_manager, db)


class TestF1Calculator:
    """V3.8: margin_balance / market_cap ratio with monthly forward-fill"""

    def test_calculate_margin_ratio(self, f1_calculator):
        margin_df = pd.DataFrame({
            "date": ["2024-01-03"] * 100,
            "融资余额": [1000000.0] * 100,
        })
        cap_df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "market_cap": [463852.98] * 100,
        })
        result = f1_calculator.calculate_margin_ratio(margin_df, cap_df)
        assert "margin_ratio" in result.columns
        assert result["margin_ratio"].iloc[0] > 0

    def test_calculate_score(self, f1_calculator):
        score = f1_calculator.calculate_score(0.5)
        assert score == 50.0

        score = f1_calculator.calculate_score(0.0)
        assert score == 0.0

        score = f1_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, f1_calculator, db):
        result = f1_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["f1"] is not None
        assert 0 <= result["f1"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["F1"] == result["f1"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestF2Calculator:
    def test_calculate_percentile(self, f2_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "position": [90.0] * 100
        })
        result = f2_calculator.calculate_percentile(df)
        assert "fund_position" in result.columns
        assert "percentile" in result.columns

    def test_calculate_percentile_ffills_weekly_to_daily(self, f2_calculator):
        """周频仓位先 ffill 成日频序列再做滚动百分位（V3.8 2.4）"""
        df = pd.DataFrame({
            "date": pd.date_range("2023-01-06", periods=52, freq="W-FRI").strftime("%Y-%m-%d"),
            "position": [70.0 + i * 0.2 for i in range(52)],
        })
        result = f2_calculator.calculate_percentile(df)
        assert len(result) > 200  # 52 个周频点 ffill 为日频
        assert result["fund_position"].isna().sum() == 0

    def test_calculate_score(self, f2_calculator):
        score = f2_calculator.calculate_score(0.5)
        assert score == 50.0

        score = f2_calculator.calculate_score(0.0)
        assert score == 0.0

        score = f2_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, f2_calculator, db):
        result = f2_calculator.run("2024-01-10", lookback_days=2000)
        assert result["status"] == "normal"
        assert result["f2"] is not None
        assert 0 <= result["f2"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["F2"] == result["f2"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"

    def test_run_does_not_write_back_latest_week(self, f2_calculator, db):
        """不得把最近一周值以当日日期写回 raw_data（污染自身百分位窗口）"""
        f2_calculator.run("2024-01-10", lookback_days=2000)
        # 2024-01-10 是周三，周频数据没有该日期的行
        row = db.get_raw_data("f2_fund_position", "2024-01-10", "2024-01-10")
        assert row.empty


class TestF3Calculator:
    def test_calculate_flow_proxy(self, f3_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100.0 + i * 0.5 for i in range(100)],
            "volume": [1000000.0] * 100,
        })
        result = f3_calculator.calculate_flow_proxy(df)
        assert "flow_magnitude" in result.columns
        assert "flow_proxy" in result.columns
        assert "price_change" in result.columns

    def test_calculate_score(self, f3_calculator):
        score = f3_calculator.calculate_score(0.5)
        assert score == 50.0

        score = f3_calculator.calculate_score(0.0)
        assert score == 0.0

        score = f3_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_splice_real_proxy(self, f3_calculator):
        """真实资金流覆盖对应日期的 proxy 值，缺失日期保留 proxy"""
        proxy = pd.DataFrame({
            "date": ["2024-01-08", "2024-01-09", "2024-01-10"],
            "flow_magnitude": [10.0, 20.0, 30.0],
        })
        real = pd.DataFrame({"date": ["2024-01-09"], "value": [-7.0]})
        result = f3_calculator.splice_real_proxy(proxy, real)
        assert list(result["flow_magnitude"]) == [10.0, -7.0, 30.0]

    def test_run_with_mock_data(self, f3_calculator, db):
        result = f3_calculator.run("2024-01-10", lookback_days=2000)
        assert result["status"] == "normal"
        assert result["f3"] is not None
        assert 0 <= result["f3"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["F3"] == result["f3"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"

    def _index_only_calculator(self, db):
        """只配指数链（无行业资金流源），真实数据只靠 db 种子"""
        manager = DataSourceManager()
        manager.register_source("mock", MockSource("mock", healthy=True))
        manager.configure_chain("f3_index", ["mock"])
        return F3Calculator(manager, db)

    def test_negative_real_flow_scores_zero(self, db):
        """大幅净流出 = 恐慌低分；修复前 .abs() 会把它变成贪婪高分"""
        calc = self._index_only_calculator(db)
        db.upsert_raw_data("2024-01-10", "f3_industry_net_flow", -5e6)
        db.commit()
        result = calc.run("2024-01-10", lookback_days=400)
        assert result["status"] == "normal"
        assert result["f3"] == 0.0

    def test_today_falls_back_to_proxy_is_substituted(self, db):
        """当日无真实数据回退 proxy → 状态 substituted"""
        calc = self._index_only_calculator(db)
        db.upsert_raw_data("2024-01-09", "f3_industry_net_flow", 3e5)
        db.commit()
        result = calc.run("2024-01-10", lookback_days=400)
        assert result["status"] == "substituted"
        assert result["f3"] is not None