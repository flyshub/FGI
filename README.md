# FGI — A股恐惧贪婪指数

![CI](https://github.com/flyshub/FGI/actions/workflows/ci.yml/badge.svg)

全自动 A 股市场情绪量化指数。每日从 5 大数据源采集 12 个指标，合成 0–100 的情绪读数，通过 PushPlus 推送至手机。

> 极度恐惧时买入，极度贪婪时卖出——前提是恐惧/贪婪信号真的有效。FGI 的目标是用数据验证这个假设，并给出可量化的决策辅助。

## 📖 简单了解

先快速了解一下 FGI 是什么，怎么算，怎么用：
- [**A股恐惧贪婪指数—大白话解读**](https://flyshub.github.io/FGI/A股恐惧贪婪指数__简单解释.html)

## 快速开始

```bash
# 1. 安装依赖（依赖声明只在 pyproject.toml 维护）
pip install -e ".[dev]"

# 2. 获取数据库基线（data/fgi.db 不再提交到仓库，约 14MB）
mkdir -p data && gh release download data-latest --pattern fgi.db --dir data --clobber

# 3. 配置推送（可选）
export FGI_PUSHPLUS_TOKEN=your_token_here      # 主推送
export FGI_PUSHPLUS_FRIENDS=tokenA,tokenB       # 好友令牌（逗号分隔，可选）

# 4. 运行
python -m fgi.output.daily_run          # 最近交易日
python -m fgi.output.daily_run --date 2026-07-24  # 指定日期
```

**为什么要下数据库**：`data/fgi.db` 含 2015 年至今的每日分数与 2005 年至今的原始数据，且**不可重建**——上游只提供有限窗口（腾讯约 640 天、akshare 涨停池约 30 天、北向资金 2024-08 后停更）。本地跑 `daily_run`、离线模式（`FGI_OFFLINE=1`）、回测都需要它。CI 在每次运行开始时从同一个 Release 资产恢复基线，成功跑完后覆盖回传。

## 指数构成

| 维度  | 权重  | 指标                                                    |
| --- | --- | ----------------------------------------------------- |
| 动量  | 20% | 涨停板家数 (M1) · 散户意愿 (M2) · 偏离60日均线 (M3) · 创业板成交活跃度 (M4) |
| 情绪  | 20% | 股吧热度 (S2) · 涨停封单量 (S3)                                |
| 估值  | 20% | 沪深300风险溢价 (V1) · ΔERP Z-score (V2)                    |
| 波动率 | 20% | 50ETF期权隐含波动率 QVIX (V4)                                |
| 资金  | 20% | 融资余额占比 (F1) · 基金股票仓位 (F2) · 主力资金板块偏好 (F3)             |

各指标经 5 年滚动百分位标准化 → 0–100 → 维度等权聚合 → 五维等权合成 → FGI。

## 推送内容

每日推送包含：

1. **FGI 头部** — 数值、情绪标签（极度恐惧/恐惧/中性/贪婪/极度贪婪）、趋势、健康度、历史位置
2. **🎯 情绪-估值决策矩阵** — FGI 情绪分位 × 沪深300 PE/PB 估值分位 → 3×3 象限解读
3. **📈 历史信号参考** — 当前 FGI 区间的 5/20/60 日前瞻上证综指平均涨跌和胜率。极端+趋势明确时附加**时间锚点**：上次同向接近此水平的日期及后市表现
4. **指标明细** — 12 个指标得分、数据来源日期、状态标注
5. **维度汇总** — 五维度得分和权重
6. **极端信号** — ≥85 或 ≤15 的指标
7. **最大变动** — 日环比变化最大的前 3 个指标

## 信号验证

运行 `python scripts/generate_signal_report.py` 生成完整回测报告（输出至 `reports/`）。

关键发现（上证综指基准）：

| 区间        | 天数   | 60 日胜率 | 60 日平均收益 |
| --------- | ---- | ------ | -------- |
| 极度恐惧（<20） | 7    | 71%    | +3.02%   |
| 恐惧（20-40） | 298  | 57%    | +2.72%   |
| 中性（40-60） | 1474 | 57%    | +1.27%   |
| 贪婪（60-80） | 729  | 46%    | -0.42%   |
| 极度贪婪（≥80） | 7   | 43%    | +2.17%   |

> 极端区间样本量极小（<30），统计推断不可靠。历史不代表未来。

报告包含 Rank IC 分析、10 档分层回测、逆情绪 DCA 模拟等。详见 [实施方案 V3.9](A股恐惧贪婪指数（FGI）实施方案%20·%20终稿%20V3.9.md)。

## 项目结构

```
fgi/
├── calculator/         # 12 个指标计算器（momentum/sentiment/valuation/funding）
├── collector/          # 数据源采集层（Fuyao/AKShare/ZZShare/Mootdx/Tencent + 自动降级）
├── storage/            # SQLite 存储（raw_data / scores_daily / daily_status）
├── output/             # 每日运行、推送、回填、回测、决策矩阵
├── common/             # 工具函数
└── config/             # 全局配置
```

## 数据架构

### 数据源链

```
指标 → DataSourceManager → FallbackChain
                           ├─ Fuyao（同花顺官方 API，m3/m4/f3 的首选源）
                           ├─ AKShare（其余指标的首选源，也是 m3/m4/f3 的兜底）
                           ├─ ZZShare
                           ├─ Mootdx（TCP，无 IP 封禁）
                           └─ Tencent（HTTP，无 IP 封禁）
```

每个指标有首选来源链。首源失败后按序降级。5 次连续失败后冷却 5 分钟，更多失败后冷却 1 小时。

m3 / m4 / f3 三条链走 **DB-first 拼接**：历史从 `raw_data` 读，数据源只补最近 30 天。原因是 Fuyao 只提供近 5 自然年（约 1211 根），整窗重取会把 1260 的滚动窗口截短（详见 ADR-0005 与 `AGENTS.md` 不变式 8）。

### 存储

SQLite（`data/fgi.db`），三张核心表：

| 表              | 用途             | 主键                  |
| -------------- | -------------- | ------------------- |
| `raw_data`     | 原始指标值（API 返回值） | `(date, indicator)` |
| `scores_daily` | 标准化得分 + FGI 合成 | `(date, indicator)` |
| `daily_status` | 每日各指标采集状态      | `(date, indicator)` |

写入幂等（`ON CONFLICT DO UPDATE`），多次运行安全。

## 数据源配置

通过环境变量控制数据源可用性。默认全部开启（值为 `true` / `false`）：

```bash
FGI_AKSHARE_ENABLED=false python -m fgi.output.daily_run   # 关闭 akshare
FGI_MOOTDX_ENABLED=false python -m fgi.output.daily_run    # 关闭 mootdx 兜底
FGI_TENCENT_ENABLED=false python -m fgi.output.daily_run   # 关闭腾讯兜底
```

zzshare 没有开关：以 `import zzshare` 的成败决定是否注册（M2、S2、S3 依赖它）。

Fuyao 没有 `FGI_*_ENABLED` 开关：由 `FUYAO_API_KEY` 是否存在决定。没有 Key 时它不注册，m3/m4/f3 自动退回 akshare，`daily_run` 不会因此失败。需要 Key 时才用官方 API（申请入口见 [fuyao.aicubes.cn](https://fuyao.aicubes.cn/admin/)）：

```bash
export FUYAO_API_KEY=sk-fuyao-...    # 不要写进仓库或 .env 后提交
```

支持离线重建模式（`FGI_OFFLINE=1`）：从数据库直接加载，无需网络。仅用于历史重算，不可用于生产推送。

## 回填与重算

```bash
python -m fgi.output.backfill         # 全指标历史回填
python fgi/output/zt_backfill.py      # 仅涨停数据
python scripts/recompute_v2.py        # 重算历史得分（可中断续算，向量化）
```

## GitHub Actions

项目通过 GitHub Actions 每个交易日自动计算 FGI、推送 PushPlus、回写数据库、更新 Web 前端。

- **触发**：交易日 18:00（北京时间）+ 21:30 兜底（当天已算过则跳过）+ 手动 `workflow_dispatch`
- **配置**：在 Settings → Secrets → Actions 添加以下 Secret（注意不带 `FGI_` 前缀——workflow 会映射为对应的环境变量）：
  - `PUSHPLUS_TOKEN` — 主推送地址（对应环境变量 `FGI_PUSHPLUS_TOKEN`）
  - `PUSHPLUS_FRIENDS` — 好友令牌（逗号分隔，可选；对应环境变量 `FGI_PUSHPLUS_FRIENDS`）
  - `FUYAO_API_KEY` — 同花顺 API Key（可选；缺失时 m3/m4/f3 退回 akshare，管道照常运行）
- **流水线**：
  1. `python -m fgi.output.daily_run` — 计算 FGI + PushPlus 推送
  2. `gh release upload data-latest data/fgi.db` — 数据库回传（**不进 git**，见不变式 1）
  3. `python -m fgi.output.web_export --full` — 导出 6 个 JSON 文件
  4. `git commit docs/data/` — Web 数据回写 → GitHub Pages 自动部署
- **产物**：每次运行后可在 Actions 页面下载 `fgi-results-<date>` artifact
- **失败处理**：指标崩溃 ≥3 个，或 ≥2 个指标从「此前 3 个有状态日期全 `normal`」掉成 `missing`/`error`（`fgi/output/health_gate.py` 的回归判定）时，不推送、`exit 1`，DB 与 Web 数据都不写回，并自动开 issue。单个指标缺失不拦——当日未发布、次日由 `backfill_late` 补齐属正常流程。
- **连通性检查**：`verify_sources.yml`（手动触发，`gh workflow run verify_sources.yml`）逐个指标打印实际命中的数据源，用来确认 Key 有效、链序正确。不写库、不推送；Key 轮换后跑一次。
- **M1 交叉校验**：`validate_m1.yml` 每周一 11:00（北京时间）自动跑，用全市场日 K 按交易所规则自行推导涨停家数，与 `raw_data.m1_zt_count` 比对（`fgi/analysis/limit_up.py`）。只读；偏差突然变大才开 issue。本地：`pip install -e ".[dump]"` 后 `python scripts/validate_m1.py --days 120`。已知系统性偏差（约 +2 家；2026-07-06 主板 ST 改 10% 后为 +8~13）属正常，判定看的是突变。详见 `docs/research/2026-11-22-limit-up-history-sources.md`。

## Web 前端

在线地址：**[https://flyshub.github.io/FGI/](https://flyshub.github.io/FGI/)**

纯静态页面，零构建步骤，通过 GitHub Pages 从 `main` 分支的 `docs/` 目录提供服务。

### 功能

| 区块          | 内容                                                |
| ----------- | ------------------------------------------------- |
| 日期选择器       | 查看任意历史交易日的详情，选日期后所有区块联动刷新                         |
| FGI 仪表盘     | 大数字 FGI + 情绪等级 + 趋势方向 + 涨跌变化 + 数据健康度 + 历史百分位      |
| 🎯 决策矩阵     | 3×3 情绪-估值象限图，当前象限高亮                               |
| 📈 历史信号参考   | 当前 FGI 区间的 5/20/60 日胜率和平均收益 + 时间锚点（明细表）           |
| 📊 FGI 历史走势 | 交互式折线图（FGI 左轴 + 上证综指右轴），底部滑块缩放，极端信号菱形标记，点击任一点切换日期 |
| 📐 五维度雷达图   | 动量/情绪/估值/波动率/资金 五维对比                              |
| 🔍 指标明细     | 12 指标条形图，85/15 阈值线，极端指标边框高亮                       |
| 📊 FGI 分布   | 20 桶直方图，当前日期橙色标记                                  |
| 📈 维度/指标趋势  | 下拉切换查看各维度/指标的历史走势                                 |
| 📊 信号报告     | Rank IC 分析 + 10 档分层回测 + 反向 DCA 策略对比               |
| ⚡ 极端信号标签    | ≥85（红）或 ≤15（绿）的指标名标注                              |

### 数据更新

与每日 FGI 计算在同一个 GitHub Actions workflow 中顺序执行——计算完成后自动导出 JSON → 提交 → Pages 部署。用户无感知，每日 18:00 后几分钟内页面自动刷新。GitHub 免费 cron 常延迟甚至漏跑，因此 21:30 还有一次兜底（`python -m fgi.output.check_ran` 判断当天是否已有分数，有则整趟跳过）。

### 本地预览

```bash
# 1. 生成 JSON 数据
python -m fgi.output.web_export --full

# 2. 启动本地 HTTP 服务器
cd docs && python -m http.server 8000

# 3. 浏览器打开 http://localhost:8000
```

### 导出脚本

```bash
python -m fgi.output.web_export                  # 最新日期
python -m fgi.output.web_export --date 2026-07-24  # 指定日期
python -m fgi.output.web_export --full              # 全量所有文件
```

输出文件：

| 文件                            | 内容                          | 大小     |
| ----------------------------- | --------------------------- | ------ |
| `fgi_all_dates.json`          | 2575 个交易日全量预计算数据，日期切换秒级响应   | ~5MB   |
| `fgi_latest.json`             | 当日最新数据（仪表盘/决策矩阵/锚点/极端信号）    | ~2KB   |
| `fgi_history.json`            | 全量历史 FGI + 上证综指收盘价          | ~320KB |
| `fgi_signal_report.json`      | 信号报告（区间统计/Rank IC/分层回测/DCA） | ~30KB  |
| `fgi_indicators_history.json` | 12 指标 + 收盘价全量时间序列           | ~1.9MB |
| `fgi_anchors_history.json`    | 每日锚点预计算                     | ~160KB |

## 测试

```bash
pytest tests/ -x -q
```

## 数据真实性说明

- 数据来自同花顺（Fuyao）/ AKShare / ZZShare / Mootdx / Tencent 等公开接口，爬虫类数据源可能因网站改版中断。m3 / m4 / f3 三条链已改用同花顺官方 API（ADR-0005），其余指标仍依赖爬虫类接口。
- F3（主力资金板块偏好）使用上证指数量价代理估算（真实 API 不可达）。V3.8.5 全量 recompute 后分布健康（mean≈49，范围 0–100），序列内百分位相对位置有效。
- F2（基金股票仓位）为周频数据，前向填充超过 7 天标记为 degraded。
- F1（融资余额占比）受上游数据 T+1 发布节奏影响，前向填充 >1 天标记为 degraded。

## License

MIT
