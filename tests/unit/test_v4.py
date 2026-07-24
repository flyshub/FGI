"""V4Calculator (QVIX 期权隐含波动率) 单元测试。"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fgi.calculator.valuation.v4 import V4Calculator
from fgi.collector.fallback import DataSourceManager
from fgi.collector.mock_source import MockSource
from fgi.storage.database import Database


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
    manager.configure_chain("v4_qvix", ["mock"])
    return manager


@pytest.fixture
def v4_calculator(data_manager, db):
    return V4Calculator(data_manager, db)


class TestV4Calculator:
    """V3.8.7: V4 = 50ETF QVIX 反向百分位（高 VIX → 低分 = 恐慌）。"""

    def test_calculate_score_reverse(self, v4_calculator):
        # percentile=1.0 (VIX 历史最高) → 得分 0 (极度恐惧)
        assert v4_calculator.calculate_score(1.0) == 0.0
        # percentile=0.0 (VIX 历史最低) → 得分 100 (极度平静/贪婪)
        assert v4_calculator.calculate_score(0.0) == 100.0
        # 中位 VIX → 50
        assert v4_calculator.calculate_score(0.5) == 50.0

    def test_run_with_mock_data(self, v4_calculator, db):
        result = v4_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["v4"] is not None
        assert 0 <= result["v4"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["V4"] == result["v4"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"

    def test_qvix_spike_yields_low_score(self, v4_calculator, db):
        """模拟 VIX 飙升场景：在历史平稳序列末尾插入极高值 → 当日得分应为低分。"""
        # 构造 1300 天平稳 + 末尾 100 天飙升的数据
        dates = pd.date_range("2018-01-01", periods=1400, freq="B").strftime("%Y-%m-%d")
        n = len(dates)
        baseline = 18.0 + np.sin(np.linspace(0, 4 * np.pi, n - 100)) * 1.0  # 平稳波动
        spike = np.linspace(18, 60, 100)  # 末尾 100 天飙升至 60
        full = np.concatenate([baseline, spike])
        df = pd.DataFrame({"date": dates, "qvix": full})
        for _, row in df.iterrows():
            db.upsert_raw_data(row["date"], "v4_qvix", float(row["qvix"]))
        db.commit()

        target = dates[-1]
        result = v4_calculator.run(target, lookback_days=1400)
        assert result["status"] == "normal"
        # VIX 在末尾飙到历史最高 → percentile ≈ 1.0 → 反向得分 ≈ 0
        assert result["v4"] < 10, f"恐慌时期权 VIX 飙升应得低分，实际 {result['v4']}"
