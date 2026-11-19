"""registry 是 raw_key / 链-方法 知识的唯一来源（P2-2）。

这些测试防的是「某处又写了一份映射，改名时漏改」。
"""

import pytest

from fgi.collector.chains import DEFAULT_CHAINS
from fgi.indicators.registry import (
    CHAIN_METHODS,
    INDEX_DAILY_CHAINS,
    INDICATOR_RAW_KEY,
    METHOD_RAW,
    raw_key_for_field,
    raw_keys_for,
)

ALL_INDICATORS = ["M1", "M2", "M3", "M4", "S2", "S3", "V1", "V2", "V4", "F1", "F2", "F3"]


class TestCoverage:
    def test_every_configured_chain_declares_its_method(self):
        missing = sorted(set(DEFAULT_CHAINS) - set(CHAIN_METHODS))
        assert missing == [], f"这些链没有声明方法，configure 时无法校验数据源能力: {missing}"

    def test_no_declared_chain_is_unused(self):
        stale = sorted(set(CHAIN_METHODS) - set(DEFAULT_CHAINS))
        assert stale == [], f"这些链已不在 DEFAULT_CHAINS 中，registry 里是死条目: {stale}"

    def test_every_method_has_raw_keys(self):
        for chain, method in CHAIN_METHODS.items():
            assert raw_keys_for(method, chain), f"{chain} → {method} 没有 raw_key"

    def test_every_indicator_has_traceability_key(self):
        assert sorted(INDICATOR_RAW_KEY) == sorted(ALL_INDICATORS)

    def test_declared_keys_match_field_count(self):
        for method, (keys, fields) in METHOD_RAW.items():
            assert len(keys) == len(fields), f"{method}: raw_key 与字段数量不一致"
        for chain, (keys, fields) in INDEX_DAILY_CHAINS.items():
            assert len(keys) == len(fields), f"{chain}: raw_key 与字段数量不一致"


class TestBackfillDerivation:
    """backfill 的 9 个回填项必须能从 registry 反查出 raw_key。"""

    ROWS = [
        ("m3_index", "fetch_index_daily", "close", "m3_close"),
        ("m4_cyb_volume", "fetch_cyb_daily", "volume", "m4_volume"),
        ("v1_pe", "fetch_pe_data", "滚动市盈率", "v1_pe_ttm"),
        ("v1_bond", "fetch_bond_yield", "yield_10y", "v1_bond_yield"),
        ("f1_margin", "fetch_margin_data", "融资余额", "f1_margin_balance"),
        ("f1_market_cap", "fetch_market_cap", "market_cap", "f1_market_cap"),
        ("f2_fund_position", "fetch_fund_position", "position", "f2_fund_position"),
        ("f3_index", "fetch_index_daily", "close", "f3_proxy_close"),
        ("f3_index", "fetch_index_daily", "volume", "f3_proxy_volume"),
    ]

    @pytest.mark.parametrize("chain,method,field,expected", ROWS)
    def test_derives_expected_raw_key(self, chain, method, field, expected):
        assert raw_key_for_field(method, field, chain) == expected

    def test_f3_and_m3_do_not_collide(self):
        """fetch_index_daily 被两个链共用：按链分流，close 不能都映射到 m3_close。"""
        assert raw_key_for_field("fetch_index_daily", "close", "m3_index") == "m3_close"
        assert raw_key_for_field("fetch_index_daily", "close", "f3_index") == "f3_proxy_close"
        assert raw_key_for_field("fetch_index_daily", "close", "m3_index") != raw_key_for_field(
            "fetch_index_daily", "close", "f3_index"
        )

    def test_unknown_field_returns_none(self):
        assert raw_key_for_field("fetch_pe_data", "不存在的列") is None
        assert raw_key_for_field("fetch_nonexistent", "close") is None
