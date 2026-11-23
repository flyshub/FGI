# M1 / S3 涨停数据源历史深度调研

- 日期：2026-11-22（实测数据于 2026-11-22 采集，Fuyao 实测日期口径至 2026-11-20）
- 范围：M1（每日涨停家数）、S3（每日涨停封单额合计），二者均为 **1260 个交易日滚动百分位**
- 方法：只查一手来源（官方文档 / 源码 / 交易所规则原文）；Fuyao 相关结论由主会话用真实 Key 实测后填入文末「本地实测」一节
- 约定：查不到就写「未查到」，不做推测

---

## 结论先行

1. **「等 Fuyao 的涨停池自己攒够 1260 天」不可行。** 涨停池窗口是**滚动的**，实测最早可用日 `2025-11-07`，到 `2026-11-20` 共 **251 个交易日（380 个自然日）**，即「最近约一年」，不随时间变长，永远到不了 1260。官方文档对保留期/历史窗口**只字未提**（未写即无法推断），只能以实测为准。详见 B 节与文末实测。
   > 注：此前记录的「约 416 自然日 ≈ 297 交易日」是**未二分核实的估计，已作废**。`docs/adr/0005-fuyao-primary-source.md:20` 与 `docs/audits/full-review-2026-11-18.md` 均已按 251 更正。

2. **继续用 levistock 作为 M1/S3 的历史主源。** 它是目前唯一同时满足「覆盖 2017-06 起约 2090 个交易日」「含封单额」「免费、无 Key」的源。历史深度由上游**开盘红（kaipanhong.com）**的留存决定，2017-06-02 前无数据（本仓库 CONSTRAINTS #64 已记录）。详见 A 节。

3. **Fuyao 可以做当日增量 + 交叉校验，不能做历史。** 同时注意两个坑：`limit-up-statistics` 端点**静默忽略 `date_ms`**（fail-open，不能取历史）；它与 `limit-up-pool` 的涨停口径**不一致**（同日 28 vs 30），接入前必须定口径。

4. **Tushare `limit_list_d` 是唯一值得引入的第二源**：数据从 **2020 年**开始累积（2020→今 ≈ 1665 个交易日 ≥ 1260，可独立覆盖完整 5 年窗口），含 `fd_amount` 封单金额，但**不提供 ST 股票统计**、需要积分。适合做 levistock 中断时的补洞与交叉校验，不是替代。详见 C 节。

5. **封单额无法从日 K 推导**（结构性缺数据：日 K 没有买一档委托队列）。涨停**家数**理论上可推，但要同时解决 5 个规则断点 + 4 类失败模式，其中「ST 比例 2026-07-06 由 5% 改 10%」和「除权日基准价是除权参考价而非昨收」两条最容易踩。详见 D 节。

6. **两个指标对窗口长度的敏感度不同，S3 更脆弱。** 截断到 251 天：M1 平均偏 12.84 分（最大 21.29），S3 平均偏 34.33 分（最大 47.98）——S3 的平均偏差是 M1 的 **2.67 倍**。重尾金额的高分位由少数极端日决定，251 天样本量不足以稳定估计。若将来必须二选一降级，**M1 比 S3 更能容忍短窗口**。另注意：`rolling_percentile` 的 `min_periods = 252`，**251 < 252，真实代码路径下 M1/S3 不是「偏分」而是每天直接 `missing`**——这个后果比偏差更严重。

---

## A. levistock 是什么

### A.1 基本信息

| 项 | 值 | 出处 |
|---|---|---|
| PyPI | https://pypi.org/project/levistock/ | PyPI 页面 |
| 最新版本 / 发布时间 | `0.1.8`，**2026-09-22T10:04:31Z** 上传（前一版 0.1.7 为 2026-05-22） | `https://pypi.org/pypi/levistock/json` 实测 |
| Python 要求 | `>=3.8` | 同上 |
| 源码仓库 | https://github.com/fleetinglife/levistock （MIT，创建于 2026-05-11） | 仓库页 |
| 定位 | 「A股市场数据 SDK，封装东方财富、财联社、同花顺、开盘红、i问财常用接口」 | 仓库 README |
| 本仓库依赖 | `pyproject.toml:23` → `"levistock>=0.1.8"` | `pyproject.toml:23` |

**最后一次更新：2026-09-22（0.1.8）。** 仓库 2026-05 创建，半年内 8 个版本，近期仍在维护。

### A.2 涨停数据的上游是谁

两个函数的上游**都是开盘红（kaipanhong.com）**，不是东财：

- `market_emotion_kph` → `levistock/market/market_emotion_kph.py:26`
  ```python
  _HOST_REALTIME = "https://apphwshhq.kaipanhong.com/w1/api/index.php"
  _HOST_HISTORY  = "https://apphis.kaipanhong.com/w1/api/index.php"
  ```
  「不传日期或传今天日期 → 实时接口（apphwshhq）；否则走历史接口（apphis）」。

- `limit_up_his_kph` → `levistock/stock/stock_daban_his_kph.py`
  ```python
  _HOST = "https://apphis.kaipanhong.com/w1/api/index.php"
  _BASE_PARAMS = {"PhoneOSNew":"1","DeviceID":"1a609dd6-...","VerSion":"6.0.6",
                  "Token":"0","UserID":"0","Red":"1","apiv":"w45"}
  ```
  调 `a=HisDaBanList&c=HisHomeDingPan&PidType=4`（`_PID_LIMIT_UP = "4"` 涨停 / `3` 跌停 / `6` 风向标），分页 `st=50` + `Index`，`_headers` 用 Android `Dalvik/2.1.0` UA。

  模块头注释写明：
  > 「仅支持历史日期查询，当日数据由 TCP 长连接推送无法获取」

  关键字段（`_parse_limit_up`）：
  | 字段 | 来源 | 含义 |
  |---|---|---|
  | `seal_money` | `row[23]` | **封单金额（元）** ← S3 用的就是它 |
  | `seal_amount` | `row[8]` | 封单量 |
  | `open_time` | `row[7]` | 开板时间戳（0=未开板） |
  | `limit_count` / `limit_tag` | `row[10]` / `row[9]` | 连板数 / 首板·二板标签 |
  | `reason` / `themes` | `row[16]` / `row[11]` | 涨停原因 / 题材 |

