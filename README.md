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
FGI_OFFLINE=1 python -m fgi.output.daily_run    # 完全离线（从数据库重建）
```

### PushPlus 推送

设置环境变量或写入 `.env`：

```bash
FGI_PUSHPLUS_TOKEN=your_token_here
```

推送格式为 Markdown + HTML 混合模板，含 FGI 值、趋势、维度明细表、极端信号、当日总结。

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
│   ├── daily_run.py    # 每日运行入口
│   ├── pushplus.py     # PushPlus 推送模板
│   ├── alert.py        # 异常检测与告警
│   ├── status.py       # 状态记录辅助
│   ├── backfill.py     # 历史回填
│   ├── backtest.py     # 极端事件回测
│   └── zt_backfill.py  # 涨停数据专用回填
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

支持离线重建模式（`FGI_OFFLINE=1`）：从 `raw_data` 数据库直接加载，无需网络。

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
- F3（主力资金板块偏好）因东财 API 间歇性不可达，全量使用上证指数量价代理估算，系统性偏高约 24 分。序列内百分位相对位置有效。
- F2（基金股票仓位）为周频数据，前向填充超过 7 天标记为 degraded。
- F1（融资余额占比）受上游数据 T+1 发布节奏影响，前向填充 >1 天标记为 degraded。
