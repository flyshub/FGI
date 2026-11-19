"""退出码表示「目标日期是否已经算过」，供兜底 cron 决定要不要重跑。

用法（GitHub Actions 里）：
    python -m fgi.output.check_ran            # 读 $TARGET_DATE
    python -m fgi.output.check_ran --date 2026-11-19

退出码：0 = 已有当日分数（可以跳过）；1 = 还没有（需要跑）；2 = 无法判断（当作需要跑）。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from fgi.config.settings import DB_PATH
from fgi.storage.database import Database


def has_score(date: str, db_path=DB_PATH) -> bool:
    with Database(db_path) as db:
        rows = db.get_scores(date, date)
    if rows is None or rows.empty:
        return False
    value = rows.iloc[0].get("FGI_final")
    # NaN 不算算过
    return bool(value is not None and value == value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a date already has an FGI score")
    parser.add_argument("--date", default=os.environ.get("TARGET_DATE"))
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    date = args.date or datetime.now().strftime("%Y-%m-%d")
    try:
        done = has_score(date, args.db)
    except Exception as exc:  # 库不存在/读不了 → 让调用方照常跑
        print(f"cannot determine ({exc}); assuming not computed", file=sys.stderr)
        return 2

    print(f"{date}: {'already computed' if done else 'not computed'}")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