**历史深度由什么决定**：由**开盘红 `HisDaBanList` 接口自身的留存期**决定，levistock 只是薄封装、不做任何本地补历史。本仓库记录的边界是 **2017-06-02**：

- `A股恐惧贪婪指数（FGI）实施方案 · 终稿 V3.9.md:57` —「levistock API 在 2017-06-02 前无数据覆盖（CONSTRAINTS #64），该区间 raw_data 标记 NULL」
- 同文件 `:766`、`:771` — M1 / S3 的历史起点均记为 `2017-06 (levistock)`
- 按 2017-06-02 → 2026-11-20 计，约 **2090 个交易日**（与背景一致）

### A.3 已知失效模式

| # | 失效模式 | 证据 |
|---|---|---|
| 1 | **历史空窗**：2017-06-02 前完全无数据 | `实施方案 · 终稿 V3.9.md:57`、`:766` |
| 2 | **同源不同字段起点不同**：`market_emotion_kph` 的 `rise_num`/`fall_num` 仅 2024-04-22 起有数据（曾因此被排除出 M2 候选） | `实施方案 · 终稿 V3.9.md:33` |
| 3 | **中断被误判为「0」**（最危险）：production DB 中 496 行 `s3_seal_fund` 被存为 0 或 `~1.4e-142`，而同日 M1（同源涨停家数）均 > 0，证实是数据源中断被当成「零封单」。零值分布 2017(126) / 2018(117) / 2020(231) / 2026(2) | `实施方案 · 终稿 V3.9.md:46`；已修（`:57` ②「`zt_backfill` 把空列表误写成 0，改为 skip」） |
| 4 | **未授权爬虫接口**：硬编码 `DeviceID` + `Token="0"` + `UserID="0"` + 伪装 Android UA，账号侧一旦校验 Token 或按 IP/设备风控即失效 | `levistock/stock/stock_daban_his_kph.py` `_BASE_PARAMS` / `_headers` |
| 5 | **当日不可取**：`limit_up_his_kph` 对 `date >= today` 直接 `raise ValueError("日期必须小于今天")`；`market_emotion_kph` 当日走另一台主机 | `stock_daban_his_kph.py::_fetch_daban`；`akshare_source.py:568-569` 注释 |
| 6 | **网站改版风险**（项目自评）：「zzshare、levistock 等爬虫数据源可能因网站改版中断」 | `实施方案 · 终稿 V3.9.md:740` |
| 7 | **限流/慢**（已在代码里兜）：每 20 个日期 `sleep(0.5)`，`market_emotion_kph` 走 `_retry(retries=2, delay=2)` | `fgi/collector/akshare_source.py:555-556`、`:196` |

**未查到**：levistock / 开盘红 官方的 SLA、字段变更公告、IP 封禁策略——这些都是非公开爬虫接口，没有官方文档。

### A.4 我们代码里用它哪些函数

**主路径 `fetch_zt_daily_summary`**（`fgi/collector/akshare_source.py:543-595`，docstring：「Fetch daily 涨停板 summary via levistock」）：

| 行 | 做什么 |
|---|---|
| `:548` | `import levistock as lk` |
| `:550` | `pd.date_range(start, end, freq="B")` 逐交易日循环 |
| `:559` | `lk.market_emotion_kph(date=ds)` ← M1 家数来源 |
| `:565` | `zt_count = emotion.get("sjzt", emotion.get("zt", 0))` ← **优先 `sjzt`（实际涨停），缺失回退 `zt`** |
| `:568-569` | 注释：当天 API 会拒绝（"日期必须小于今天"），回退到昨天（T+1 延迟）而非 0，避免情绪维度被错误压低 |
| `:573` | `lk.limit_up_his_kph(date=ds)` ← S3 封单额来源 |
| `:578` | `seal_fund = sum(item.get("seal_money", 0) for item in limit_up_list)` |
| `:585` | `seal_fund if seal_fund > 0 else last_seal_fund` ← 跨日向后借用上一次非零值 |

**配套**：
- 回填路径 `fgi/output/zt_backfill.py:21-30` — 同样读 `market_emotion_kph` 的 `sjzt`/`zt` 与 `limit_up_his_kph` 的 `seal_money`（`:24`、`:30`），与日线路径保持一致
- 链注册 `fgi/indicators/registry.py:23`（`m1_zt_stats`）、`:28`（`s3_zt_daily`）→ 均映射到 `fetch_zt_daily_summary`；`:47-50` 声明 raw_key 为 `("m1_zt_count", "s3_seal_fund")`
- 计算器入口 `fgi/calculator/momentum/m1.py:31`、`fgi/calculator/sentiment/s3.py:33`
- 另有 `fetch_zt_pool`（`akshare_source.py:161-183`）走 **`ak.stock_zt_pool_em(date=...)`**，但**未注册进任何链**（只出现在 `mock_source.py:32` 与 `tests/unit/test_collector.py:93`），不参与 M1/S3

> ⚠️ **文档漂移**：`实施方案 · 终稿 V3.9.md:148` 与 `:188` 写 M1/S3 数据源是「levistock `stock_zt_pool_em`（主），AKShare `stock_zt_pool_em`（备，仅历史）」，与实际代码（`market_emotion_kph` + `limit_up_his_kph`）**不一致**。本次只记录，不改文件。

---

