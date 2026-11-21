# ADR-0005: 同花顺（Fuyao）作为 m3/m4/f3 的主数据源

**日期：** 2026-11-22
**状态：** 已接受
**关联：** 探针脚本 `scripts/probe_fuyao.py`、`scripts/probe_fuyao2.py`（一次性，未提交）

## 背景

`akshare` 是爬虫式接口，`AGENTS.md` 已明确它依赖 11 个端点且「上游任一字段改名都会让无人值守的每日管道静默降级」。同花顺官方数据服务（`https://fuyao.aicubes.cn`，仓库 `Hithink-Tech-Org/Financial-API`）提供正式 REST API，需要评估能否替代。

2026-11-22 用真实 Key 做了两轮探针，实测结论如下（全部为本机实测，非文档推断）：

| 项目 | 实测结果 |
|---|---|
| 权限 | 6/6 capability 返回 `code=0`，无 `2003`（未开通） |
| 速率 | `X-RateLimit-Limit: 15`（15 req/s），无累计调用限额字段 |
| 费用 | 公开渠道（官网、`llms.txt`）无定价信息，账号侧未确认 |
| 数值一致性 | 2026-11-19 上证：Fuyao close 3577.62 / volume 41,291,276,000；库内 `m3_close` 3577.617 / `f3_proxy_volume` 41,291,275,500。相对误差 1.2e-6，**同单位（股），无需换算** |
| 指数历史深度 | **1211 根**，硬上限「近 5 自然年」，更早的 start 返回 `code=1003` |
| 涨停池深度 | **约 416 自然日 ≈ 297 个交易日**，更早返回 `code=1002` |
| 全市场 dump | `daily-k` 182.9 MB / `daily-k-10d` 1.05 MB / `adjustment-factors` 300 KB，均为合法 Parquet（`PAR1`） |

用库内真实序列做「截断到 Fuyao 可用深度」实验，量化分数偏差（近 30 个交易日，窗口 1260）：

| 指标 | 截断后长度 | 平均 \|Δ\| | 最大 \|Δ\| |
|---|---|---|---|
| F3 | 1211 | **0.22 分** | 0.53 分 |
| M3 | 1211 | **0.86 分** | 1.38 分 |
| M4 | 1211 | **1.46 分** | 2.22 分 |
| M1 | 297 | 12.21 分 | 20.64 分 |
| S3 | 297 | 34.95 分 | 48.23 分 |

同时检查 `daily_status` 全史，评估「兜底」的实际价值：

- m3 的 251 天非 normal **全部**是 2015-01-05 ~ 2016-01-12 的预热期（`Insufficient data`，不足 252 根），另加 1 次 forward_fill（2026-07-23）。
- f3 最近一次非 normal：2016-01-13；m4 最近一次非 normal：2015-12-16。
- 近 73 个交易日，m3/m4/f3 均为 73/73 normal。

即：**这三条链十年内从未因取数失败而缺失**，「兜底」在历史上一次都不会被触发；而且若上游改字段但不抛错（返回空表/错列），`FallbackChain` 可能判定成功，兜底永不触发——兜底无法覆盖最担心的失效模式。

## 决策

1. **Fuyao 作为 m3_index / m4_cyb_volume / f3_index 三条链的首选源**，akshare 降为兜底。
2. **历史深度问题由计算器的 DB-first 拼接解决**：m3/f3 改为先从 `raw_data` 读历史、只用 Fuyao 补最近 30 天，再在内存中拼接。m4 本来就是 DB-first。这样既拿到 Fuyao 的稳定性，又保留库内 2015 年至今的完整历史，**不引入窗口截断偏差**（这是 1211 < 1260 造成 0.86 / 1.46 分的唯一原因）。
3. **m1 / s3 不接入**：涨停池只有 297 个交易日，S3 会偏 35 分，M1 偏 12 分，远超可接受范围。
4. **v1 / v2 / s2 / v4 / f1 / f2 不接入**：无对应端点（估值仅最新快照无历史、无国债收益率、无两融、无 QVIX、无股吧热度）。
5. **backfill 不启用 Fuyao**：回填历史必须走 akshare，Fuyao 窗口太短（沿用 `extra_fallbacks=False` 的既有语义）。

