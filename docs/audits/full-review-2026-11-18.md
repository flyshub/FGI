# FGI 项目完整评审与改进方案

**日期**：2026-11-18
**范围**：架构深度、数据管道正确性、测试与 CI、文档与仓库卫生、前端展示层（五线并行 + 交叉核对）
**基线**：`main` @ `98342a6`（408 个提交 / 120 天；`fgi/` 6558 行，`tests/` 4144 行 / 23 个文件）

## 总体判断

指标计算本身是扎实的：12 个指标公式与 V3.9 规格逐项对齐（`fgi/calculator/fgi.py:33-50` 的权重、`renderer.py:68-73` 的 20/40/60/80 分级、静态 S2=0.75/S3=0.25），测试覆盖了核心计算与信号报告。

真正的风险只有一个主题：**失败不可见**。日历缺失、指标抛错、上游返回旧数据、推送失败、CI 步骤失败——任一环节出问题，系统默认继续输出一个看起来正常的数字，并把它以「全绿」状态推给读者。

## 分级标准

| 级别 | 含义 |
|---|---|
| P0 | 会产生错数据、或让告警链在最需要时失效 |
| P1 | 会让环境装不起来、仓库失控、类型门禁空转、文档误导操作 |
| P2 | 架构摩擦，增加每次改动的成本（需先 grill 再动手） |
| P3 | 用户体验与信息传达 |

## 与历史审计的关系（已闭环项）

- `database.py` 已加 `__enter__/__exit__/rollback`（L155-169），OCR 报告 §2.1 已修复。
- `docs/scripts/charts.js` 已有 `_disposeIfExists()` 并在 5 处图表初始化前调用（L6-9, 18, 167, 189, 255, 298），OCR 报告 §6.1 已修复。
- M2/V1 的 offline raw_key 已补齐；M4 已进入 mapping（`fallback.py:32`）。data-accuracy-audit 记录的映射缺口大部分闭环。
- 仍存在：全项目 **57 处 `except Exception`**（其中 `fgi/output/` 与 `fgi/collector/` 占多数）。

---

# P0 — 正确性（6 项）

## P0-1 CI 失败仍把数据库提交到 main

**证据**：`.github/workflows/daily_update.yml:63,80` 两次 `daily_run` 都是 `continue-on-error: true`；`daily_run` 步骤在 `:99`（Commit DB）与 `:126`（Commit web data）之前；失败判定 `check_result` 在 `:136-149`。提交步骤只看 `skip != 'true'`，不看运行结果。随后 `[skip ci]` 让这笔提交绕过所有检查。

**后果**：半截或失败的 `data/fgi.db` 直接进 main，且没有任何 CI 锚点。这是当前最高风险的单点。

**方案**：
1. 把两个 Commit 步骤移到 `check_result` 之后，条件加 `steps.check_result.outputs.failed == 'false'`。
2. 失败时改为上传 artifact（`actions/upload-artifact`），保留取证，不污染 main。
3. 可选：失败时提交到 `data-failed/<date>` 分支，供人工比对。

**验证**：`workflow_dispatch` 手跑一次，临时禁用全部数据源使 daily_run 失败，确认「无提交 + 有 artifact + 有 issue」。

**风险**：失败当天数据不落库，次日需补跑（`backfill_late_data.py` 已覆盖 degraded/missing 补数）。

## P0-2 交易日历缺失时静默回退到 weekday，昨日数据被当成今日

**证据**：`daily_run.py:40-46`（日历为 None → `dt.weekday() < 5`）；`fgi/calculator/fgi.py:156-157`（目标日不在交易日集合 → `elapsed += 1`）；`fgi.py:162`（`status = "normal" if elapsed == 1`）。

**后果**：节假日（工作日历上的非交易日，如国庆）照常执行；所有指标 `today.empty → missing`；forward-fill 沿用上一交易日分数；`elapsed` 恰为 1 被标 `normal`；health_score 按满额计。最终「昨日 FGI + 全绿」被推送为「今日」。

**方案**：
1. 日历不可用 → 打 error 日志并 `exit 1`，交给 cron_health 建 issue；删除 weekday 兜底。
2. forward-fill 的状态改为 `forward_fill`（不要复用 `normal`），并在 `record_indicator_status` 的说明里写清来源日期。
3. 推送卡片顶部显示「数据日期 YYYY-MM-DD」，非当日时高亮。