## B. Fuyao 涨停池的官方说法

### B.1 端点与参数

`GET /api/a-share/special-data/limit-up-pool`，文档：https://fuyao.aicubes.cn/docs/api-reference/limit-up-data/limit-up-pool

> 文档开篇：「按交易日返回 A 股涨停 / 连板股票池，**后端固定取全部连板与 `main,chinext,ssestar,north` 四类板块**，支持分页与排序。」

| 参数 | 位置 | 类型 | 必需 | 说明 | 默认 |
|---|---|---|---|---|---|
| `date_ms` | query | long | 否 | 查询交易日 Unix 毫秒戳（Asia/Shanghai 00:00:00）；**省略时回退到服务端当前自然日** | - |
| `page` | query | integer | 否 | 页码，必须 `>= 1` | `1` |
| `size` | query | integer | 否 | 分页大小，`1`–`200` | `50` |
| `sort_field` | query | enum | 否 | `last_price` / `continue_day_cnt` / `seal_money` / `limit_up_time` | `last_price` |
| `sort_dir` | query | enum | 否 | `asc` / `desc` | `desc` |

分页机制：返回 `data.pagination = { total, pages, size, page }` + `data.item[]`，翻页取全部需循环 `page=1..pages`（单页最多 200 条）。

**错误码**：`sort_field` 不在白名单 → `code=1002`；`page < 1` 或 `size` 越界 → `code=1003`；Key 缺失/无效 → `code=2001`。HTTP 状态恒为 200，业务错误走 `code`。

### B.2 返回字段（含封单额）

| 字段 | 类型 | 说明 |
|---|---|---|
| `thscode` / `ticker` / `name` | string | `603986.SH` / `603986` / 兆易创新 |
| `is_st` | boolean | 是否 ST，**仅展示，不作为筛选入参** |
| `is_new` | boolean | 是否未开板新股，仅展示 |
| `last_price` | decimal | 当前价格（元） |
| `price_change_ratio_pct` | decimal | 涨跌幅百分比（已 ×100） |
| `limit_up_time` | string | 涨停时间 `HH:MM` |
| `limit_up_reason` | string/null | 涨停原因；上游空串标准化为 `null` |
| `continue_day_text` / `continue_day_cnt` | string/int | `首板`、`5天4板` / 连板计数 |
| **`seal_money`** | decimal | **当前封单额（元）** ← S3 对应字段 |
| `max_seal_money` | decimal | **峰值封单额（元）** |
| `open_times` | int/null | 开板次数；缺失为 `null` |

同组另外三个端点：`limit-down-pool`（**无封单额字段**）、`limit-break-pool`（炸板）、`limit-up-ladder`（连板天梯，文档明写「返回**近 30 个交易日**的连板梯队矩阵」——这是该组文档里**唯一**出现的窗口表述，且属于另一个端点）。来源：https://fuyao.aicubes.cn/docs/api-reference/limit-up-data/

### B.3 关于保留期 / 历史窗口

**文档未写。** `limit-up-pool` 的参数表、字段表、约束与错误三节中，**没有任何**关于 `date_ms` 可接受范围、数据保留天数、历史起始日的说明；也没有类似 `limit-up-ladder` 的「近 N 个交易日」字样。

→ **滚动窗口还是累积窗口，文档层面无法判断，需实测**（实测结论见文末 `本地实测（Fuyao API）`：**滚动，最早 2025-11-07，251 个交易日**）。

### B.4 另外两个必须知道的坑（实测）

1. **`limit-up-statistics` 静默忽略 `date_ms`** —— 传 2025-11-07 / 2026-06-01 / 2026-11-20，返回 `trade_date` 一律 `2026-11-20`。这是典型的 **fail-open**：调用方无法察觉参数没生效，**绝不能用它取历史**。本项目原则「失败必须可见，不许 fail-open」（`AGENTS.md` 数据管道不变式 #2）直接适用。
2. **两个端点口径不一致** —— 同日 2026-11-20：`statistics.limit_up_stock_count = 28`，`limit-up-pool.pagination.total = 30`，差 2（可能涉及 ST / 新股 / 北交所过滤）。**接入前必须定口径，不能混用。**

---

## C. 还有哪些源能提供长历史涨停数据

### C.1 Tushare `limit_list_d`（涨跌停列表·新）

出处：https://tushare.pro/document/2?doc_id=298

| 项 | 值 |
|---|---|
| **最早日期** | **「数据从 2020 年开始」**（官方原文） |
| ST | **「不提供 ST 股票的统计」**（官方原文） |
| **封单额** | ✅ 有，**`fd_amount` =「封单金额（以涨停价买入挂单的资金总量）」** |
| 其他关键字段 | `limit`（D跌停/U涨停/Z炸板）、`limit_times` 连板数、`first_time`/`last_time` 封板时间、`open_times` 炸板次数、`up_stat`、`limit_amount`（**板上成交金额，仅跌停有，涨停无此数据**）、`exchange`（SH/SZ/BJ） |
| 单次限量 | 2500 条，可按日期或股票循环提取 |
| **限流** | **5000 积分**：200 次/分钟、1 万次/天；**8000 积分以上**：500 次/分钟、**每天总量不限制** |
| 积分获取 | https://tushare.pro/document/1?doc_id=13 （**未查到公开价目**，需按官方办法获取） |

**覆盖测算**：2020-01 → 2026-11-20 ≈ 6.9 年 ≈ **1665 个交易日 ≥ 1260**。也就是说，**Tushare 单独就能覆盖一个完整的 1260 天窗口**（窗口只需回溯到 2021-11），且起点比 levistock 的 2017-06 晚，但仍在窗口内。

