"""数据源链配置：指标 → 首选来源映射。

daily_run 和 backfill 共用此配置，按需追加兜底来源（如 mootdx/tencent）。
FallbackChain 自动剔除不支持的方法，追加到所有链是安全的。
"""

from typing import Dict, List, Optional
from fgi.collector.fallback import DataSourceManager


# 指标 → 首选来源列表
# m1/s3 包含 zzshare + akshare 备份；其余指标单来源
DEFAULT_CHAINS: Dict[str, List[str]] = {
    "m1_zt_stats": ["zzshare", "akshare"],
    "m2_market_overview": ["zzshare"],
    "m3_index": ["akshare"],
    "m4_cyb_volume": ["akshare"],
    "s2_sentiment": ["zzshare"],
    "s3_zt_daily": ["zzshare", "akshare"],
    "v1_pe": ["akshare"],
    "v1_bond": ["akshare"],
    "v2_index": ["akshare"],
    "f1_margin": ["akshare"],
    "f1_market_cap": ["akshare"],
    "f2_fund_position": ["akshare"],
    "f3_index": ["akshare"],
}


def configure_manager(
    manager: DataSourceManager,
    extra_fallbacks: Optional[List[str]] = None,
) -> None:
    """为 DataSourceManager 配置所有指标链。

    Args:
        manager: 已注册数据源的 DataSourceManager 实例
        extra_fallbacks: 额外兜底来源（如 ["mootdx", "tencent"]），追加到所有链末尾
    """
    for indicator, sources in DEFAULT_CHAINS.items():
        chain = list(sources)
        if extra_fallbacks:
            chain.extend(extra_fallbacks)
        # 只保留 manager 中已注册的来源
        chain = [s for s in chain if manager.has_source(s)]
        if chain:
            manager.configure_chain(indicator, chain)
