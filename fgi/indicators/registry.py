"""指标与数据源知识的单一真相表（P2-2）。

raw_key 过去散落在三处：
- ``collector/fallback.py`` 的 OFFLINE_RAW_MAPPING 与 INDICATOR_RAW_KEY
- ``calculator/*`` 各指标写入 raw_data 时的字面量
- ``output/backfill.py`` 的 indicators_config

改名必须手工同步，任何一处漏改都会让 offline 重构或 forward-fill 溯源悄悄失效。
现在所有映射只在这里定义，其余模块引用本表。

新增/修改指标时：
1. 在 ``CHAIN_METHODS`` 里声明该链消费哪个取数方法（configure 时据此校验数据源能力）
2. 在 ``METHOD_RAW`` 里声明该方法落库的 raw_key 与重构字段名
3. 若该方法被多个链共用（fetch_index_daily），在 ``INDEX_DAILY_CHAINS`` 里按链分流
"""

from __future__ import annotations

# 链（指标取数入口）→ 它唯一消费的方法。
# configure_chain 用它校验「这个数据源到底能不能服务这条链」，
# 不能服务的组合在配置期就被拒绝，而不是运行时静默剔除（P2-5）。
CHAIN_METHODS: dict[str, str] = {
    "m1_zt_stats": "fetch_zt_daily_summary",
    "m2_market_overview": "fetch_open_sentiment",
    "m3_index": "fetch_index_daily",
    "m4_cyb_volume": "fetch_cyb_daily",
    "s2_sentiment": "fetch_market_hot_sentiment",
    "s3_zt_daily": "fetch_zt_daily_summary",
    "v1_pe": "fetch_pe_data",
    "v1_bond": "fetch_bond_yield",
    "v1_pb": "fetch_pb_data",
    "v4_qvix": "fetch_qvix",
    "f1_margin": "fetch_margin_data",
    "f1_market_cap": "fetch_market_cap",
    "f2_fund_position": "fetch_fund_position",
    "f3_index": "fetch_index_daily",
}

# 方法 → (raw_key(s), 重构字段名(s))
# FGI_OFFLINE 模式下从 raw_data 重建 DataFrame；未列入的方法在 offline 下保持 FAILED。
# 单字段：value 列直接改名；多字段：按 date 对齐外连接。
METHOD_RAW: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "fetch_margin_data": (("f1_margin_balance",), ("融资余额",)),
    "fetch_market_cap": (("f1_market_cap",), ("market_cap",)),
    "fetch_fund_position": (("f2_fund_position",), ("position",)),
    "fetch_market_hot_sentiment": (("s2_heat",), ("p_close",)),
    "fetch_zt_daily_summary": (
        ("m1_zt_count", "s3_seal_fund"),
        ("limit_up_count", "seal_fund_sum"),
    ),
    "fetch_pe_data": (("v1_pe_ttm",), ("滚动市盈率",)),
    "fetch_pb_data": (("v1_pb",), ("市净率",)),
    "fetch_bond_yield": (("v1_bond_yield",), ("yield_10y",)),
    "fetch_cyb_daily": (("m4_volume",), ("volume",)),
    "fetch_open_sentiment": (("m2_up_num", "m2_down_num"), ("up_num", "down_num")),
    "fetch_qvix": (("v4_qvix",), ("close",)),
}

# fetch_index_daily 被 m3 与 f3 共用，按链分流到不同 raw_key
INDEX_DAILY_CHAINS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "m3_index": (("m3_close",), ("close",)),
    "f3_index": (("f3_proxy_close", "f3_proxy_volume"), ("close", "volume")),
}

# 指标 → 溯源用的主 raw_key（forward-fill 时找出「这个分数来自哪天的原始数据」）
INDICATOR_RAW_KEY: dict[str, str] = {
    "M1": "m1_zt_count",
    "M2": "m2_up_num",
    "M3": "m3_close",
    "M4": "m4_volume",
    "S2": "s2_heat",
    "S3": "s3_seal_fund",
    "V1": "v1_pe_ttm",
    "V2": "v1_erp",
    "V4": "v4_qvix",
    "F1": "f1_margin_ratio",
    "F2": "f2_fund_position",
    "F3": "f3_percentile",
}


def raw_keys_for(method: str, chain: str | None = None) -> tuple[str, ...] | None:
    """该方法（或该链）落库用的 raw_key 列表；未声明返回 None。"""
    if method == "fetch_index_daily" and chain is not None:
        spec = INDEX_DAILY_CHAINS.get(chain)
        return spec[0] if spec else None
    spec = METHOD_RAW.get(method)
    return spec[0] if spec else None


def raw_key_for_field(method: str, field: str, chain: str | None = None) -> str | None:
    """按重构字段名反查 raw_key，供回填按列名配置时使用。"""
    if method == "fetch_index_daily" and chain is not None:
        spec = INDEX_DAILY_CHAINS.get(chain)
    else:
        spec = METHOD_RAW.get(method)
    if spec is None:
        return None
    keys, fields = spec
    if field not in fields:
        return None
    return keys[fields.index(field)]