**风险**：
- 「不提供 ST 股票的统计」与我们 M1 的 `sjzt`（实际涨停）口径**可能一致也可能不一致**——`sjzt` 的精确定义**未查到**（levistock 与开盘红均无文档），**需实测比对**。
- `fd_amount` 是否从 2020 起就有值（而非后期才加字段）——**文档未写，需实测**。
- 限量条款写明「如需商业用途，请自行联系同花顺解决数据采购问题」的限制**属于 `limit_list_ths`**，不是 `limit_list_d`；两者别混。

### C.2 Tushare `limit_list_ths`（同花顺源）

出处：https://tushare.pro/document/2?doc_id=355

- **最早日期：`20231101`**（官方原文「历史数据从20231101开始提供」），增量每天 16 点左右更新
- **封单额**：✅ `limit_amount` 封单额、`lu_limit_order` 最大封单（**仅涨停池、连板池有值**，炸板池/跌停池为空）
- 限量：单次最大 4000 条；**8000 积分以上**每分钟 500 次、每天总量不限制
- ⚠️ 官方明写：「**本接口只限个人学习和研究使用，如需商业用途，请自行联系同花顺解决数据采购问题**」

**2023-11 → 2026-11 ≈ 750 个交易日 < 1260，不足以独立支撑窗口。**

### C.3 AKShare 各涨停池端点

出处（官方文档）：https://akshare.akfamily.xyz/data/stock/stock_quote.html#id88 「涨停板行情」章节

| 接口 | 说明 | 时间跨度 | 封单额字段 |
|---|---|---|---|
| `stock_zt_pool_em` | 东方财富-涨停股池 | **「该接口只能获取近期的数据」**（官方原文，未给出具体天数） | ✅ **`封板资金`** |
| `stock_zt_pool_previous_em` | 昨日涨停股池 | **「该接口只能获取近期的数据」**（官方原文） | ❌ 无（有 `涨停价`、`昨日封板时间`） |
| `stock_zt_pool_strong_em` / `sub_em` / `zbgc_em` / `dtgc_em` | 强势股 / 潜在涨停 / 炸板股 / 跌停股 | 同属该章节，均为「近期」口径 | 见各接口输出参数表 |

- **目标地址**：`https://quote.eastmoney.com/ztb/detail#type=ztgc`，即**东方财富 push2ex 涨停板行情**（非官方开放接口，AKShare 逆向封装）
- **AKShare 本身免费开源**（`pip install akshare`），无 Key、无积分，但依赖东财页面/接口稳定性
- **「近期」到底是多久**：官方文档**未给数字**；社区资料（VeriGlow 的接口梳理，**非官方**）称 push2ex 涨停池仅保留**约 30 个交易日**。→ **未查到官方数字，需实测**
- 本仓库已有实现：`fgi/collector/akshare_source.py:170` 调 `ak.stock_zt_pool_em(date=ds)`（在未接线的 `fetch_zt_pool` 里）

### C.4 东方财富 push2ex 能否直接取历史

**否（就「长历史」而言）。** push2ex 无公开 API 文档；AKShare 作为最接近官方的中文文档，对该族接口统一标注「**只能获取近期的数据**」。社区称约 30 个交易日。**未查到任何官方渠道能直接回溯 5 年涨停池。**

### C.5 同花顺 iFinD / Wind / JoinQuant / RiceQuant

| 源 | 历史涨停池？ | 最早日期 | 封单额 | 成本 | 出处 |
|---|---|---|---|---|---|
| **同花顺 iFinD 数据接口** | **未查到**有专门的「历史涨停池」函数。`THS_DataPool` 数据池列表为板块成分、指数成分、融资标的、停牌/复牌、十大股东等，**未列涨停池** | 未查到 | 未查到 | iFinD 终端 + 数据接口权限；**免费账号仅少量接口，试用账号「历史数据年限不同」，正式账号购机开通** | 数据接口说明 http://ft.10jqka.com.cn/thsft/iFindService/docCenter?ID=S201501231127 ；FAQ https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/faq.html |
| **Wind** | 未查到官方「历史涨停池」API 文档。实务中普遍做法是**用 Wind 日线自行判定涨停**（研报：2021-01~2025-12 全 A，用 `close` 与涨跌幅阈值识别，「涨停判定基于收盘价而非盘中最高价」，并单独处理 48,664 次炸板） | 未查到（Wind 日线覆盖极长，但涨停池**未查到**） | 未查到 | **付费终端级**（「机构标配」） | 华安证券金工研报 http://finance.sina.com.cn/wm/2026-03-20/doc-inhrqtyz8159352.shtml ；Wind 使用指南 https://maoyanqing.com/wind |
| **JoinQuant（聚宽 JQData）** | **无专门涨停池接口**。社区方案是 `get_price(全市场, fields=['close','high_limit','paused'])` 后筛 `close == high_limit` | `get_price` 数据自 **2005 年**（JQData 说明）；`start_date` 默认 `2015-01-01` | ❌ **无封单额**（字段仅 `high_limit`/`low_limit` 涨跌停价） | 注册试用 → 付费购买 JQData | JoinQuant API PDF https://cdn.joinquant.com/help/img/JoinQuantAPI.pdf ；jqdatasdk https://github.com/JoinQuant/jqdatasdk |
| **RiceQuant（米筐 RQData）** | **无专门涨停池接口**。`get_price` 提供 `limit_up` / `limit_down` 字段 | A股日线「**过去 10 多年以来**」；分钟线 2005 年起 | ❌ **无封单额** | 基础版 / 企业版（企业版含 tick 等更多数据），需注册 | https://rqopen.ricequant.com/doc/quant/ ；https://rqopen.ricequant.com/doc/rqalpha-plus/api/data-api |

**结论**：这四家都**不能**直接给出「历史涨停池 + 封单额」。iFinD/Wind 付费且未查到对应接口；JQ/RQ 免费档只能给涨跌停**价格**，需要自己推导（回到 D 节），且同样没有封单额。

