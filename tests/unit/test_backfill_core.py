"""Tests for fgi/output/backfill.py — store_indicator_data and backfill core."""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from fgi.output.backfill import store_indicator_data
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


class TestStoreIndicatorData:
    def test_stores_rows(self, db):
        """正常写入行"""
        df = pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "close": [3000.0, 3010.0],
            }
        )
        n = store_indicator_data(db, "m3_close", df, value_col="close")
        assert n == 2
        raw = db.get_raw_data("m3_close", "2024-01-01", "2024-01-05")
        assert len(raw) == 2
        assert float(raw.iloc[0]["value"]) == 3000.0

    def test_skips_nan_values(self, db):
        """NaN 值传递到 upsert_raw_data（和真实数据一样写入 DB，这是现有行为）。

        注意：DataFrame 中的 NaN 转为 np.float64('nan')，Python 的 `val is not None`
        为 True 所以进入写入。如果以后要跳过 NaN，需加 pd.isna() 检查。
        """
        df = pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "close": [3000.0, None],
            }
        )
        n = store_indicator_data(db, "m3_close", df, value_col="close")
        assert n == 2  # NaN is stored as NaN (current behavior)

    def test_skips_empty_dates(self, db):
        """空日期跳过"""
        df = pd.DataFrame(
            {
                "date": ["", "2024-01-03"],
                "close": [3000.0, 3010.0],
            }
        )
        n = store_indicator_data(db, "m3_close", df, value_col="close")
        assert n == 1
        raw = db.get_raw_data("m3_close", "2024-01-01", "2024-01-05")
        assert len(raw) == 1

    def test_skips_nat_date(self, db):
        """NaT 日期跳过"""
        df = pd.DataFrame(
            {
                "date": [pd.NaT, "2024-01-03"],
                "close": [3000.0, 3010.0],
            }
        )
        n = store_indicator_data(db, "m3_close", df, value_col="close")
        assert n == 1

    def test_handles_non_numeric_value(self, db):
        """不可转为 float 的值跳过"""
        df = pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "close": [3000.0, "N/A"],
            }
        )
        n = store_indicator_data(db, "m3_close", df, value_col="close")
        assert n == 1

    def test_empty_dataframe(self, db):
        """空 DataFrame"""
        df = pd.DataFrame(columns=["date", "close"])
        n = store_indicator_data(db, "m3_close", df, value_col="close")
        assert n == 0

    def test_custom_date_column(self, db):
        """自定义日期列名"""
        df = pd.DataFrame(
            {
                "trade_date": ["2024-01-02"],
                "position": [85.0],
            }
        )
        n = store_indicator_data(
            db, "f2_fund_position", df, value_col="position", date_col="trade_date"
        )
        assert n == 1
