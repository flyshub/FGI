"""数据源装配的单一入口。

此前 daily_run 与 backfill 各有一份 setup_data_manager：注册的源不同、
是否追加兜底不同，行为漂移没有测试拦得住（P2-7）。现在只有这一份。
"""

from __future__ import annotations

import logging

from fgi.collector.akshare_source import AKShareSource
from fgi.collector.chains import configure_manager
from fgi.collector.fallback import DataSourceManager
from fgi.collector.mootdx_source import MootdxSource
from fgi.collector.tencent_source import TencentSource
from fgi.collector.zzshare_source import ZZShareSource
from fgi.config.settings import AKSHARE_ENABLED, MOOTDX_ENABLED, TENCENT_ENABLED

logger = logging.getLogger(__name__)


def _zzshare_available() -> bool:
    try:
        import zzshare  # noqa: F401
    except ImportError:
        return False
    return True


def _try_register(manager: DataSourceManager, name: str, factory) -> None:
    """可选数据源构造失败不该拖垮整条管道，但必须留下痕迹——
    否则这条源只是「消失了」，事后只能从某个指标长期 missing 反推。"""
    try:
        manager.register_source(name, factory())
    except Exception as exc:
        logger.warning("data source %r unavailable, continuing without it: %s", name, exc)


def setup_data_manager(extra_fallbacks: bool = True) -> DataSourceManager:
    """按配置装配生产数据源。

    extra_fallbacks:
        True  — 把 mootdx/tencent 追加到它们能服务的链（实际只有指数日线，见 registry）
        False — 不追加，供 backfill 使用：历史深度只信 akshare，兜底源窗口太短
    """
    manager = DataSourceManager()

    if AKSHARE_ENABLED:
        manager.register_source("akshare", AKShareSource())
    if MOOTDX_ENABLED:
        _try_register(manager, "mootdx", MootdxSource)
    if TENCENT_ENABLED:
        _try_register(manager, "tencent", TencentSource)
    if _zzshare_available():
        _try_register(manager, "zzshare", ZZShareSource)

    extra = None
    if extra_fallbacks:
        extra = [name for name in ("mootdx", "tencent") if manager.has_source(name)]

    configure_manager(manager, extra_fallbacks=extra)
    return manager