---

## D. 能不能自己推导

### D.1 涨停家数：理论可推，但有 5 个规则断点

**所需输入**：Fuyao `daily-k` dump（全市场未复权日 K）+ `adjustment-factors` dump + 逐日 ST 状态 + 上市/退市/停牌日历。

Fuyao 官方 dump 规格（https://fuyao.aicubes.cn/docs/api-reference/market-dumps/ ）：

| dump | `dump_id` | 内容 |
|---|---|---|
| daily-k | `a_share_daily_k_1d_none_10y` | 「**daily-k 提供最近约 10 年全量窗口**」；列 `thscode, currency, interval, adjusted, date_ms, open_price, high_price, low_price, close_price, volume, turnover`；`adjusted` 恒为 `none`（**未复权**） |
| daily-k-10d | `a_share_daily_k_1d_none_10d` | 最近 10 个交易日增量 |
| adjustment-factors | `a_share_adjustment_factors_event_none_all` | 事件流，列 `thscode, ticker, ex_date_ms, dividend_per_share, per_share_bonus, allotment_ratio, allotment_price, currency` |

> ✅ **已实测澄清（2026-11-22）**：`docs/audits/full-review-2026-11-18.md:313` 的「近 5 自然年（1211 根）」指的是**逐 thscode 的 quotes 接口**，不是 dump。dump 实测 **2016-11-21 → 2026-11-19，2428 个交易日，5580 只股票，10,408,334 行**（下载 182.8 MB 后读 parquet 统计）——10 年窗口恒定覆盖 1260，不受滚动影响。

#### 涨跌幅规则的准确定义与生效时间

| 板块 / 情形 | 比例 | 生效时间 | 出处 |
|---|---|---|---|
| 沪深**主板**普通股 | **10%** | 长期有效 | 上交所交易规则(2023修订) 3.3.13 https://cms.crsec.com.cn:9443/files/file/2023/03/22/a70cddd8-46f7-4ae1-8c55-446422da355d.pdf ；深交所交易规则(2026修订) 3.3.13 https://docs.static.szse.cn/www/lawrules/rule/stock/W020260424690713155663.pdf |
| 沪深主板**风险警示股票（ST/\*ST）** | **5% → 10%** | **2026-07-06 起改为 10%**（2026-04-24 发布，2025-06-27 曾征求意见） | 上交所新闻稿「将主板风险警示股票价格涨跌幅限制比例由5%调整为10%」 https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml ；深交所修订说明 https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713346617.pdf ；旧规则 5% 见上交所交易规则(2023) 4.4.10 |
| **创业板** | **20%** | **2020-08-24 起** | 深交所《创业板交易特别规定》 https://www.szse.cn/lawrules/rule/stock/t20200824_583939.html ；证监会问答「涨跌幅限制比例由10%提高至20%…自2020年8月24日起施行」「首次公开发行上市后的前5个交易日不实行价格涨跌幅限制」 http://www.csrc.gov.cn/csrc/c101953/c7493228/content.shtml |
| **科创板** | **20%**，首发上市后**前 5 个交易日不设限** | 2019-07-22 开板 | 上交所《科创板股票交易特别规定》第十八条 https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/10785118/files/8c544552dc7e4c83a863440179f0b9de.pdf |
| **北交所** | **30%**，**仅上市首日**不设限（不含增发上市）、退市整理期首日不设限 | 长期有效；《北京证券交易所交易规则》2026-07-06 施行 | 北证公告〔2026〕17号 3.3.11/3.3.12（镜像 http://office.cjtax.cn/a/202604/yrlwnksyembv.shtml ）；财联社北交所官方问答「涨跌幅限制比例为前收盘价的30%」 https://www.cls.cn/detail/1777985 |
| **不设涨跌幅**的情形 | — | — | 深交所 3.3.15：① 首次公开发行上市后的**前五个交易日** ② **重新上市首日** ③ **进入退市整理期的首日** ④ 本所认定的其他情形；上交所 3.3.13 同口径；北交所 3.3.12 仅「首发上市首日 + 退市整理期首日 + 证监会/本所规定」 |

**计算与取整**（决定「是不是涨停」的那一步）：
- 涨跌幅限制价格 = **前收盘价 ×（1 ± 比例）**（深交所 3.3.14、上交所 3.3.13）
- **四舍五入至最小价格变动单位 0.01 元**（深交所 3.3.19「按照四舍五入原则取至申报价格最小变动单位」；上交所同）→ 例：4.37 × 1.10 = 4.807 → **4.81**
- **除权除息日的基准不是昨收，而是除权（息）参考价**：
  > 除权（息）参考价 ＝〔（前收盘价 − 现金红利）＋ 配股价格 × 股份变动比例〕÷（1 ＋ 股份变动比例）
  > 「除权（息）日证券买卖，按除权（息）参考价作为计算涨跌幅度的基准」
  出处：深交所交易规则 4.4.2 / 4.4.3，上交所交易规则 4.3.2 / 4.3.3；即时行情「前收盘价」在除权日即为除权参考价（深交所 5.2.3 第（四）项）。上交所投教同口径 https://www.cls.cn/detail/1306861
  ⚠️ 深交所 4.4.2 明写「证券发行人认为有必要调整上述计算公式时，可以向本所提出调整申请」——**存在个案改公式的可能**。

#### 哪些会让推导失败

