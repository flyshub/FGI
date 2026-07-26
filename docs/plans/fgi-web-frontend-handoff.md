# FGI Web 前端设计 — 交接文档

## 项目背景

FGI（A股恐惧贪婪指数）是一个已完全清理完毕的 Python 项目。
- `fgi/storage/database.py` — SQLite DB（data/fgi.db），含 2808 个交易日的历史 FGI 数据
- 所有代码已清理，18 个 issues 已关闭，260 测试通过

## 目标

搭建一个漂亮美观的 FGI 指数展示 Web 页面，部署到 GitHub Pages。

## 技术路线（已确定）

- **后端**：GitHub Actions 每天用 Python 脚本将 SQLite 数据导出为 JSON 文件
- **前端**：纯静态 HTML + JS + 图表库
- **部署**：GitHub Pages（从 `docs/` 或 `gh-pages` 分支托管）

## 数据导出设计

需要从 `data/fgi.db` 导出以下 JSON 文件：

### 文件 1：`fgi_latest.json`（当日最新数据）
```json
{
  "date": "2026-07-24",
  "fgi_final": 35.69,
  "fgi_raw": 35.69,
  "health_score": 100,
  "zone": "恐惧",
  "trend": "↓",
  "delta": -8.4,
  "scores": {
    "M1": 28.4, "M2": 3.3, "M3": 3.7, "M4": 0.4,
    "S2": 3.5, "S3": 22.3,
    "V1": 77.2, "V2": 16.7, "V4": 53.3,
    "F1": 83.1, "F2": 97.2, "F3": 2.9
  },
  "dimensions": {
    "momentum": 8.9, "sentiment": 8.2,
    "valuation": 46.9, "volatility": 53.3, "funding": 61.1
  },
  "statuses": {
    "M1": "normal", "M2": "normal", ...
  },
  "decision_matrix": {
    "sentiment_tier": "恐惧",
    "valuation_pct": 0.80,
    "valuation_tier": "高估",
    "quadrant": "观望",
    "advice": "情绪悲观且估值偏高，建议观望"
  }
}
```

### 文件 2：`fgi_history.json`（全量历史数据，约 2800 行）
```json
[
  {"date": "2026-07-24", "FGI_final": 35.69, "FGI_raw": 35.69, "health_score": 100},
  ...
]
```

### 文件 3：`fgi_signal_report.json`（回测统计结果）
- 区间分布
- Rank IC 分析结果
- 10 档分层回测
- DCA 策略对比

## 建议前端内容

### 必须有的（推送消息中的所有内容）
1. **FGI 头部仪表盘** — 大数字 FGI 值 + 情绪等级 + 趋势方向 + 涨跌变化
2. **健康度** — 进度条/分数
3. **历史位置百分位** — "低于历史上 XX% 的日子"的仪表
4. **时间锚点** — 最接近的同向历史日期及后市表现（带明细表）
5. **决策矩阵** — 3x3 情绪×估值象限，当前位置高亮
6. **12 指标明细** — 各指标得分条形图 + 状态标志
7. **维度汇总** — 五维度得分雷达图
8. **历史信号参考** — 当前区间的 5/20/60 日胜率和收益
9. **极端信号** — 得分 ≥85 或 ≤15 的指标列表

### 扩展功能（从现有数据挖掘）
10. **全历史 FGI 折线图** — 可缩放的交互式时间序列，带恐惧/贪婪区间着色
11. **极端信号时间线** — FGI<20 或 FGI>80 的标记点
12. **FGI 分布直方图** — 历史分布和当前分位
13. **维度和指标的趋势图** — 可切换查看各维度/指标的历史走势
14. **日期选择器** — 查看任意历史日期的详情
15. **移动端自适应**

## 建议技术选型

| 层次 | 推荐 | 理由 |
|------|------|------|
| 框架 | 无（纯 HTML+JS） | GitHub Pages 无构建，尽量减少依赖 |
| 图表库 | ECharts | 支持交互缩放的折线图/雷达图/热力图，中文文档好 |
| CSS | Tailwind CDN 或 手写 | 静态页面不需要构建 Tailwind |
| CI | GitHub Actions 已有 workflow | 在 daily_update.yml 后追加导出 JSON 步骤 |

## 项目结构建议

```
FGI/
├── docs/                    # GitHub Pages 根目录
│   ├── index.html           # 主页面
│   ├── styles/              # CSS
│   ├── scripts/             # JS
│   └── data/                # 生成的 JSON 数据文件（定期更新）
├── scripts/
│   └── export_fgi_web_data.py  # 导出 JSON 的 Python 脚本
├── .github/workflows/
│   └── daily_update.yml     # 追加 JSON 导出+部署到 Pages
```

## 关键资源

- 数据：`fgi/storage/database.py` 是所有数据的入口
- 信号报告：`fgi/output/signal_report.py` 含 IC/分层回测/DCA 计算
- 决策矩阵：`fgi/output/decision_matrix.py` 含 3x3 象限逻辑
- 推送模板：`fgi/output/renderer.py` 可作为内容参考
- 时间锚点：`fgi/output/signal_report.py` 中的 `_find_closest_prior_fgi` 和 `render_zone_context_card`