**验证**：
- 单测：mock `TradingCalendar.load()` 返回 None → 断言退出码 1 且未调用推送。
- 单测：构造 `elapsed == 1` 的 forward-fill → 断言 status 不为 `normal`。

**风险**：日历源偶发失败会少跑一天。缓解：磁盘缓存 + workflow 失败重试一次。

## P0-3 指标异常被吞成 `score=None`，FGI 照常出数

**证据**：`fgi.py:80-89`（`except Exception → score=None, status="error"`）；`fgi.py:96-106`（维度内缺指标后重加权）；`fallback.py:115-117`（数据源级异常不进日志，只进 `result.error`）。

**后果**：一个指标崩溃时 FGI 仍输出，只是少一维或维度内重加权；日志一条 warning；无 issue、无推送标注。读者无法知道这个数字由 11 个还是 6 个指标得出。

**方案**：
1. `run_all_indicators` 汇总 `error/degraded/missing` 计数并返回。
2. 推送标题/卡片显示「n/12 指标正常」，异常指标列名。
3. 当 `error` 数 ≥ 阈值（建议 3）时抑制推送并走 alert 通道。
4. 数据源级异常写 `logger.warning("source=%s method=%s: %s")`。

**验证**：单测 mock 3 个指标抛异常 → 断言推送被抑制且卡片含计数。

**风险**：阈值过严会误抑制；建议先只标注、不抑制，观察两周后再收紧。

## P0-4 V4 只判断「今天这行存在」，不校验值

**证据**：`v4.py:53` `today_in_db = not db_data.empty and date in db_data["date"].values`。对比：`m1.py:71`、`m4.py:77`、`s3.py:88` 都已加 `pd.notna(...) and > 0` 守卫（注释明确写了原因）。

**后果**：QVIX 当日写入 0 或 NULL 时短路，不回源，直接进计算。相同模式在 `f2.py` 是特例处理（周频），需单独确认。

**方案**：v4 补与 m1/m4/s3 相同的非空守卫；另在各 calculator 写入前加「上游 `df.date.max() == target_date`」校验，不等则标 `stale` 而非静默写入。

**验证**：单测 v4 返回 0/NaN 行时不短路。

**风险**：QVIX 在某些日期可能确实为 0，需先确认口径再定阈值。

## P0-5 推送失败退出码仍为 0

**证据**：`daily_run.py:140-142` 打印 `PushPlus: skipped`；`pushplus.py:172-175` 捕获后只 `logger.error`。

**后果**：`cron_health.yml` 只检查 workflow 是否成功，推送连续失败不会产生 issue。告警链断在最后一公里。

**方案**：`send_fgi_report` 返回 False 时 `sys.exit(1)`（`--first-pass` 除外）；同时把 push 结果写入 `daily_status`（新增 `push` 行或独立表）。

**验证**：单测断言退出码；故意置空 token 跑一次。

## P0-6 前端把「无数据」画成 0 分

**证据**：`docs/scripts/charts.js:172-173` `dimensions[k] || 0`；`fgi_latest.json` 中 valuation/volatility 可能为 null。

**后果**：雷达图把缺失维度画成「极低/极度恐惧」，直接误导判断——这是全站最严重的单点数据失真。

**方案**：保留 `null`（ECharts radar 断开），轴标签显示「—」，tooltip 写「暂无数据」。构造一份 `valuation: null` 的 JSON 实测渲染效果后再定稿。

**验证**：浏览器打开页面，对比 null 与 0 的渲染差异。

---

# P1 — 环境、仓库与门禁（9 项）

## P1-1 仓库体积失控：`.git` 249MB，数据文件占绝大多数

**实测**：`du -sh .git` = 249M；`git count-objects -vH` → 松散对象 167.38 MiB、pack 64.66 MiB（6 个 pack）、**garbage 16.34 MiB（2 个 `tmp_pack_*` 残留）**。被跟踪的大文件：`data/fgi.db`（138 个版本）、`docs/audits/67378893-*.jsonl`（10.3 MB）、`docs/screenshots/*.png`（11 张 / 4.5 MB）、`reports/*.md`、`uv.lock`（727 KB）。