| # | 失败模式 | 说明 |
|---|---|---|
| 1 | **ST 标记的历史缺失** | 比例取决于当日是否 ST（主板 5%→10% 断点叠加），而 ST 是**逐日状态**（戴帽/摘帽随时发生）。Fuyao `daily-k` **无 ST 列**（列见上表）；`limit-up-pool` 的 `is_st` 只覆盖其 251 天窗口。**必须另取股票状态历史**，否则 2017–2026 的 ST 判定不可靠 |
| 2 | **规则断点** | 创业板 10%→20%（2020-08-24）、主板 ST 5%→10%（2026-07-06）。比例必须**按日期分段**取，硬编码单一比例会整段判错 |
| 3 | **复权 / 除权基准** | `daily-k` 是 `adjusted=none`（未复权）——方向正确，但**除权日的「前收盘价」必须换成除权参考价**。需用 `adjustment-factors` 事件流自算；公式含 `dividend_per_share`、`per_share_bonus`、`allotment_ratio`、`allotment_price`，与深交所 4.4.2 逐项对应。**漏一天除权日就多判/漏判一批涨停** |
| 4 | **四舍五入到分** | 必须按交易所规则 round-half-up 到 0.01，再与收盘价比较；直接用 `close >= prev_close * 1.1` 的浮点比较会因 `4.807 vs 4.81` 系统性漏判 |
| 5 | **盘中开板（口径分叉）** | 「收盘封住」vs「盘中触及」是两个数：研报口径「涨停判定基于收盘价而非盘中最高价」，并指出 5 年 48,664 次炸板是「伪涨停」。levistock 有 `open_time`（0=未开板）、Fuyao 有 `open_times`，**日 K 只能判收盘涨停，判不出炸板** |
| 6 | **停牌 / 当日无成交** | 深交所 4.2.3「当日无成交的，以前收盘价为当日收盘价」——这类日子若按 `close == prev_close` 比较会误判 |
| 7 | **无限制日** | 新股前 5 日（沪深主板/创业板/科创板）、北交所首日、退市整理期首日、重新上市首日**没有涨跌幅**，**无法用规则判定涨停**，必须整日或整股剔除。需要可靠的上市日/退市整理起始日历史 |
| 8 | **北交所是否计入 M1** | 现有源口径不一：Fuyao 后端固定含 `north`，Tushare `limit_list_d` 有 `exchange=BJ`，levistock 口径**未查到**。**M1 到底含不含北交所，需先定口径** |

### D.2 涨停封单额：**不能从日 K 推导**

**结构性不可得。** 封单额 = 收盘时挂在涨停价上的**未成交买入委托金额**（`seal_money`，单位元）。它属于**委托队列（order book）信息**，而日 K 只有 `open / high / low / close / volume / turnover` 五个量价字段 + 成交额——**成交额是已成交的钱，封单额是没成交的钱**，两者无函数关系。

要拿到它，必须有下列之一：

1. **Level-2 五档（或十档）行情的历史快照**，在收盘时点取「买一价 == 涨停价」的挂单量 × 涨停价 —— 需要逐日逐股的 L2 快照历史，成本高、且多数供应商不提供 5 年前的 L2 快照
2. **交易所或授权数据商的涨停池统计**（即 levistock / Fuyao / Tushare `fd_amount` / 东财 `封板资金` 这一类）
3. **券商/数据商定制数据库**（Wind、iFinD 的涨停专题库）

→ **结论：S3 无法自行推导，只能依赖第 2 类源。这也意味着 S3 的源选择面比 M1 窄得多——M1 至少还有「用日 K 自己算」这条退路，S3 没有。**

---

## 源对比表

| 源 | 最早可用日期 | 封单额 | 成本 | 可靠性 / 已知问题 | 出处 |
|---|---|---|---|---|---|
| **levistock**（`market_emotion_kph` + `limit_up_his_kph`，上游开盘红） | **2017-06-02**（≈2090 交易日） | ✅ `seal_money`（元） | 免费，无 Key | 非官方爬虫；硬编码 Token；2017 前空窗；中断曾被写成 0；改版即断 | `stock_daban_his_kph.py`；`market_emotion_kph.py:26`；`实施方案·V3.9.md:46,57,740` |
| **Fuyao `limit-up-pool`**（同花顺官方） | **2025-11-07（251 交易日，滚动）** | ✅ `seal_money` / `max_seal_money`（元，实测无空值） | 需 `X-api-key`（计费方式**未查到**） | 官方 REST，稳定；但窗口太短；`statistics` 端点 fail-open；与 pool 口径不一致 | https://fuyao.aicubes.cn/docs/api-reference/limit-up-data/limit-up-pool ；文末实测 |
| **Tushare `limit_list_d`** | **2020 年**（≈1665 交易日 ≥1260） | ✅ `fd_amount`（元） | 5000/8000 积分（**价目未查到**） | 官方文档；**不含 ST**；单次 2500 条；`fd_amount` 早期是否可用需实测 | https://tushare.pro/document/2?doc_id=298 |
| **Tushare `limit_list_ths`** | 2023-11-01（≈750 交易日 <1260） | ✅ `limit_amount` / `lu_limit_order` | 8000 积分 | **仅限个人学习研究，商用需联系同花顺** | https://tushare.pro/document/2?doc_id=355 |
| **AKShare `stock_zt_pool_em`**（东财 push2ex） | **「只能获取近期」**（官方原文，天数未给；社区称约 30 交易日） | ✅ `封板资金` | 免费开源 | 逆向东财非公开接口；无官方 SLA | https://akshare.akfamily.xyz/data/stock/stock_quote.html#id88 |
| **东财 push2ex 直连** | 同上，**未查到**可回溯 5 年的官方途径 | ✅（经 AKShare 暴露为 `封板资金`） | 免费 | 无公开 API 文档 | 同上 |
| **同花顺 iFinD** | **未查到**有历史涨停池函数 | **未查到** | 付费终端 + 数据接口权限（免费账号仅少量接口） | 未查到对应官方函数 | http://ft.10jqka.com.cn/thsft/iFindService/docCenter?ID=S201501231127 |
| **Wind** | **未查到**有历史涨停池 API（实务为用日线自算） | **未查到** | 付费终端级 | 未查到官方接口文档 | 华安证券研报（新浪） |
| **JoinQuant JQData** | 日线自 2005 年起 | ❌ 无（仅 `high_limit`/`low_limit` 价格） | 注册试用 → 付费 | 需自己判涨停，无封单额 | https://cdn.joinquant.com/help/img/JoinQuantAPI.pdf |
| **RiceQuant RQData** | A股日线「10 多年以来」 | ❌ 无（仅 `limit_up`/`limit_down` 价格） | 基础版/企业版，需注册 | 需自己判涨停，无封单额 | https://rqopen.ricequant.com/doc/quant/ |
| **自行推导（Fuyao daily-k dump）** | **2016-11-21 起**（实测 2428 交易日，10 年滚动窗口） | ❌ **结构性不可得** | 需 Fuyao Key | 8 类失败模式（见 D.1） | https://fuyao.aicubes.cn/docs/api-reference/market-dumps/ ；文末实测 |

