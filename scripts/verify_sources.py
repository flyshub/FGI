"""数据源连通性检查：装配结果 + 真实取一次数。

用于 Key 轮换后、或怀疑上游端点变更时确认链路。不写库、不推送、不改历史。

本地：
    export FUYAO_API_KEY=...   # 或用 .env
    python scripts/verify_sources.py

CI：
    .github/workflows/verify_sources.yml（手动触发）

任何一项不达标即 exit 1——失败必须可见。
"""

from __future__ import annotations

import logging
import os
import sys

from fgi.collector.factory import setup_data_manager

# 用一个已收盘的最近交易日区间，避免「当日数据还没出」被误判为失败。
RANGE_START = "2026-11-01"
RANGE_END = "2026-11-20"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not os.getenv("FUYAO_API_KEY", "").strip():
        print("FUYAO_API_KEY 未设置：只能验证 akshare 链路", file=sys.stderr)
        return 1

    manager = setup_data_manager()
    print(f"sources: {sorted(manager._sources)}")

    failures: list[str] = []

    if "fuyao" not in manager._sources:
        failures.append("fuyao 未注册（Key 未读到或构造失败）")
    else:
        for indicator, method, args in (
            ("m3_index", "fetch_index_daily", ("sh000001", RANGE_START, RANGE_END)),
            ("m4_cyb_volume", "fetch_cyb_daily", (RANGE_START, RANGE_END)),
            ("f3_index", "fetch_index_daily", ("sh000001", RANGE_START, RANGE_END)),
        ):
            chain = manager._chains[indicator]
            order = [type(s).__name__ for s in chain._sources]
            result = manager.fetch(indicator, method, *args)
            rows = 0 if result.data is None else len(result.data)
            print(
                f"{indicator:15s} chain={order} → {result.status.value} src={result.source} rows={rows}"
            )
            if result.source != "fuyao":
                failures.append(
                    f"{indicator} 首源未命中 fuyao（src={result.source}）：{result.error}"
                )
            elif result.status.value != "healthy":
                failures.append(f"{indicator} 状态 {result.status.value}：{result.error}")

    if failures:
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("OK 三个指标均由 fuyao 取到健康数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
