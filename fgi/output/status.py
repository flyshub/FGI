"""daily_status 全链路记录：daily_run 与 backfill 共用。"""


def record_indicator_status(db, date: str, indicator_results: dict):
    """把 calculator.run() 返回的每个指标当日状态写入 daily_status 表。

    状态值：normal / missing / degraded / substituted（异常时可能为 error）。

    #51: calculator 内部已经通过 upsert_status 写入了完整 source（如 'akshare',
    'database', 'forward_fill'）。calculator.run() 返回的 dict 通常不含 source，
    所以这里只在 calculator 明确返回 source 时才覆盖，避免清空 calculator 已写的值。
    """
    for name, r in (indicator_results or {}).items():
        if not isinstance(r, dict):
            r = {}
        source = r.get("source") or ""
        error = r.get("error", "") or ""
        if source:
            db.upsert_status(
                date, name,
                r.get("status", "missing"),
                source,
                error,
            )
        else:
            # 用一个不覆盖 source 的轻量写入：只更新 status/error
            db.upsert_status_keep_source(
                date, name,
                r.get("status", "missing"),
                error,
            )
    db.commit()
