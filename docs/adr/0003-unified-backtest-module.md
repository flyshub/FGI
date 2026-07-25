# ADR-0003: 回测模块整合 — signal_report.py 统一承载全部回测功能

**日期**：2026-07-25
**状态**：已采纳

## 背景

项目有三个回测相关模块：

| 模块 | 状态 | 问题 |
|------|------|------|
| `backtest.py` | 死代码 | FGI 自相关缺陷；零生产调用者 |
| `signal_report.py` | 生产使用 | 正确的市场收益 + 五区间前瞻分析 |
| `decision_matrix.py` | 生产使用 | 情绪×估值解读，不涉及历史回测 |

根因 `backtest.py` 的 `BacktestEngine` 用 `FGI_final.shift(-N)/FGI_final - 1` 当未来收益 — 衡量 FGI 自相关而非对市场收益的预测力。

## 决策

**扩展 `signal_report.py` 承载全部回测功能（IC 分析、策略模拟、分层回测），废弃 `backtest.py`。**

具体措施：
1. 在 `signal_report.py` 中新增 Rank IC 分析、策略模拟（DCA、极端择时）、分层回测
2. `backtest.py` 顶部加 `NotImplementedError` 标记废弃
3. `decision_matrix.py` 保持不变（它属于输出/解读层，不参与回测）

## 理由

1. `signal_report.py` 已有正确的数据管道（FGI + 收盘价合并 + 前瞻收益计算）
2. 它已在生产中使用（每日推送的历史信号参考卡片），扩展它立即增值
3. `BacktestEngine` 是死代码 — 零个生产调用者，修复它不服务任何人
4. 一个模块承载全部回测功能，降低认知负荷

## 备选方案

- **修复 backtest.py** — 被否决。该模块从未被调用，修复完仍是死代码。不如直接在 `signal_report.py` 正确实现。
- **保留两者各司其职** — 被否决。两个模块做类似事情且需要共享数据管道，增加维护成本。

## 影响

- `fgi/output/backtest.py` — 废弃
- `tests/unit/test_backtest.py` — 移除或迁移
- `fgi/output/signal_report.py` — 扩展，保持现有 API 兼容
- 现有生产调用不受影响

## 相关

- Spec V3.8 §5（回测与验证）
- [#79](https://github.com/flyshub/FGI/issues/79) FGI 历史信号有效性验证报告
- [#68](https://github.com/flyshub/FGI/issues/68) 补全回测验证体系
