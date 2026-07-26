# FGI Web 前端设计 — 交接文档（已实施）

## 项目背景

FGI（A股恐惧贪婪指数）是一个已完全清理完毕的 Python 项目。
- `fgi/storage/database.py` — SQLite DB（data/fgi.db），含 2808 个交易日的历史 FGI 数据
- 所有代码已清理，18 个 issues 已关闭，260 测试通过

## 在线地址

**https://flyshub.github.io/FGI/**

## 目标

搭建一个漂亮美观的 FGI 指数展示 Web 页面，部署到 GitHub Pages。

## 技术路线（已确定并实施）

- **后端**：GitHub Actions 每天用 Python 脚本将 SQLite 数据导出为 JSON 文件
- **前端**：纯静态 HTML + JS + ECharts CDN（jsDelivr）
- **部署**：GitHub Pages，从 `main` 分支的 `docs/` 目录提供服务
- **CSS**：手写 CSS（无 Tailwind 依赖）
- **颜色系统**：极度恐惧 `#d32f2f` → 恐惧 `#ef5350` → 中性 `#9e9e9e` → 贪婪 `#66bb6a` → 极度贪婪 `#2e7d32`

## 数据导出设计

从 `data/fgi.db` 导出以下 JSON 文件到 `docs/data/`：

### 文件清单

| 文件 | 内容 | 大小 | 更新频率 |
|------|------|------|---------|
| `fgi_all_dates.json` | 2575 个交易日全量预计算数据 | ~5.2MB | 全量刷新 |
| `fgi_latest.json` | 当日最新完整数据 | ~2KB | 每日 |
| `fgi_history.json` | 全量历史 FGI + 上证综指收盘价 | ~320KB | 全量刷新 |
| `fgi_signal_report.json` | 区间统计 + Rank IC + 10档分层回测 + DCA | ~30KB | 每周/手动 |
| `fgi_indicators_history.json` | 12 指标全量时间序列 | ~1.9MB | 全量刷新 |
| `fgi_anchors_history.json` | 每日锚点预计算 | ~160KB | 全量刷新 |

### fgi_latest.json 格式

```json
{
  "date": "2026-07-24",
  "fgi_final": 35.69,
  "fgi_raw": 35.69,
  "health_score": 100,
  "zone": "恐惧",
  "trend": "↓",
  "delta": -8.4,
  "prev_fgi": 44.09,
  "percentile": 7.7,
  "percentile_label": "低于历史上 92% 的日子（极低）",
  "extreme_note": "⚠️ 处于历史极低区间",
  "scores": { "M1": 28.4, "M2": 3.3, "M3": 3.7, "M4": 0.4, ... },
  "dimensions": { "momentum": 8.9, "sentiment": 8.2, ... },
  "statuses": { "M1": "normal", ... },
  "decision_matrix": { "sentiment_tier": "恐惧", "valuation_pct": 0.80, "valuation_tier": "高估", "quadrant": "观望", "advice": "..." },
  "extreme_signals": { "high": [["F2", "基金股票仓位", 97.2]], "low": [["M2", "散户意愿", 3.3]] },
  "zone_context": { "zone": "恐惧", "n": 556, "total": 2575, "horizons": { "5": { "mean": 0.0034, "win_rate": 0.52 }, ... } },
  "anchor": { "closest_date": "2019-01-30", "closest_fgi": 35.7, "forward_20d_return": 20.4, "detail_table": [...] },
  "generated_at": "2026-07-24T19:00:00"
}
```

## 前端页面

### 布局（垂直流式，移动优先）

```
┌─────────────────────────────────────┐
│  HEADER: A股恐贪指数 + 日期 + 健康度  │
├─────────────────────────────────────┤
│  📅 日期选择器 — 置顶，选后全部刷新    │
├─── FGI 仪表盘 ──────────────────────┤
│  大数字 + 情绪等级 + 趋势 + 健康度 + 百分位
├─── 🎯 决策矩阵 ─────────────────────┤
│  3×3 网格，当前象限高亮（黄色）        │
├─── 📈 历史信号 + 锚点 ──────────────┤
│  区间统计表 + 最接近日期 + 明细表 + 区间
├─── 📊 历史走势（双轴折线图） ─────────┤
│  FGI(左轴)+上证综指(右轴), 橙色竖线标记当前│
├─── 📐 雷达图 + 🔍 指标条形图 ────────┤
│  五维度 / 12指标 + 85/15阈值线       │
├─── 📊 分布直方图 + 📈 趋势切换 ─────┤
│  20桶分布 + 维度/指标下拉切换        │
├─── 📊 信号报告 ─────────────────────┤
│  Rank IC / 10档分层回测 / DCA       │
└─────────────────────────────────────┘
```

### JS 模块

| 文件 | 职责 |
|------|------|
| `main.js` | 数据加载、日期切换、防抖 |
| `charts.js` | 5 个 ECharts 实例（历史/雷达/指标/分布/趋势） |
| `components.js` | DOM 组件（仪表盘/矩阵/信号/锚点/极端标签/报告） |

### 关键交互

- 顶部日期选择器 → 所有区块联动刷新
- 折线图点击任一点 → 切换到该日期
- 折线图底部滑块 → 缩放时间范围
- 维度/指标趋势 → 下拉切换系列
- 所有图表响应窗口 resize
