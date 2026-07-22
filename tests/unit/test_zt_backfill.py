"""zt_backfill 单元测试：写 s3_seal_fund（非 s4_seal_fund）、封单额亿元、M1/S3 同源。
注入假 levistock，不依赖真实包与网络。"""
import sys
import types

import pytest

from fgi.storage.database import Database


@pytest.fixture
def ztb(monkeypatch, tmp_path):
    """导入 zt_backfill 并把模块级 levistock 句柄替换为假实现。"""
    fake_ls = types.SimpleNamespace()
    fake_ls.market_emotion_kph = lambda date=None: {"sjzt": 42, "zt": 40}
    fake_ls.limit_up_his_kph = lambda date=None: [
        {"seal_money": 2e8}, {"seal_money": 1e8}]
    try:
        import fgi.output.zt_backfill as module
    except ImportError:
        monkeypatch.setitem(sys.modules, "levistock", fake_ls)
        import fgi.output.zt_backfill as module
    monkeypatch.setattr(module, "ls", fake_ls)
    monkeypatch.setattr(module, "resolve_trading_days",
                        lambda s, e, db=None: [s] if s == e else [s, e])
    return module, fake_ls


class TestFetchM1S3:
    def test_zt_count_from_emotion(self, ztb):
        # M1 与日线 fetch_zt_daily_summary 同源：market_emotion_kph 的 sjzt
        module, _ = ztb
        result = module.fetch_m1_s3("2024-01-02")
        assert result["zt_count"] == 42  # 不是 len(limit_up)=2

    def test_seal_fund_in_yi(self, ztb):
        module, _ = ztb
        result = module.fetch_m1_s3("2024-01-02")
        assert result["seal_fund_sum"] == pytest.approx(3.0)  # 3e8 / 1e8 亿元

    def test_emotion_missing_falls_back_to_list_len(self, ztb, monkeypatch):
        module, fake_ls = ztb
        fake_ls.market_emotion_kph = lambda date=None: None
        result = module.fetch_m1_s3("2024-01-02")
        assert result["zt_count"] == 2


class TestZtBackfillKeys:
    def test_writes_s3_seal_fund_not_s4(self, ztb, tmp_path):
        module, _ = ztb
        db_path = tmp_path / "test.db"
        module.zt_backfill("2024-01-02", "2024-01-02", db_path=db_path)
        with Database(db_path) as db:
            db.init_schema()
            s3 = db.get_raw_data("s3_seal_fund", "2024-01-02", "2024-01-02")
            m1 = db.get_raw_data("m1_zt_count", "2024-01-02", "2024-01-02")
            s4 = db.get_raw_data("s4_seal_fund", "2024-01-02", "2024-01-02")
            assert len(s3) == 1
            assert s3["value"].iloc[0] == pytest.approx(3.0)
            assert len(m1) == 1
            assert m1["value"].iloc[0] == 42.0
            assert s4.empty

    def test_skip_existing_dates(self, ztb, tmp_path):
        module, fake_ls = ztb
        db_path = tmp_path / "test.db"
        with Database(db_path) as db:
            db.init_schema()
            db.upsert_raw_data("2024-01-02", "m1_zt_count", 99.0)
            db.upsert_raw_data("2024-01-02", "s3_seal_fund", 9.9)
            db.commit()
        module.zt_backfill("2024-01-02", "2024-01-02", db_path=db_path)
        with Database(db_path) as db:
            m1 = db.get_raw_data("m1_zt_count", "2024-01-02", "2024-01-02")
            assert m1["value"].iloc[0] == 99.0  # 未被覆盖
