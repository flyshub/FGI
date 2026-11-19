"""重构安全网：离线重放一段时间的评分，快照下来，重构后再跑一遍做逐字段比对。

为什么需要它：P2-4（状态写入收敛）与 P2-1（指标骨架去重）会动全部 12 个指标，
一旦算错是静默的错数据。唯一可信的验收标准是「同一段原始数据，重构前后算出来的
每一天、每个指标、每个状态完全相同」。

用法：
    # 重构前：生成基线（不改动 data/fgi.db，内部用副本）
    python scripts/golden_replay.py --start 2025-01-01 --end 2026-11-19 --save /tmp/base.json

    # 重构后：比对
    python scripts/golden_replay.py --start 2025-01-01 --end 2026-11-19 --save /tmp/after.json \
        --compare /tmp/base.json

退出码：一致 0；有差异 1（并打印前 40 条差异）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 强制离线：从 raw_data 重建，杜绝网络让两次运行不可比
os.environ["FGI_OFFLINE"] = "1"

from fgi.calculator.fgi import FGICalculator  # noqa: E402
from fgi.collector.factory import setup_data_manager  # noqa: E402
from fgi.collector.trading_calendar import resolve_trading_days  # noqa: E402
from fgi.config.settings import DB_PATH  # noqa: E402
from fgi.storage.database import Database  # noqa: E402

FLOAT_TOL = 1e-9


def _norm_note(note: str | None) -> str | None:
    """fetched_at 是运行时刻，不参与等价性比较。"""
    if not note:
        return note
    parts = [p for p in str(note).split(";") if not p.strip().startswith("fetched_at=")]
    return ";".join(parts).strip() or None


def _snapshot_round(value):
    if isinstance(value, float) and value != value:  # NaN
        return "NaN"
    return value


def replay(start: str, end: str, db_path: Path) -> dict:
    with Database(db_path) as db:
        db.init_schema()
        dates = resolve_trading_days(start, end, db=db)
        manager = setup_data_manager()
        manager.set_db(db)
        calculator = FGICalculator(manager, db)

        rows: dict[str, dict] = {}
        t0 = time.time()
        for i, date in enumerate(dates, 1):
            result = calculator.run(date)

            statuses = db.get_status(date)
            status_map = {}
            if statuses is not None and not statuses.empty:
                for _, row in statuses.iterrows():
                    status_map[str(row["indicator"])] = {
                        "status": row.get("status"),
                        "source": row.get("source"),
                        "note": _norm_note(row.get("note")),
                    }

            scores = db.get_scores(date, date)
            score_row = {}
            if scores is not None and not scores.empty:
                for col, val in scores.iloc[0].items():
                    if col == "date":
                        continue
                    score_row[col] = _snapshot_round(val)

            indicators = {}
            for name, r in sorted(result.get("indicator_results", {}).items()):
                if not isinstance(r, dict):
                    continue
                indicators[name] = {
                    "status": r.get("status"),
                    "source_date": r.get("source_date"),
                    "score": _snapshot_round(r.get("score", r.get(name.lower(), r.get(name)))),
                }

            rows[date] = {
                "scores": score_row,
                "statuses": status_map,
                "indicators": indicators,
                "fgi_final": _snapshot_round(result.get("fgi_final")),
                "fgi_raw": _snapshot_round(result.get("fgi_raw")),
                "health": _snapshot_round(result.get("health_score")),
            }
            if i % 50 == 0:
                print(f"  {i}/{len(dates)} ({time.time() - t0:.0f}s)", flush=True)

    return {
        "meta": {
            "start": start,
            "end": end,
            "days": len(rows),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "rows": rows,
    }


def _diff_values(path: str, a, b, out: list[str]) -> None:
    if isinstance(a, float) and isinstance(b, float):
        if abs(a - b) > FLOAT_TOL:
            out.append(f"{path}: {a!r} != {b!r}")
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            _diff_values(f"{path}.{key}", a.get(key), b.get(key), out)
        return
    if a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def compare(base: dict, after: dict, limit: int = 40) -> list[str]:
    diffs: list[str] = []
    base_rows, after_rows = base["rows"], after["rows"]
    for date in sorted(set(base_rows) | set(after_rows)):
        if date not in base_rows:
            diffs.append(f"{date}: 新增日期（基线没有）")
            continue
        if date not in after_rows:
            diffs.append(f"{date}: 缺失日期（基线有）")
            continue
        _diff_values(date, base_rows[date], after_rows[date], diffs)
        if len(diffs) >= limit * 10:
            break
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline golden replay for score refactors")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--save", required=True, help="快照输出路径")
    parser.add_argument("--compare", help="与已有快照比对")
    parser.add_argument("--db", default=str(DB_PATH), help="源数据库（会复制，不就地修改）")
    args = parser.parse_args()

    source = Path(args.db)
    if not source.exists():
        print(f"数据库不存在：{source}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "replay.db"
        shutil.copy2(source, work)
        snapshot = replay(args.start, args.end, work)

    Path(args.save).write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    print(f"快照已写入 {args.save}：{snapshot['meta']['days']} 个交易日")

    if not args.compare:
        return 0

    base = json.loads(Path(args.compare).read_text(encoding="utf-8"))
    diffs = compare(base, snapshot)
    if not diffs:
        print(
            f"一致：{snapshot['meta']['days']} 天逐字段相同（基线 {base['meta']['generated_at']}）"
        )
        return 0

    print(f"发现 {len(diffs)} 处差异（最多显示 40 条）：", file=sys.stderr)
    for line in diffs[:40]:
        print(f"  {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
