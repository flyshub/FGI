"""daily_status 全链路记录：daily_run 与 backfill 共用。"""


def record_indicator_status(db, date: str, indicator_results: dict):
    """把 calculator.run() 返回的每个指标当日状态写入 daily_status 表。

    状态值：normal / missing / degraded / substituted（异常时可能为 error）。
    """
    for name, r in (indicator_results or {}).items():
        if not isinstance(r, dict):
            r = {}
        db.upsert_status(
            date, name,
            r.get("status", "missing"),
            r.get("source", "") or "",
            r.get("error", "") or "",
        )
    db.commit()
