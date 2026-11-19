"""FGI 历史信号验证（原 fgi/output/signal_report.py，932 行拆为四个模块）。

对外 API 保持不变：调用方仍写 `from fgi.output.signal_report import X`。

- analysis  : 区间定义、前瞻收益、逐区间统计、SignalReportEngine
- render    : Markdown 报告与各分节渲染
- backtest  : Rank IC / 分层回测 / DCA
- card      : 推送卡片（含少量 DB 查询）
"""

from fgi.output.signal_report.analysis import (
    HORIZONS,
    SAMPLE_SPLIT_YEAR,
    ZONES,
    SignalReportEngine,
    assign_zone,
    compute_forward_returns,
    compute_zone_stats,
    get_zone_for_fgi,
)
from fgi.output.signal_report.backtest import (
    compute_rank_ic,
    compute_rolling_ic_window,
    layer_backtest_10,
    simulate_dca,
)
from fgi.output.signal_report.card import (
    _find_closest_prior_fgi,
    _get_forward_return,
    render_zone_context_card,
)
from fgi.output.signal_report.render import (
    _render_dca_section,
    _render_ic_section,
    _render_layer_section,
    render_markdown,
)

__all__ = [
    "HORIZONS",
    "SAMPLE_SPLIT_YEAR",
    "ZONES",
    "SignalReportEngine",
    # 以下三个下划线函数被 scripts/generate_signal_report.py 直接使用
    "_render_dca_section",
    "_render_ic_section",
    "_render_layer_section",
    # 以下两个下划线函数被 fgi/output/web_export.py 直接使用
    "_find_closest_prior_fgi",
    "_get_forward_return",
    "assign_zone",
    "compute_forward_returns",
    "compute_rank_ic",
    "compute_rolling_ic_window",
    "compute_zone_stats",
    "get_zone_for_fgi",
    "layer_backtest_10",
    "render_markdown",
    "render_zone_context_card",
    "simulate_dca",
]