矛盾点：`.gitignore:40` 写了 `*.db`，但 `data/fgi.db` 仍在跟踪；ADR-0004 又声明 raw/scores 可完全重建。

**方案（分两步，第二步需决策）**：

立即（零风险，约 10 分钟）：
```bash
git rm --cached docs/audits/67378893-05d7-4056-bba7-85c45e075881.jsonl
git rm --cached reports/*.md          # 或移入 docs/audits/
# .gitignore 追加：.mypy_cache/ .pytest_cache/ .ruff_cache/ *.jsonl
git gc --prune=now --aggressive       # 回收 16.34MB garbage + 松散对象
```

中期（需你拍板）：`data/fgi.db` 出库。注意 CI 是 fresh clone，**仓库里的 db 就是基线**；出库必须同时改 `daily_update.yml`（改为 artifact / Release 附件 / 独立数据仓），不是单纯 `git rm`。

**验证**：`du -sh .git` 前后对比；`git ls-files` 确认移除项；CI 全绿。

**风险**：`--aggressive` 耗时较长（当前仓库规模下预计数分钟）；历史路径上的旧大文件仍留在 pack 里，彻底瘦身需 `git filter-repo`。

## P1-2 `requires-python >=3.9` 与代码实际不符

**证据**：`pyproject.toml:9` 声明 `>=3.9`；但 `fgi/collector/trading_calendar.py:13` 用 `Path | None` 且文件内无 `from __future__ import annotations`（3.9 下 import 即 `TypeError`）；`repositories.py:95`、`fallback.py:173` 使用 `zip(..., strict=)`（3.10+）。同时 `pyproject.toml:43` mypy target 3.12、`:52` ruff target py312，CI 只测 3.12（`ci.yml:16,31,52`）。

**后果**：3.9 用户 `pip install` 成功、运行即崩；mypy 以 3.12 检查永远发现不了。

**方案**：`requires-python` 改为 `>=3.10`（建议 `>=3.11`，与 CI 一致），mypy/ruff target 同步降为 py310 或统一声明 py312 + CI 加 matrix。

**验证**：`python3.10 -c "import fgi.collector.trading_calendar"`；或在 CI 加 3.10 job。

## P1-3 依赖三处声明漂移，运行时依赖缺失

**证据**：`pyproject.toml:9-18` 缺 `levistock`、`zzshare`、`python-dotenv`；`requirements.txt:1-3` 有；版本下界不一致（akshare 1.5 vs 1.12，scipy 1.10 vs 1.11）；`uv.lock`（727 KB，已跟踪）无任何 workflow 使用；CI 用 `pip install -e ".[dev]"`（`ci.yml:57`），daily_update 用 `pip install -r requirements.txt`（`daily_update.yml:32`）——两套环境。

**后果**：`pip install -e .` 后 `python -m fgi.output.daily_run` 直接 ImportError；两个 CI 装出不同环境；akshare 无 pin，上游字段改名即断管道（`test_akshare_source.py` 全用假模块，测不到真实漂移）。

**方案**：
1. pyproject dependencies 补 `levistock`、`zzshare`、`python-dotenv`。
2. `requirements.txt` 改为 `-e .[dev]`（单一来源），或直接删除并改 README。
3. akshare pin 到 `~=1.18.75`（与 uv.lock 对齐），daily_update 改 `uv sync --frozen`。
4. 可选：每周 advisory smoke job 真实拉一次上游。

**验证**：全新 venv → `pip install -e ".[dev]"` → `python -m fgi.output.daily_run --help`。

## P1-4 mypy 门禁近乎空转

**证据**：`pyproject.toml:48` 禁用 `arg-type`、`union-attr`、`attr-defined` 等 7 类；`:44` `warn_return_any=false`；`:46` `ignore_missing_imports`；`:47` `no_implicit_optional=false`。三者叠加后 mypy 实际只查名字/语法错误。典型：`fallback.py:136` `self._db = None` 无注解。

