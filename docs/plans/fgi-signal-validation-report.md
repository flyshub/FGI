# FGI 历史信号验证报告 — 实施计划

## Context

用户希望利用历史数据验证 FGI 信号的有效性，增强用户信任。具体做法是：统计 FGI 进入不同区间（<20 / 20-40 / 40-60 / 60-80 / >80）后一定时期内上证指数的平均涨跌幅和胜率，生成可发布的回测报告。

当前 `fgi/output/backtest.py` 存在根本性缺陷：用 FGI_final 预测 FGI_final 自身（自相关），而非预测市场收益。需要从零构建信号验证逻辑。

## 设计决策

### 基准选择：上证综指（已有数据，无需拉新数据）

- `raw_data` 表中已有 `m3_close`（上证指数收盘价，2805 行，2015-01-05 起）
- 直接复用，v1 不引入沪深 300
- 报告中注明基准为上证综指

### 模块策略：新建 `fgi/output/signal_report.py`

- 不改动现有的 `backtest.py`（它的问题太多，且用户这次的需求侧重信号验证报告而非全量回测引擎）
- 新模块职责单一：读 FGI + 基准价格 → 算各区间前瞻收益 → 生成 Markdown 报告

### 范围：聚焦核心价值，不做全套 spec 回测

- 用户明确要求"增强信心"的实用报告，不是合规性 spec 实现
- v1 只做 FGI 区间 × 前瞻窗口的收益分析
- 分层回测、IC 分析、策略模拟留到后续

## 报告内容

```
📊 FGI 历史信号有效性报告
├── 概览：数据范围、样本量、基准说明
├── 一、区间分布统计
│   └── 各 FGI 区间的交易日数/占比
├── 二、前瞻收益分析（核心）
│   ├── 5 日前瞻：各区间上证综指平均涨跌幅、中位数、胜率、t 统计量
│   ├── 20 日前瞻：同上
│   └── 60 日前瞻：同上
├── 三、极端信号专项
│   ├── FGI < 20（极度恐惧）后市场表现时间线
│   ├── FGI > 80（极度贪婪）后市场表现时间线
│   └── 极端信号触发日期列表
└── 四、结论与局限性
    ├── 信号有效性总结
    └── 数据与方法局限声明
```

## 技术方案

### 数据流

```
scores_daily (FGI_final)  ──┐
                             ├── merge on date ──→ zone assignment ──→ forward returns ──→ stats ──→ markdown
raw_data (m3_close)        ──┘
```

### 计算逻辑（signal_report.py）

1. `load_data()`: 从 DB 读取 FGI_final + m3_close，按日期合并
2. `assign_zone(fgi)`: `<20 / 20-40 / 40-60 / 60-80 / >80`
3. `compute_forward_returns(df, horizons=[5, 20, 60])`: 对每个日期，计算 N 日后上证综指涨跌幅
4. `zone_stats(df, horizon)`: 按区间分组，计算 count / mean / median / win_rate / t_statistic
5. `extreme_signals(df)`: 筛选 FGI<20 或 >80 的日期，列出触发日期和后续表现
6. `generate_report(stats, extremes)`: 渲染 Markdown

### 统计指标

| 指标 | 含义 |
|------|------|
| 触发次数 | 该区间出现的交易日数 |
| 平均涨跌幅 | 前瞻窗口内上证综指平均 % 变化 |
| 标准差 | 收益波动率 |
| 95% CI | 均值 ± 1.96 × std / √n（n≥30 时报告；极端区间 n<30 时标注"样本不足，不报告置信区间"） |
| 胜率 | 前瞻窗口内上涨的交易日占比 |

### 关键细节

- **前瞻收益计算**：`(close[t+N] - close[t]) / close[t]`，其中 close 来自 m3_close
- **重叠观测处理**：滚动窗口产生重叠样本，标注"观测非独立，显著性检验需谨慎解读"
- **日期处理**：只在 FGI_final 非 NULL 的日期计算信号
- **尾部数据**：最后 N 天不计算 N 日前瞻（无数据）
- **样本拆分**：样本内 2015-2022（参数观察期），样本外 2023-2026（纯验证期），分别报告
- **极端区间处理**：FGI<20 仅 8 天、FGI>80 仅 12 天，CI 仅对 n≥30 的区间计算
- **无新依赖**：pandas + numpy 即可，不需要 scipy/statsmodels（v1 用正态近似）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `fgi/output/signal_report.py` | **新建** | 信号验证报告主模块 |
| `scripts/generate_signal_report.py` | **新建** | CLI 入口脚本 |
| `reports/` | **新建目录** | 报告输出目录 |
| `reports/fgi_signal_validation_YYYY-MM-DD.md` | **生成** | 带日期的报告文件 |
| `tests/unit/test_signal_report.py` | **新建** | 单元测试 |

### 不改动的文件

- `fgi/output/backtest.py` — 保持不变，后续单独处理
- `fgi/collector/chains.py` — 不需要新数据链（复用 m3_close）
- `fgi/storage/database.py` — 现有接口足够

## 模块结构（signal_report.py）

```
class SignalReport:
    __init__(db: Database)
    load_data(start, end) -> pd.DataFrame
    assign_zones(df) -> pd.DataFrame
    compute_forward_returns(df, horizons) -> pd.DataFrame
    zone_summary(df) -> dict        # 各区间各窗口的统计表
    extreme_signals(df) -> dict     # 极端信号详情
    render_markdown(stats, extremes, metadata) -> str
    run(start, end, output_path) -> Path
```

## 验证方式

1. **单元测试**：用合成数据验证 zone 分桶、forward return 计算、统计指标
2. **集成测试**：用真实 DB 运行，确认报告可生成、无崩溃
3. **人工校验**：检查极端恐惧/贪婪日期的市场后续表现是否合理（如 2020-03-16 疫情底后的反弹、2016-01-04 熔断后的下跌）
4. **回归保护**：确保不改动现有 214 个测试

## 后续扩展（v2+，不在本次范围）

- 加入沪深 300 基准对比（需先 fetch + 存储）
- matplotlib 图表（FGI 走势 + 信号标注）
- Rank IC 分析
- 分层回测（10 分位层）
- 策略模拟（逆情绪定投、极端择时）