---

## 决策建议

### 建议

1. **M1 / S3 继续以 levistock 为唯一历史主源，不接入 Fuyao 做历史。**
   - 理由：Fuyao 窗口 251 交易日 ≪ 1260（占 19.9%），截断实验平均偏差 12.84 / 34.33 分，且 251 < `min_periods`（252）会导致直接 `missing`，远超可接受范围；且窗口滚动，**「等」永远等不到**。
   - Fuyao 的正确用法（与 ADR-0005 对 m3/m4/f3 的处理一致）：**只做最近若干日的增量与交叉校验**，历史一律 DB-first。

2. **引入 Tushare `limit_list_d` 作为第二源**，用于：levistock 单日失败时补洞、以及对 `m1_zt_count` / `s3_seal_fund` 做定期交叉校验。
   - 前提：拿到 5000+ 积分；先实测 `fd_amount` 在 2020–2021 段是否有值；先对齐「ST 是否计入」的口径（Tushare 明确不含 ST）。

3. **不把「自己推导」作为主路径，但它是 M1 唯一可行的退路**：`daily-k` dump 实测覆盖 2016-11-21 起 2428 个交易日（≫ 1260），历史深度不是问题，代价是要处理 D.1 的 8 类规则失败模式（ST 逐日状态、2020-08-24 创业板与 2026-07-06 主板 ST 两次比例变更、除权参考价、四舍五入到分、停牌日、无涨跌幅日、北交所口径）。
   - **近期落地方式：先做校验器。** 用推导出的涨停家数与 levistock 的 `sjzt` 逐日比对，偏差超阈值即告警——这能把「源静默失真」变成可见失败，符合项目不变式 #2。
   - **S3 不适用此法**（封单额推导不出来）。

4. **修两处文档漂移**（已由主会话同步）：
   - `实施方案 · 终稿 V3.9.md:148`、`:188` 写 M1/S3 数据源是 `stock_zt_pool_em`，实际是 `market_emotion_kph` + `limit_up_his_kph` —— **已改正**
   - `docs/audits/full-review-2026-11-18.md` 的「约 297 个交易日」—— **已改为 251**

### 置信度

| 结论 | 置信度 | 依据强度 |
|---|---|---|
| Fuyao 涨停池窗口是滚动的、约 251 交易日 | **高** | 二分查找实测（2025-11-06 及更早一律 `code=1002`） |
| 「等 1260 天」不可行 | **高** | 滚动窗口 + 251 ≪ 1260，数学上不可能 |
| levistock 覆盖 2017-06-02 起 | **高** | 本仓库 CONSTRAINTS #64 + 生产数据起点一致 |
| Tushare `limit_list_d` 2020 起、含 `fd_amount`、不含 ST | **高**（官方文档原文） | 但**字段历史连续性未实测** |
| Tushare 可独立覆盖 1260 天窗口 | **中高** | 由「2020 起」推算，需实测 2020–2021 段字段完整性 |
| 东财 push2ex 约 30 交易日 | **低** | 仅社区来源，官方只说「近期」 |
| iFinD / Wind 无历史涨停池 | **中**（只能证否到「未查到」） | 二者文档分散，不排除藏在付费手册里 |
| Fuyao daily-k dump 覆盖 10 年（2016-11-21 起，2428 交易日） | **高** | 下载 182.8 MB 实测 min/max date、行数、股票数 |

### 什么情况下结论会变

| 触发条件 | 结论如何变 |
|---|---|
| **Fuyao 扩充历史**（实测最早日往前推，例如推到 2021 年之前） | 「不可行」翻转为「可行」——**但必须重新二分实测**，不能信文档 |
| **levistock / 开盘红 断供或改版**（Token 失效、接口下线） | 唯一历史源消失 → 被迫二选一：(a) 接受窗口截断并重标定权重，(b) 付费买 Wind/iFinD 涨停专题库，(c) 用日 K 推导 M1（S3 无解） |
| **Tushare `fd_amount` 证实 2020 起完整 + ST 口径对齐** | Tushare 可升为**主源**（免费档限流够用即可），levistock 降为兜底——这是最可能的正向翻转 |
| **截断实验复算出显著更小的偏差**（例如换用分位数插值/收缩估计） | 「S3 不能截断」的强度下降，短窗口方案重新进入视野 |
| **M1/S3 权重被下调或窗口被改为 630 天** | 对源的历史深度要求同步下降，Fuyao 的 251 天可能变得可用（仍需重跑截断实验） |
| **发现 `sjzt` 与 Tushare「不含 ST」口径不一致** | 交叉校验方案失效，第二源需换（或先做口径映射） |

---

## 本地实测（Fuyao API）

> 采集条件：2026-11-22，使用真实 API Key 直接请求 `https://fuyao.aicubes.cn`（非文档推断）。