**方案（分三步，每步一个提交、CI 必须绿）**：
1. 开 `attr-defined` — 先给 `fallback._db` 等加注解。
2. 开 `union-attr` — 处理 Optional 解引用。
3. 开 `arg-type` — 需先给 `database.py` 公共方法补注解（工作量最大）。
   过渡期用 per-module override 逐目录豁免。

**验证**：`mypy fgi/` 错误数逐提交下降；typecheck job 变红即为有效拦截。

## P1-5 README 的环境变量与调度时间是错的

**证据**：`README.md:114-116` 写 `FGI_ZZSHARE=0` / `FGI_MOOTDX=0` / `FGI_TENCENT=0` —— 全仓 grep 只命中 README 这三行，代码零实现；真实开关是 `settings.py:10-12` 的 `FGI_AKSHARE_ENABLED/FGI_MOOTDX_ENABLED/FGI_TENCENT_ENABLED`；zzshare 由 `import zzshare` 成败决定（`daily_run.py:26-31`）。`README.md:134,169` 写 19:00，实际 `daily_update.yml:6` cron `0 10 * * 1-5` = 北京 18:00。

**方案**：三行改为 `*_ENABLED`，补 `FGI_AKSHARE_ENABLED`，说明 zzshare 无开关；时间改 18:00。

**验证**：grep 全仓确认无死变量名残留。

## P1-6 版本号三重漂移

**证据**：`pyproject.toml:7` = 3.6.0（首次提交后 408 次提交从未 bump）；根目录规格文档标题 V3.9.0；`settings.py:28` `FGI_VERSION` 是 `current/legacy` 运行时开关，不是版本号；`docs/index.html` 与 JS 不含任何版本。

**方案**：pyproject → 3.9.0（唯一真值）；export 脚本注入版本号到页面页脚；`FGI_VERSION` 改名为 `FGI_CALC_VERSION` 避免歧义。

## P1-7 AGENTS.md 的技能表与文档路径大部分不存在

**证据**：`AGENTS.md:95-121` 列 25 个技能，其中 16 个在 `~/.config/opencode/skills/` 下不存在（verification-planning、deepwork、to-tickets、request-refactor-plan、design-an-interface、simplify、reflect、worktrees、clonedeps、codebase-memory-mcp、ubiquitous-language、exa-web-search、gh-grep、context7-mcp、documentation-lookup、code-review）；项目级 `.opencode/skill` 为空。`AGENTS.md:126,128` 声称产出 `docs/glossary.md` 与读取 `CONTEXT.md` —— 两者都不存在。表里还混有 Husky、dependency-cruiser 等与 Python 仓库无关的条目。

**方案**：删除不存在的行；保留实际可用的；需要安装的注明「需先安装」；补 `CONTEXT.md` 或删掉相关承诺。

**验证**：逐条 `ls` 对应目录。

## P1-8 测试盲区：推送与渲染主链路无直接测试

**证据**：`fgi/output/pushplus.py`（184 行）零直接测试 —— `tests/unit/test_pushplus.py:3` 只 import `fgi.output.renderer` 的 4 个函数，文件名误导。`renderer.build_fgi_markdown`（被 `pushplus.py:164` 调用）、`decision_matrix_section` 无直接断言。`mootdx_source.py`、`tencent_source.py` 无任何测试 import。

**方案**：重写 `test_pushplus.py` 真正测 `send_fgi_report`（mock requests）；renderer 补两个函数的行为测试；mootdx/tencent 各 1 个 fetch 契约测试。

**验证**：`pytest --cov=fgi --cov-report=term-missing`，确认 pushplus 行覆盖。

## P1-9 缺三个与真实失败模式对应的测试

1. **上游返回旧数据**：无测试断言「akshare 返回 N 天前数据 → health_score 下降 / 推送带警告」；`scripts/backfill_late_data.py` 零测试。
2. **全部数据源同时挂**：`test_collector.py` 只测单链 fallback；缺「所有源 FAILED → FGICalculator 产出 sane 结果」的端到端测试。
3. **任一维度 NaN**：缺计算器级参数化测试；`test_fgi.py` 的 mock 恒为 `healthy=True`（:27）。

**方案**：按上述三条各加一个测试，全部放在 `tests/unit/`，用 tmp_path 真 sqlite（参考 `test_m3.py:13-21`）。

