import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fgi.config.settings import WEBHOOK_URL, WEBHOOK_TYPE, ANOMALY_PERCENTILE, DB_PATH
from fgi.storage.database import Database

logger = logging.getLogger(__name__)

# 异常检测窗口：最近 5 年约 1260 个交易日的 |ΔFGI|
ANOMALY_WINDOW_TRADING_DAYS = 5 * 252
# 1260 个交易日约合 7 个自然年，查询放宽到 8 年自然日确保覆盖
QUERY_LOOKBACK_DAYS = 365 * 8


class Alert:
    def __init__(self, db_path=None):
        self.webhook_url = WEBHOOK_URL
        self.webhook_type = WEBHOOK_TYPE
        self.anomaly_percentile = ANOMALY_PERCENTILE
        self.db_path = db_path

    def check_and_alert(self, date: str, fgi_result: Dict[str, Any]) -> bool:
        """异常检测为真时记 warning 并推送（PushPlus / Webhook），不打断写入。返回是否异常。"""
        if not self._is_anomaly(date, fgi_result):
            return False
        logger.warning(f"FGI anomaly detected on {date}: {fgi_result.get('fgi_final')}")
        message = self._build_alert_message(date, fgi_result)
        if self.webhook_url:
            self._send_webhook(message)
        try:
            from fgi.output.pushplus import send_alert
            send_alert(f"FGI 异常告警 · {date}", message)
        except Exception as e:
            logger.error(f"PushPlus alert skipped: {e}")
        return True

    def _is_anomaly(self, date: str, fgi_result: Dict[str, Any]) -> bool:
        """spec line 263: |ΔFGI| over rolling 5y 99-percentile = anomaly → suppress push."""
        try:
            db_path = self.db_path or DB_PATH
            with Database(db_path) as db:
                lookback = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=QUERY_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
                df = db.get_scores(lookback, date)
                # 排除当日，用最近 1260 个交易日的历史 |ΔFGI| 的 99% 分位做阈值
                hist = df[df["date"] < date] if not df.empty else df
                fgi_changes = hist["FGI_final"].diff().abs().dropna().tail(ANOMALY_WINDOW_TRADING_DAYS)
                today_fgi = fgi_result.get("fgi_final")
                if not fgi_changes.empty and today_fgi is not None:
                    prev_fgi = hist["FGI_final"].dropna().iloc[-1]
                    threshold = fgi_changes.quantile(self.anomaly_percentile / 100)
                    if abs(today_fgi - prev_fgi) > threshold:
                        return True

            return False
        except Exception:
            return False

    def _build_alert_message(self, date: str, fgi_result: Dict[str, Any]) -> str:
        fgi_final = fgi_result.get("fgi_final", 0)
        health_score = fgi_result.get("health_score", 0)

        message = f"FGI anomaly alert for {date}\n"
        message += f"|ΔFGI| exceeded the rolling 5y 99-percentile.\n"
        message += f"FGI Final: {fgi_final:.2f}\n"
        message += f"Health Score: {health_score:.2f}\n"
        message += f"\nPush suppressed, manual review required (spec line 262)."

        return message.strip()

    def _send_webhook(self, message: str):
        if self.webhook_type == "wecom":
            self._send_wecom(message)
        elif self.webhook_type == "dingtalk":
            self._send_dingtalk(message)

    def _send_wecom(self, message: str):
        try:
            data = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }
            response = requests.post(self.webhook_url, json=data, timeout=10)
            response.raise_for_status()
        except Exception:
            pass

    def _send_dingtalk(self, message: str):
        try:
            data = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }
            response = requests.post(self.webhook_url, json=data, timeout=10)
            response.raise_for_status()
        except Exception:
            pass
