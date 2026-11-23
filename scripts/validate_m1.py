"""M1 校验器：用全市场日 K 推导涨停家数，与 raw_data 里 levistock 的值比对。

只读：不写数据库、不推送、不改历史。规则见 fgi/analysis/limit_up.py 的模块
注释（含时区陷阱与已知的 +2 家系统偏差）。

用法：
    pip install -e ".[dump]"            # 需要 pyarrow 读 parquet
    export FUYAO_API_KEY=...            # 仅首次下载 dump 时需要
    python scripts/validate_m1.py                       # 最近 120 个交易日
    python scripts/validate_m1.py --days 365 --report report.md
    python scripts/validate_m1.py --start 2017-06-02 --end 2026-11-19
    python scripts/validate_m1.py --dump /path/daily_k.parquet   # 跳过下载

退出码：0 通过 / 1 判定失败（有 fail 日或连续同向超容差）/ 2 数据不可用。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from fgi.analysis import limit_up as lu
from fgi.storage.database import Database

INDICATOR = "m1_zt_count"
DUMP_URL = "https://fuyao.aicubes.cn/api/dump/market-dumps/daily-k/download-url"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "fgi" / "dumps"
CACHE_FILE = "daily_k.parquet"
CACHE_TTL_HOURS = 24
DETAIL_STOCK_CAP = 50

SHANGHAI = ZoneInfo(lu.SHANGHAI_TZ)


def _shanghai_midnight_ms(day: dt.date) -> int:
    return int(dt.datetime(day.year, day.month, day.day, tzinfo=SHANGHAI).timestamp() * 1000)


# ── dump 获取 ────────────────────────────────────────────


def _download_dump(target: Path) -> None:
    key = os.getenv("FUYAO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FUYAO_API_KEY 未设置，无法下载 dump；或用 --dump 指定本地文件")

    logging.info("请求 dump 签名 URL")
    resp = requests.get(DUMP_URL, headers={"X-api-key": key}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"签名失败: code={payload.get('code')} {payload.get('message')}")

    url = payload["data"]["presigned_url"]
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    logging.info("下载 daily-k dump → %s", target)
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.replace(target)
    logging.info("下载完成：%.1f MB", target.stat().st_size / 1e6)


def ensure_dump(path: str | None, cache_dir: Path, refresh: bool) -> Path:
    if path:
        p = Path(path).expanduser()
        if not p.exists():
            raise RuntimeError(f"--dump 指向的文件不存在：{p}")
        return p

    cached = cache_dir / CACHE_FILE
    if cached.exists() and not refresh:
        age_h = (dt.datetime.now().timestamp() - cached.stat().st_mtime) / 3600
        if age_h <= CACHE_TTL_HOURS:
            logging.info("使用缓存 %s（%.1f 小时前）", cached, age_h)
            return cached

    _download_dump(cached)
    return cached


# ── 数据装载 ────────────────────────────────────────────


def load_bars(parquet_path: Path, start: dt.date, end: dt.date, buffer_days: int) -> pd.DataFrame:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise RuntimeError('缺少 pyarrow，请执行 pip install -e ".[dump]"') from exc

    lo = _shanghai_midnight_ms(start - dt.timedelta(days=buffer_days))
    hi = _shanghai_midnight_ms(end)
    table = pq.read_table(
        parquet_path,
        columns=["thscode", "date_ms", "close_price"],
        filters=[("date_ms", ">=", lo), ("date_ms", "<=", hi)],
    )
    bars = table.to_pandas()
    bars["date"] = lu.to_shanghai_dates(bars["date_ms"])
    bars = bars.drop(columns=["date_ms"])
    logging.info(
        "载入 %d 根 K 线：%s ~ %s，%d 只股票",
        len(bars),
        bars["date"].min(),
        bars["date"].max(),
        bars["thscode"].nunique(),
    )
    return bars


def load_stored(start: dt.date, end: dt.date, db_path: str | None = None) -> pd.Series:
    with Database(Path(db_path).expanduser() if db_path else None) as db:
        frame = db.raw_data.get_raw_data(INDICATOR, start.isoformat(), end.isoformat())
    if frame.empty:
        return pd.Series(dtype="float64")
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    series = frame.set_index("date")["value"].astype("float64").sort_index()
    logging.info("载入 %d 个已存值：%s ~ %s", len(series), series.index.min(), series.index.max())
    return series


# ── 报告 ────────────────────────────────────────────────


def _top_days(cmp: pd.DataFrame, n: int) -> pd.DataFrame:
    return cmp.reindex(cmp["delta"].abs().sort_values(ascending=False).index).head(n)


def render_report(
    cmp: pd.DataFrame,
    summary: dict[str, object],
    hits: pd.DataFrame,
    *,
    start: dt.date,
    end: dt.date,
    detail_days: int,
    abs_tolerance: int,
    rel_tolerance: float,
) -> str:
    verdict = str(summary["verdict"]).upper()
    lines = [
        "# M1 涨停家数校验报告",
        "",
        f"- 区间：{start} ~ {end}",
        f"- 参与比对：{summary['days']} 个交易日",
        f"- 结论：**{verdict}**",
        f"- 容差：max({abs_tolerance}, {rel_tolerance:.0%} × stored)，"
        f"fail 阈值 {lu.DEFAULT_FAIL_DELTA} 家",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 完全相等 | {summary['equal_days']} 天 |",
        f"| 容差内（ok） | {summary['ok_days']} 天 |",
        f"| 超容差未报警（warn） | {summary['warn_days']} 天 |",
        f"| 报警（fail） | {summary['fail_days']} 天（最近 {lu.DEFAULT_RECENT_DAYS} 日 "
        f"{summary['recent_fail_days']} 天） |",
        f"| 平均偏差 | {summary['mean_delta']:+.2f} 家 |",
        f"| 偏差范围 | {summary['min_delta']:+d} ~ {summary['max_delta']:+d} 家 |",
        f"| 最长连续同向超容差 | {summary['max_consecutive']} 天 |",
        "",
        "> 系统性偏差（约 +2 家，2026-07-06 主板 ST 改为 10% 后抬到 +8~13）属已知现象。"
        "判定看的是偏差是否突然变大，不是是否为零。2017 年的对比不可信（源数据不全）。",
        "",
        "## 偏差最大的 10 天",
        "",
        "| 日期 | 推导 | 存储 | 偏差 | 状态 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for day, row in _top_days(cmp, 10).iterrows():
        lines.append(
            f"| {day} | {int(row['derived'])} | {int(row['stored'])} | "
            f"{int(row['delta']):+d} | {row['status']} |"
        )

    if detail_days > 0 and not cmp.empty:
        lines += ["", f"## 明细：偏差最大的 {detail_days} 天", ""]
        for day, row in _top_days(cmp, detail_days).iterrows():
            day_hits = hits[(hits["date"] == day) & hits["is_limit_up"]].sort_values("thscode")
            codes = list(day_hits["thscode"])
            shown = ", ".join(codes[:DETAIL_STOCK_CAP])
            more = "" if len(codes) <= DETAIL_STOCK_CAP else f" …（共 {len(codes)} 只）"
            lines += [
                f"### {day}（推导 {int(row['derived'])} / 存储 {int(row['stored'])}，"
                f"偏差 {int(row['delta']):+d}）",
                "",
                f"计为涨停：{shown}{more}",
                "",
            ]

    return "\n".join(lines)


# ── 主流程 ──────────────────────────────────────────────


def _resolve_range(args: argparse.Namespace, stored: pd.Series) -> tuple[dt.date, dt.date]:
    end = (
        dt.date.fromisoformat(args.end)
        if args.end
        else (max(stored.index) if not stored.empty else dt.date.today())
    )
    if args.start:
        start = dt.date.fromisoformat(args.start)
    else:
        # 用已存值反推 N 个交易日，避免自然日与交易日混淆
        in_range = stored[stored.index <= end]
        if len(in_range) > args.days:
            start = in_range.index[-args.days]
        else:
            start = in_range.index[0] if len(in_range) else end - dt.timedelta(days=args.days * 2)
    return start, end


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 M1 涨停家数（日 K 推导 vs raw_data）")
    parser.add_argument("--start", help="起始日 YYYY-MM-DD（默认由 --days 反推）")
    parser.add_argument("--end", help="结束日 YYYY-MM-DD（默认取库内最新日期）")
    parser.add_argument(
        "--days", type=int, default=120, help="未给 --start 时回看的交易日数（默认 120）"
    )
    parser.add_argument("--db", help="SQLite 路径（默认 data/fgi.db；测试可用副本）")
    parser.add_argument("--dump", help="本地 daily-k parquet 路径（跳过下载）")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="dump 缓存目录")
    parser.add_argument("--refresh", action="store_true", help="强制重新下载 dump")
    parser.add_argument(
        "--buffer-days", type=int, default=90, help="起始日前多读的自然日数（默认 90）"
    )
    parser.add_argument("--abs-tolerance", type=int, default=lu.DEFAULT_ABS_TOLERANCE)
    parser.add_argument("--rel-tolerance", type=float, default=lu.DEFAULT_REL_TOLERANCE)
    parser.add_argument(
        "--detail-days", type=int, default=3, help="报告里列出差异股票的天数（0 关闭）"
    )
    parser.add_argument("--report", help="把 Markdown 报告写到该路径")
    parser.add_argument("--json", help="把汇总写到该 JSON 路径")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        stored_all = load_stored(dt.date(2005, 1, 1), dt.date.today(), args.db)
        if stored_all.empty:
            print(f"raw_data 里没有 {INDICATOR}：无法校验", file=sys.stderr)
            return 2
        start, end = _resolve_range(args, stored_all)
        stored = stored_all[(stored_all.index >= start) & (stored_all.index <= end)]

        dump = ensure_dump(args.dump, Path(args.cache_dir).expanduser(), args.refresh)
        bars = load_bars(dump, start, end, args.buffer_days)
    except Exception as exc:  # noqa: BLE001 - 数据不可用统一 exit 2
        print(f"数据不可用：{exc}", file=sys.stderr)
        return 2

    hits = lu.annotate_limit_ups(bars)
    derived = lu.derive_daily_counts(bars)
    derived = derived[(derived.index >= start) & (derived.index <= end)]

    cmp = lu.compare_counts(
        derived,
        stored,
        abs_tolerance=args.abs_tolerance,
        rel_tolerance=args.rel_tolerance,
    )
    if cmp.empty:
        print("推导值与存储值没有重叠日期：无法校验", file=sys.stderr)
        return 2

    summary = lu.assess(cmp)
    report = render_report(
        cmp,
        summary,
        hits,
        start=start,
        end=end,
        detail_days=args.detail_days,
        abs_tolerance=args.abs_tolerance,
        rel_tolerance=args.rel_tolerance,
    )

    print(report)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        logging.info("报告已写入 %s", args.report)
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"start": str(start), "end": str(end), **summary}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )

    if summary["verdict"] == "fail":
        print(
            f"判定失败：fail {summary['fail_days']} 天，"
            f"最长连续同向超容差 {summary['max_consecutive']} 天",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