---

# P2 — 架构深化（7 项，逐项需先 grill 再动手）

| # | 问题 | 证据 | 建议形状 |
|---|---|---|---|
| P2-1 | 12 个 calculator 复制同一骨架（~30 行 × 12：lookback 计算、HEALTHY 检查、missing 分支、raw 写入 + upsert + normal 状态） | `f1.py:51-96` ≡ `s2.py:54-92`；m2/m3/m4/v1/v4/f3 同型 | 一个按「取数 / 变换 / raw_key 清单 / 状态来源」参数化的 driver，指标只留变换函数 |
| P2-2 | `raw_key` 知识散落四处，改名需手工同步 | `fallback.py:20-58`、`f1.py:70-93`、`backfill.py:75-157`、`signal_report.py:106-108` | 收拢到单一 module，由指标侧声明式提供 |
| P2-3 | `signal_report.py` 932 行混 6 种职责 | 常量 1-34；统计 36-92；Engine 94-176；Markdown 179-380；DB 查询 382-456；推送卡片 458-611；回测 619-841；分节渲染 843-932 | 切「纯分析 / 渲染 / DB 取数」三块，卡片独立 |
| P2-4 | `daily_status` 双写入者，靠 `upsert_status_keep_source` 特例调和 | `status.py:1-34` vs 各 calculator 内直接 `upsert_status` | 收敛为唯一 writer，删特例接口 |
| P2-5 | 数据源链的声明与实际能力不符，真 seam 只有 1 条 | `chains.py:12,17` 给 m1/s3 配 zzshare 兜底，但 `zzshare_source.py` 无 `fetch_zt_daily_summary`；`daily_run.py:61-67` 把 mootdx/tencent 追加到全部 14 条链，而二者只实现 `fetch_daily/fetch_index_daily` | configure 时按方法能力校验并报错，让死配置不可写 |
| P2-6 | 生产代码住在 `scripts/` | `export_fgi_web_data.py` 601 行被 `daily_update.yml:118` 与 `backfill_late_data.py:159` 依赖，却与 7 个一次性脚本混放 | 迁入 `fgi/output/`，一次性脚本删除（backfill_f1_margin、backfill_f1_market_cap、backfill_f3_flow、fetch_m4、spike_f3_alternatives、recompute_scores） |
| P2-7 | `setup_data_manager` / `is_trading_day` 双份实现且行为不同 | `daily_run.py:49-69` vs `backfill.py:15-17,41-49`（backfill 用纯 weekday） | 装配与交易日判断各收敛为一处 |

另：`database.py:75-136` 的 ~60 行一对一透传层（`db.upsert_score` vs `db.scores.upsert_score` 两套写法并存）应在迁移完成后删除。

---

# P3 — 前端（5 项）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| P3-1 | null 画成 0（见 P0-6） | `charts.js:172-173` | 保留 null，轴上标「—」 |
| P3-2 | 选非交易日点「查看」毫无反馈 | `components.js:30-35` 只在命中时动作 | `else` 提示「该日无交易数据，最近可用 X」，并可一键跳转 |
| P3-3 | 红绿承担两套相反语义 | `main.js:6-12`（红=恐惧）vs `components.js:55`（↑红=涨）；`main.css:132-133`「高」用绿底而 `components.js:208` 用 🔴 | 区域色只用于指数评分；趋势改灰底 + 箭头并标注口径；去掉 emoji |
| P3-4 | 历史曲线无 20/40/60/80 阈值线，默认只显示近 30% | `charts.js:52-67`、`dataZoom start:70` | 加 4 条浅色 markLine，标题注明「默认近 3 年，可拖动」 |
| P3-5 | 小字号与低对比度 | `main.css:21`（`--text-muted:#888` + 0.75rem）、`charts.js:226,283`（fontSize 9/8） | `--text-muted` 改 `#6b7280`；轴标签 ≥11px |

次要：加载失败的 `.catch(() => null)` 静默吞掉（`main.js:55-56`）；错误提示直出 `err.message` 暴露接口路径（`main.js:70`）；日期控件在核心指标之前（`index.html:26-34`）。

---

# 需要你决策的 4 件事

