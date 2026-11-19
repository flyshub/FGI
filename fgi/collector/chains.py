"""数据源链配置：指标 → 首选来源映射。

daily_run 和 backfill 共用此配置，按需追加兜底来源（如 mootdx/tencent）。
数据源能力以 fgi/indicators/registry.py:CHAIN_METHODS 为准：能服务该链的源才进链。
"""

import logging

from fgi.collector.fallback import DataSourceManager
from fgi.indicators.registry import CHAIN_METHODS

logger = logging.getLogger(__name__)

# 指标 → 首选来源列表（顺序即优先级）
# 注意：这里列出的源必须实现该链对应的方法（见 CHAIN_METHODS），
# 否则启动时会打 warning 并剔除——不要写进来看起来像兜底。
# zzshare 只能提供 M2/S2（open_sentiment / market_hot_sentiment）与日线，
# 无法提供涨停统计，因此 m1/s3 不列它。
DEFAULT_CHAINS: dict[str, list[str]] = {
    "m1_zt_stats": ["akshare"],
    "m2_market_overview": ["zzshare"],
    "m3_index": ["akshare"],
    "m4_cyb_volume": ["akshare"],
    "s2_sentiment": ["zzshare"],
    "s3_zt_daily": ["akshare"],
    "v1_pe": ["akshare"],
    "v1_bond": ["akshare"],
    "v1_pb": ["akshare"],
    "v4_qvix": ["akshare"],
    "f1_margin": ["akshare"],
    "f1_market_cap": ["akshare"],
    "f2_fund_position": ["akshare"],
    "f3_index": ["akshare"],
}


def _can_serve(manager: DataSourceManager, name: str, method: str | None) -> bool:
    return manager.has_source(name) and manager.can_serve(name, method)


def configure_manager(
    manager: DataSourceManager,
    extra_fallbacks: list[str] | None = None,
) -> None:
    """为 DataSourceManager 配置所有指标链。

    Args:
        manager: 已注册数据源的 DataSourceManager 实例
        extra_fallbacks: 额外兜底来源（如 ["mootdx", "tencent"]），
            只有真正实现了该链方法的源才会被追加
    """
    offenders: list[str] = []
    for indicator, sources in DEFAULT_CHAINS.items():
        method = CHAIN_METHODS.get(indicator)
        chain: list[str] = []

        # 首选来源：写错了要看得见
        for name in sources:
            if not manager.has_source(name):
                continue
            if not _can_serve(manager, name, method):
                # 首选源写错是配置错误，不是可恢复状态：报错让它不可写，
                # 而不是打一条容易被淹没的 warning 然后静默剔除。
                offenders.append(f"{indicator} ← {name} (no {method})")
                continue
            chain.append(name)

        # 兜底来源：本来就是「能用就用」，能力不足静默跳过
        for name in extra_fallbacks or []:
            if not manager.has_source(name) or not _can_serve(manager, name, method):
                continue
            if name not in chain:
                chain.append(name)

        if chain:
            manager.configure_chain(indicator, chain)

    if offenders:
        raise ValueError(
            "DEFAULT_CHAINS 声明了不存在的组合（源未实现该链的方法）：" + "; ".join(offenders)
        )
