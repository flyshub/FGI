import logging
from datetime import datetime, timedelta
from typing import Any

import requests

from fgi.config.settings import ANOMALY_PERCENTILE, DB_PATH, WEBHOOK_URL
from fgi.storage.database import Database

logger = logging.getLogger(__name__)

# 异常检测窗口：最近 5 年约 1260 个交易日的 |ΔFGI|
ANOMALY_WINDOW_TRADING_DAYS = 5 * 252
# 1260 个交易日约合 7 个自然年，查询放宽到 8 年自然日确保覆盖
QUERY_LOOKBACK_DAYS = 365 * 8


class Alert:
    def __init__(self, db_path=None):
        self.webhook_url = WEBHOOK_URL
        self.anomaly_percentile = ANOMALY_PERCENTILE
        self.db_path = db_path

    def check_and_alert(self, date: str, fgi_result: dict[str, Any]) -> bool:
        """异常检测为真时记 warning 并推送（PushPlus / Webhook），不打断写入。返回是否异常。"""
        is_anomaly, prev_fgi, delta, threshold = self._analyze(date, fgi_result)
        if not is_anomaly:
            return False
        logger.warning(f"FGI anomaly detected on {date}: {fgi_result.get('fgi_final')}")
        message = self._build_alert_message(date, fgi_result, prev_fgi, delta, threshold)
        if self.webhook_url:
            self._send_webhook(message)
        try:
            from fgi.output.pushplus import send_alert

            send_alert(f"FGI 异常告警 · {date}", message)
        except Exception as e:
            logger.error(f"PushPlus alert skipped: {e}")
        return True

    def _analyze(
        self, date: str, fgi_result: dict[str, Any]
    ) -> tuple[bool, float | None, float | None, float | None]:
        """spec line 263: |ΔFGI| over rolling 5y 99-percentile = anomaly → suppress push.

        返回 (是否异常, 前日 FGI, |ΔFGI|, 阈值)；检测自身失败时保守视为异常，
        上下文数值为 None（消息中省略复核行）。
        """
        try:
            db_path = self.db_path or DB_PATH
            with Database(db_path) as db:
                lookback = (
                    datetime.strptime(date, "%Y-%m-%d") - timedelta(days=QUERY_LOOKBACK_DAYS)
                ).strftime("%Y-%m-%d")
                df = db.get_scores(lookback, date)
                # 排除当日，用最近 1260 个交易日的历史 |ΔFGI| 的 99% 分位做阈值
                hist = df[df["date"] < date] if not df.empty else df
                fgi_changes = (
                    hist["FGI_final"].diff().abs().dropna().tail(ANOMALY_WINDOW_TRADING_DAYS)
                )
                today_fgi = fgi_result.get("fgi_final")
                if not fgi_changes.empty and today_fgi is not None:
                    prev_fgi = float(hist["FGI_final"].dropna().iloc[-1])
                    threshold = float(fgi_changes.quantile(self.anomaly_percentile / 100))
                    delta = float(today_fgi) - prev_fgi  # 带符号，便于复核判断方向
                    if abs(delta) > threshold:
                        return True, prev_fgi, delta, threshold

            return False, None, None, None
        except Exception:
            # 异常检测自身失败 → 保守原则：视为已检测到异常，暂停推送并通过
            # webhook/PushPlus 通知运维（spec L343: 暂停自动发布）
            logger.exception("Anomaly detection failed, treating as anomaly")
            return True, None, None, None

    def _build_alert_message(
        self,
        date: str,
        fgi_result: dict[str, Any],
        prev_fgi: float | None,
        delta: float | None,
        threshold: float | None,
    ) -> str:
        fgi_final = fgi_result.get("fgi_final", 0)
        health_score = fgi_result.get("health_score", 0)

        message = f"FGI anomaly alert for {date}\n"
        message += "|ΔFGI| exceeded the rolling 5y 99-percentile.\n"
        message += f"FGI Final: {fgi_final:.2f}\n"
        if prev_fgi is not None and delta is not None and threshold is not None:
            message += f"Prev FGI: {prev_fgi:.2f}\n"
            message += f"ΔFGI: {delta:+.2f}\n"
            message += f"Threshold (99th pct): {threshold:.2f}\n"
        message += f"Health Score: {health_score:.2f}\n"
        message += "\nPush suppressed, manual review required (spec line 262)."

        return message.strip()

    def _send_webhook(self, message: str):
        """统一发送 webhook 文本消息（wecom 和 dingtalk 使用相同的 msgtype:text 格式）。"""
        self._send_webhook_text(message)

    def _send_webhook_text(self, message: str):
        try:
            data = {"msgtype": "text", "text": {"content": message}}
            response = requests.post(self.webhook_url, json=data, timeout=10)
            response.raise_for_status()
        except Exception as e:
            logger.error("Webhook send failed: %s", e)