1. **`data/fgi.db` 出库**：重写历史（`git filter-repo`，收益约 173 MB，但改写 408 个提交）／保留但定期 squash／迁到 Release 附件？三者都要改 CI 的 DB 基线来源。
2. **Python 最低版本**：3.10 / 3.11 / 3.12？
3. **akshare 版本策略**：锁死 `==` ／`~=` ／下界 + 每周 smoke job？
4. **版本号唯一真值**：以 pyproject `3.9.0` 为准，还是删掉版本声明、只认规格文档？

# 建议执行顺序

| 批次 | 内容 | 依赖 |
|---|---|---|
| 1 | P0-1 → P0-5 → P0-2（CI 与告警链先修好，后续改动才有安全网） | 无 |
| 2 | P1-1 立即部分（仓库瘦身 + gc）＋ P1-5/P1-6/P1-7（文档纠错，零风险） | 无 |
| 3 | P1-2 → P1-3 → P1-4（环境与门禁，需要一次全新 venv 验证） | 决策 2、3 |
| 4 | P0-3 → P0-4 → P1-8 → P1-9（先补测试，再收紧行为） | 批次 1 |
| 5 | P2 逐项 grill，一次只动一项 | 批次 3、4 |
| 6 | P3 前端 | 独立 |

「先补观测、再收紧行为」是批次 4 的原则：P0-3 的抑制阈值建议先只标注、观察两周再启用。

---

# 执行记录（2026-11-18 当日落地）

四个决策已定：DB 出库到 Release 资产 / Python 3.10 / akshare `==1.18.75` / pyproject 为版本唯一真值。

## 已完成

| 项 | 落地方式 | 验证 |
|---|---|---|
| P0-1 | 失败判定前置；失败时 DB 与 web 数据都不写回，只留 artifact 与 issue | `workflow_dispatch` 全流程 success；`Create issue on failure` 按预期 skipped |
| P0-2 | `is_trading_day` 日历不可用改为抛错；推送前打印 stale 指标 | `test_calendar_unavailable_raises`、`test_stale_indicators_are_reported` |
| P0-3 | `run()` 返回 error/degraded/missing/ok 计数；`ERROR_SUPPRESS_THRESHOLD=3` 时抑制推送并 `exit 1` | `test_error_threshold_suppresses_push` 等 5 个用例 |
| P0-4 | v4 短路前加 `notna && > 0` 守卫（与 m1/m4/s3 一致）；f2 注释说明周频豁免理由 | `test_v4.py` |
| P0-5 | 推送失败 `exit 1`；异常检测崩溃按 fail-closed 处理 | `test_push_failure_exits_nonzero` |
| P0-6 | 雷达图保留 null 并显式列出缺哪几维 | 代码；需浏览器复核 |
| P1-1 | `git filter-repo` 移除 `data/fgi.db` 与 10MB jsonl；`.git` 249MB → **10MB**；两个 dependabot 分支因携带旧 DB blob 已删除（PR #134/#137 关闭，其版本意图已并入 pyproject） | `git count-objects -vH` = 10.12 MiB |
| P1-2 | `requires-python >=3.10`；CI matrix 3.10 + 3.12 | CI `test (3.10)` success |
| P1-3 | akshare 定版 1.18.75；依赖单一来源 pyproject；requirements.txt = `-e .[dev]` | 全流程 daily_update 安装成功 |
| P1-5 | README 环境变量改为 `*_ENABLED`，时间 18:00，补 DB 获取步骤 | grep 无死变量 |
| P1-6 | pyproject 3.9.0；导出脚本写 `app_version`；页脚显示 `v3.9.0 · 数据日期` | 线上 `fgi_latest.json` = `3.9.0` / `2026-11-18` |
| P1-7 | AGENTS.md 技能表按实际安装情况重写 | `ls ~/.config/opencode/skills/` 核对 |
| P3-2~5 | 非交易日提示、红绿语义统一、阈值参考线、对比度与字号、页脚 | `node --check` + 需浏览器复核 |

## 2026-11-19 补充（code-review 的 Spec 轴发现）

