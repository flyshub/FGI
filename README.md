# FGI — A股恐惧贪婪指数

A-Share Fear & Greed Index (FGI)，全自动 A 股市场情绪量化指数。每日从多数据源采集 12 个指标，合成 0–100 的情绪读数，通过 PushPlus 推送至手机。

## 指数构成

| 维度 | 权重 | 指标 |
|------|------|------|
| 动量 | 20% | 涨停板家数 (M1) · 散户意愿 (M2) · 偏离60日均线 (M3) · 创业板成交活跃度 (M4) |
| 情绪 | 20% | 股吧热度 (S2) · 涨停封单量 (S3) |
| 估值 | 20% | 沪深300风险溢价 (V1) · ΔERP Z-score (V2) |
| 波动率 | 20% | 50ETF期权隐含波动率 QVIX (V4) |
| 资金 | 20% | 融资余额占比 (F1) · 基金股票仓位 (F2) · 主力资金板块偏好 (F3) |

各指标经 5 年滚动百分位标准化 → 0–100 → 维度等权聚合 → 五维等权合成 → FGI。

每日推送附带 **🎯 情绪-估值决策矩阵**（V3.8.9+）：FGI 情绪分位 × 沪深300 PE/PB 估值分位 → 3×3 象限解读（强烈关注/关注/中性/谨慎/强烈谨慎/观望），辅助判断"情绪极值是否与估值匹配"。决策矩阵为输出层解读，不影响 FGI 12 指标计算。

详情见 [实施方案 V3.8](A股恐惧贪婪指数（FGI）实施方案%20·%20终稿%20V3.8.md)。

## 快速开始

```bash
pip install -r requirements.txt

# 单次运行（最近交易日）
python -m fgi.output.daily_run

# 指定日期
python -m fgi.output.daily_run --date 2026-07-24
```

### 数据源配置

通过环境变量控制数据源可用性。默认全部开启：

```bash
# 若某些源不可用，设为 0 关闭
FGI_ZZSHARE=0 python -m fgi.output.daily_run    # 关闭 zzshare
FGI_MOOTDX=0 python -m fgi.output.daily_run     # 关闭 mootdx
FGI_TENCENT=0 python -m fgi.output.daily_run    # 关闭腾讯
```

### PushPlus 推送

设置环境变量或写入 `.env`：

```bash
FGI_PUSHPLUS_TOKEN=your_token_here
```

推送格式为 Markdown + HTML 混合模板，具体内容见下方"回测与信号验证"章节。

## 推送内容

每日推送包含以下模块：

1. **FGI 头部**：FGI 数值、情绪标签（极度恐惧/恐惧/中性/贪婪/极度贪婪）、趋势、健康度、历史位置
2. **🎯 情绪-估值决策矩阵**（V3.8.9+）：FGI 情绪分位 × 沪深300 PE/PB 估值分位 → 3×3 象限解读
3. **📈 历史信号参考**（V3.8.10+）：当前 FGI 所在区间的 5/20/60 日前瞻上证综指平均涨跌和胜率
4. **指标明细**：12 个指标得分、数据来源日期、状态标注
5. **维度汇总**：五维度得分和权重
6. **极端信号**：≥85 或 ≤15 的指标
7. **最大变动**：日环比变化最大的前 3 个指标

## 回测与信号验证

### 历史信号有效性报告

运行 `python scripts/generate_signal_report.py` 生成完整回测报告（输出至 `reports/`），包含：

- **区间分布**：FGI 五区间（<20 / 20-40 / 40-60 / 60-80 / ≥80）历史交易日数与占比
- **前瞻收益矩阵**：各区间 5/20/60 日后上证综指平均涨跌、胜率、95% 置信区间
- **样本内外对比**：样本内（2015-2022）vs 样本外（2023-2026）分段验证
- **极端信号时间线**：FGI<20 和 ≥80 的具体触发日期与后续表现

关键发现：
- 极度恐惧（FGI<20，仅 8 天）：60 日胜率 75%，平均 +5.14%
- 极度贪婪（FGI≥80，仅 12 天）：20 日胜率 25%，平均 -0.58%
- 恐惧区间（20-40，333 天）：60 日胜率 51.7%，信号偏弱
- 中性区间（40-60，1464 天）占 57%，各窗口胜率均接近 50%

> 基准为上证综指。极端区间样本量极小（<30），统计推断不可靠。历史不代表未来。

### 回测框架（backtest.py）

⚠️ `fgi/output/backtest.py` 存在已知缺陷：用 FGI_final 预测自身未来值（自相关），而非预测市场收益。暂不推荐使用。该模块计划在 spec 5.2-5.4 框架下重写。

### 与实施方案 V3.8 的回测差距

| spec 要求 | 状态 | 说明 |
|----------|------|------|
| 5.1 样本分割 | ✅ 已实现 | signal_report.py 自动拆分 2015-2022 / 2023-2026 |
| 5.2 分层回测（10 档，沪深300/中证500/中证1000 基准） | ❌ 未实现 | 当前仅 5 档分桶，仅上证综指基准 |
| 5.3 IC 分析（Rank IC, Bonferroni 校正） | ❌ 未实现 | backtest.py 仅做 Pearson IC 自相关 |
| 5.4 策略模拟（逆情绪定投、极端择时） | ❌ 未实现 | 当前策略模拟用 FGI 自相关 |
| 5.5 逐指标验证（剔除测试、方向验证） | ⚠️ 部分 | 极端事件方向性已验证，系统 IC 未做 |

## 项目结构