## 与 ADR-0004 的关系

ADR-0004 规定「数据源口径变更时必须回填历史 `raw_data`，不允许新旧口径混合」。本决策**不构成口径变更**，因此不回填：

- **口径未变**：m3 仍是 `(close − MA60) / MA60`，f3 仍是 `price_change × volume`，成交量单位仍是「股」。
- **数值可交换**：2026-11-19 实测 close / volume 相对误差 1.2e-6，远小于百分位分辨率。
- **历史未被替换**：DB-first 让历史 `raw_data` 保持原样，Fuyao 只写最近 30 天。
- **实测残差量级不同**：M3 +0.008、F3 −0.159 分，来自库内序列与 akshare 实时序列的取值差异；ADR-0004 处理的是「分母/字段/单位变了」这类结构性变更（当时是 +2.57 分）。

若将来 Fuyao 与 akshare 出现结构性分歧（例如复权方式不同），回到 ADR-0004：先回填，再切换。

## 约束

1. **`FUYAO_API_KEY` 只从环境变量读取**，不写入仓库、`.env`、日志或异常消息。Key 缺失时 Fuyao 不注册，链路自动退回 akshare，`daily_run` 不因此失败。
2. **起始日必须钳到 5 年边界**，否则 `code=1003`。钳位逻辑属于适配器职责，不暴露给计算器。
3. **成交量单位不得换算**：Fuyao 与 akshare 均为「股」，实测误差 1e-6。任何「除以 100」的转换都会把 F3 的 `close × volume` 打偏 100 倍。
4. **历史回填不得用 Fuyao**：`fgi/output/backfill.py` 走 `setup_data_manager(extra_fallbacks=False)`，链里只有 akshare。`fgi/output/backfill_late.py` 只重算最近 3~5 个交易日的降级指标，允许使用 Fuyao——它同样走 DB-first，历史仍来自 `raw_data`。
5. `code != 0` 一律按失败处理并进入 `FallbackChain` 冷却，不做静默兜底。

## 后果

- ✅ m3/m4/f3 的取数从「爬虫」变为「官方 API」，消除上游改字段的脆弱性。
- ✅ 不引入窗口截断偏差：历史来自自有 DB，Fuyao 只补当日增量。窗口内实测 1313 个交易日 ≥ 1260，滚动窗口完整。
- ✅ 不新增依赖（直接 `requests`，不引入其 Python SDK / DuckDB / `marketdb`，SDK 还要求 Python 3.11+，与本项目 3.10 下限冲突）。
- ❌ 引入一个外部 Key 依赖，CI 需要配置 `secrets.FUYAO_API_KEY`。
- ❌ 费用未确认：官方文档写「不限制累计调用次数」，但没有公开定价，需账号侧确认。
- ❌ Fuyao 若某日数据滞后，DB-first 会取不到当日值并记 `missing`——这是可见失败，符合项目「不许 fail-open」原则。

## 验证（2026-11-22）

| 检查 | 结果 |
|---|---|
| 单元测试 | `pytest tests/` 402 passed |
| 静态门禁 | `pre-commit run`：ruff / ruff-format / mypy / DB 连接守卫 全通过 |
| 装配（无 Key） | m3/f3/m4 链 = akshare → mootdx → tencent，Fuyao 不注册 |
| 装配（有 Key） | m3/f3/m4 链 = **Fuyao** → akshare → mootdx → tencent |
| 装配（backfill） | m3/f3/m4 链 = 仅 akshare |
| 离线重算 2026-11-19 | 空链（任何取数都会 KeyError）下 m3/f3 均返回 normal，证明 DB-first 未触网；窗口 1313 行 |
| 与库内旧分数对比 | M3 +0.008、F3 −0.159（旧分数由 akshare 实时序列算出，差异来自序列取值而非窗口长度） |

另附 `scripts/verify_sources.py` + `.github/workflows/verify_sources.yml`：手动触发的连通性检查，Key 轮换后跑一次，不写库、不推送、不改历史。它不在决策范围内，是为「Secret 配置正确」这件事提供可复现证据而加的运维工具。