| 项 | 修正 |
|---|---|
| P0-2 方案 2（此前漏做） | forward-fill 的 elapsed=1 不再标 `normal`，改为 `forward_fill`；health_score 按「非 normal、非 impaired」计（每指标约 -3.3）。`golden_replay` 复核：FGI 分数 455 天完全未变，仅 14 处状态迁移 + 12 天 health 下降 |
| P0-2 方案 3 | 推送卡片顶部在「数据日期 ≠ 今天」时给出 ⚠️ 提示 |
| P2-5 | 首选源不能服务该链时改为 `ValueError`（此前是 warning 后静默剔除）；兜底源仍静默跳过 |

仍缺：P2-2 的写入侧——calculator 落 raw_data 时仍写字面量 raw_key（`f1.py:100-101`，且 `*_percentile` 未进 registry），改名需同步两处。

## 2026-11-22 后续进展

### P0-3 的残留缺口：抑制门对「数据源故障」不生效

原 P0-3 方案 3（`error` 数 ≥3 时抑制推送）已落地，但复核 `daily_status` 全史后发现它测的不是这件事：

- `error` 只在计算器**抛异常**时写入（`fgi/calculator/fgi.py`）；数据源取不到数走的是 `missing()`。
- 全库 `error` 共 90 条，最后一条停在 **2018-10-10**，其中 89 条是 `float() argument ... 'NoneType'`、1 条是空数组越界——**全部是代码崩溃，没有一条由数据源故障产生**（对照：`missing` 7403 条）。
- 即「所有源都挂 → 3 个指标 `missing` → 计数 0 → 照常推送」。2026-09-24 / 09-30 的 f1（`No data for date` → `missing`）就是实例，当天照常推送。

**落地**：新增 `fgi/output/health_gate.py`，在 `backfill_late` 之后的第二趟做回归判定——某指标此前 3 个有状态记录的日期全 `normal`、今天却 `missing`/`error`，且这类指标 ≥2 个时抑制推送并 `exit 1`（不写回、开 issue）。单个指标缺失不拦（属「当日未发布、次日补齐」的正常流程），第一趟不判。`AGENTS.md` 不变式 2 已同步，`test_health_gate.py` 覆盖。

### P1-3 的脆弱性：三条链改用官方 API

原审计担心 `akshare` 11 个端点「上游任一字段改名即静默降级」。2026-11-22 起 **m3 / m4 / f3 改用同花顺官方 REST**（ADR-0005），并用 DB-first 拼接保住 2015 年以来的历史——Fuyao 只提供近 5 自然年（1211 根 < 1260 窗口），整窗重取会直接偏 0.9~1.5 分。

实测：2026-11-19 上证 close / volume 与 akshare 相对误差 **1.2e-6**；离线重算 M3 +0.008、F3 −0.159 分。

**未解决**：其余 9 个指标仍依赖爬虫类接口。M1 / S3 因涨停池只有 **251** 个交易日（滚动一年窗口，不会随时间累积；截断实验偏差 12.8 / 34.3 分，且 251 < `min_periods` 252 会直接落 `missing`）明确**不接入**。详见 `docs/research/2026-11-22-limit-up-history-sources.md`。

### 仍然未做

清单未变，见下一节「未做」。测试规模 402 → **415 passed**（新增 `test_fuyao_source.py`、`test_health_gate.py` 与 m3/f3/m4 引导路径用例）。

## 未做（保留在 P1/P2/P3 清单里）

- **P1-4 mypy 逐类解禁**：需要先给 `storage/database.py` 等补注解，是独立一轮工作，不宜与行为改动混在一起。
- **P1-8/P1-9 测试补齐**：pushplus 直测、全源失败端到端、维度 NaN 参数化，见原条目。
- **P2 全部 7 项**：按约定每项先 grill 再动手。
- **P3-1 之外的剩余细项**：日期控件位置（首屏优先级）、趋势口径标注位置。

## 运维注意

- 本地开发现在需要先取 DB：`gh release download data-latest --pattern fgi.db --dir data --clobber`。
- GitHub 侧仓库体积（`repos.size`）在 force-push 后由 GitHub 异步回收，短时间内仍显示 60MB 左右，属正常。
- 备份：`~/FGI-backup/FGI-*.bundle`（44MB）+ `fgi-*.db`（14MB），确认新流程稳定后可删。