1. **涨停池最早可用日 = `2025-11-07`。** 用二分查找确定：`2025-11-06` 及更早一律返回 `code=1002 "Invalid parameter format"`。从该日到 `2026-11-20` 共 **251 个交易日**，距今 **380 个自然日**。窗口形态为**「最近一年」滚动窗口，不是从某个服务上线日累积**——因此「等 Fuyao 攒够 1260 天」在数学上不可能：滚动窗口长度恒定，永远到不了 1260。

2. **越界日期的错误信息有歧义。** 超出范围的日期返回 `code=1002 Invalid parameter format`，**而不是**明确的「超出范围」。调用方**无法从错误码区分「日期写错」与「历史不够」**——这与项目「失败必须可见」的原则冲突，若接入必须自己维护最早日边界，不能依赖错误码。

3. **`limit-up-statistics` 端点静默忽略 `date_ms`（fail-open）。** 传 `2025-11-07`、`2026-06-01`、`2026-11-20`，返回的 `trade_date` **一律为 `2026-11-20`**。该端点带 `limit_up_stock_count` / `touched_limit_up_stock_count` / `seal_rate` / `open_stock_count` 等聚合字段，看起来非常适合直接当 M1，但**不能用于取历史**——它不会报错，只会安静地返回当天值。

4. **两个端点的涨停口径不一致，接入前必须定口径。** 同一天 `2026-11-20`：`statistics.limit_up_stock_count = 28`，而 `limit-up-pool` 的 `pagination.total = 30`，**相差 2**（可能涉及 ST / 新股 / 北交所过滤）。两者不可混用，也不可互相校验而不先对齐定义。

5. **`seal_money` 字段完整性没有问题。** 最早可用日 `2025-11-07` 的 63 只涨停股，`seal_money` **无一为空**。字段可用性没问题，**问题只在历史长度**。

6. **`daily-k` dump 的真实跨度是 10 年，不是 5 年。** 下载 182.8 MB parquet 实测：**2016-11-21 → 2026-11-19，2428 个交易日，5580 只股票，10,408,334 行**；列为 `thscode, currency, interval, adjusted, date_ms, open_price, high_price, low_price, close_price, volume, turnover`，`adjusted` 为 `none`（未复权）。审计文件里的「5 自然年（1211 根）」指的是逐 thscode 的 quotes 接口，两者不是一回事。**这意味着「用日 K 自行推导 M1」在历史深度上完全可行**（2428 ≫ 1260），瓶颈只剩 D.1 的 8 类规则失败模式。

**实测对结论的修正**：本节的 251 个交易日取代此前所有「约 416 自然日 ≈ 297 交易日」的表述——后者是未二分核实的估计，已作废。`docs/adr/0005-fuyao-primary-source.md` 与 `docs/audits/full-review-2026-11-18.md` 均已按 251 更正。

---

## 已实现：M1 校验器（2026-11-23）

按决策建议第 3 条落地。**只读**：不写库、不推送、不改历史。

| 位置 | 职责 |
|---|---|
| `fgi/analysis/limit_up.py` | 纯规则：板块与比例、涨停价（四舍五入到分）、逐日聚合、容差比对与判定。无 I/O、不依赖 pyarrow |
| `scripts/validate_m1.py` | 取数（下载/缓存 dump）、读 DB、出 Markdown + JSON 报告、退出码 |
| `tests/unit/test_limit_up_rules.py` | 33 例，随 `[dev]` 在 CI 跑 |
| `.github/workflows/validate_m1.yml` | 每周一 03:00 UTC（11:00 北京时间）自动跑；失败只在同一 issue 上追加评论 |

### 实测结果

| 范围 | 容差内 | 平均偏差 | 备注 |
|---|---|---|---|
| 近 120 个交易日 | **114/120 = 95.0%** | +2.05 家 | 周检窗口，结论 PASS |
| 2018-01-01 ~ 2026-11-19（1751 日） | **1671/1751 = 95.4%** | +0.41 家 | 审计模式，结论 FAIL（19 个 fail 日） |

容差 `max(5, 8% × stored)`，fail 阈值 10 家。判定只在三种情况下报警：最近 5 个交易日内出现 fail 日、窗口内 fail 日累计 ≥3、连续 3 天同向超容差——**单个久远的 fail 日只进报告**，否则 120 天窗口里一次历史异常会让周检连挂三个月。

### 19 个 fail 日的分布（8.7 年）

- **2024-09-30（推导 826 / 存储 659，+167）与 2024-10-08（801 / 716，+85）**：极端行情日，两源口径差异被放大。
- **2019 年 3 天为负偏差**（−10 ~ −15），是全期仅有的「推导少于存储」。
- **2020 年 7-9 月 7 天正偏差**（+10 ~ +16）。
- **2026-07-08（+10）、2026-07-23（+13）**：主板 ST 改为 10% 之后。

### 两个必须记住的实现细节

1. **`date_ms` 是上海零点**（`ms % 86_400_000 == 57_600_000`）。按 UTC 解析会让整条序列错一天，产生 ±75 家的假偏差——第一次探针就踩了这个坑。代码里统一走 `to_shanghai_dates()`，并有单测钉死。
2. **2026-07-06 的 ST 规则断点会造成台阶式偏差**。实测该日前后（主板闭市价恰好等于 +5% 的股票数从 26 跌到 5 以下，`up10` 不变），说明 ST 股此前被两源同时排除、此后只能被本模块计入。偏差因此从 **+0~5 抬到 +8~13**，属结构性、不可消除，已在容差里吸收。

### 边界（明确不做）

- **不做 S3**：封单额是未成交委托，日 K 里不存在（D.2）。
- **不做区间外的历史修复**：2017 年源数据不全，对比不可信，报告里已注明「请从 2018 年起看趋势」。
- **不做自动修复**：校验器只报告，不改 `raw_data`。