```
fgi/
├── calculator/         # 各指标计算器
│   ├── momentum/       # M1–M4
│   ├── sentiment/      # S2–S3
│   ├── valuation/      # V1–V2, V4
│   ├── funding/        # F1–F3
│   └── fgi.py          # FGI 合成 + 健康度
├── collector/          # 数据源采集层
│   ├── base.py             # DataSource 抽象基类
│   ├── akshare_source.py   # AKShare 数据源
│   ├── zzshare_source.py   # ZZShare 数据源
│   ├── mootdx_source.py    # Mootdx 数据源（TCP）
│   ├── tencent_source.py   # 腾讯数据源（HTTP）
│   ├── mock_source.py      # 测试用 Mock
│   ├── fallback.py         # FallbackChain 自动降级 + 离线重建
│   ├── chains.py           # 数据源链配置
│   └── trading_calendar.py # 交易日历
├── storage/
│   └── database.py     # SQLite 存储（raw_data / scores_daily / daily_status）
├── output/
│   ├── daily_run.py         # 每日运行入口
│   ├── pushplus.py          # PushPlus 推送模板
│   ├── alert.py             # 异常检测与告警
│   ├── status.py            # 状态记录辅助
│   ├── backfill.py          # 历史回填
│   ├── backtest.py          # [PENDING] 极端事件回测框架 (broken — 仍在用 旧设计)
│   ├── signal_report.py     # 历史信号有效性验证 + 推送卡片
│   ├── decision_matrix.py   # 情绪-估值 3×3 决策矩阵
│   └── zt_backfill.py       # 涨停数据专用回填
├── common/
│   └── utils.py        # 工具函数（rolling_percentile 等）
└── config/
    └── settings.py     # 全局配置
```

## 数据存储

SQLite（`data/fgi.db`），三张核心表：

| 表 | 用途 | 主键 |
|----|------|------|
| `raw_data` | 原始指标值（API 返回值） | `(date, indicator)` |
| `scores_daily` | 标准化得分 + FGI 合成 | `(date, indicator)` |
| `daily_status` | 每日各指标采集状态 | `(date, indicator)` |

写入幂等（`ON CONFLICT DO UPDATE`），多次运行安全。

## 回填与重算

```bash
# 全指标历史回填
python -m fgi.output.backfill

# 仅涨停数据
python fgi/output/zt_backfill.py

# 重算历史得分（含 health_score 两阶段）
python scripts/recompute_scores.py

# 断点续算
python scripts/recompute_scores.py --resume

# 向量化加速版（推荐大范围重算）
python scripts/recompute_v2.py
```

## 数据源架构

```
指标 → DEFAULT_CHAINS → DataSourceManager → FallbackChain
                                               ├─ AKShare（主）
                                               ├─ ZZShare
                                               ├─ Mootdx（TCP，无 IP 封禁）
                                               └─ Tencent（HTTP，无 IP 封禁）
```

每个指标有首选来源链。首源失败后按序降级。5 次连续失败后冷却 5 分钟，更多失败后冷却 1 小时。

支持离线重建模式（`FGI_OFFLINE=1`）：从 `raw_data` 数据库直接加载，无需网络。⚠️ 仅用于 `scripts/recompute_scores.py` 历史重算，不可用于 `daily_run` 生产推送（会跳过实时数据采集，导致 FGI 基于陈旧数据）。

## GitHub Actions 自动运行

项目通过 GitHub Actions 每个交易日自动计算 FGI、推送 PushPlus、回写数据库。

### 工作流配置

`.github/workflows/daily_update.yml`：

- **触发**：交易日 19:00（北京时间，`0 11 * * 1-5` UTC）+ 手动 `workflow_dispatch`
- **Python**：3.12
- **步骤**：安装依赖 → 运行 `python -m fgi.output.daily_run --date <date>` → 上传 `data/fgi.db` + `output/` 为构建产物 → schedule 触发时把 `data/fgi.db` commit 回 main
- **手动触发**：仓库 Actions 页面点击 "Run workflow"（可传 `--date` 参数；手动触发不会 commit DB，避免误污染 main）

### 配置步骤

1. 在 GitHub 仓库 → **Settings → Secrets and variables → Actions** 添加：
   - `PUSHPLUS_TOKEN`：你的 PushPlus 推送令牌（workflow 会把它映射到代码读取的 `FGI_PUSHPLUS_TOKEN` 环境变量）

2. 工作流会在每个交易日晚 7 点自动运行并推送手机；schedule 触发的运行还会把当日更新的 `data/fgi.db` commit 回 main 分支（作者 `github-actions[bot]`，带 `[skip ci]` 避免递归）。

3. 如需调试，每次运行后可在 Actions 页面下载 `fgi-results-<date>` artifact（含完整 `data/fgi.db` + 输出文件，保留 90 天）。

### 本地测试 CI

```bash
# 模拟 CI 环境运行（仅当前日期）
python -m fgi.output.daily_run
```

## 测试

```bash
pytest tests/ -x -q
```

## 数据真实性说明

- 数据来自 AKShare / ZZShare / Mootdx / Tencent 等免费公开接口，爬虫类数据源（zzshare、levistock）可能因网站改版中断。
- F3（主力资金板块偏好）因东财 API 间歇性不可达，全量使用上证指数量价代理估算（`price_change × volume`）。V3.8.5 全量 recompute 后分布实测健康（mean≈49，范围 0–100），不存在系统性偏差。序列内百分位相对位置有效。
- F2（基金股票仓位）为周频数据，前向填充超过 7 天标记为 degraded。
- F1（融资余额占比）受上游数据 T+1 发布节奏影响，前向填充 >1 天标记为 degraded。
