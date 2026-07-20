import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fgi.config.settings import WEBHOOK_URL, WEBHOOK_TYPE, ANOMALY_PERCENTILE, DB_PATH
from fgi.storage.database import Database


class Alert:
    def __init__(self, db_path=None):
        self.webhook_url = WEBHOOK_URL
        self.webhook_type = WEBHOOK_TYPE
        self.anomaly_percentile = ANOMALY_PERCENTILE
        self.db_path = db_path

    def check_and_alert(self, date: str, fgi_result: Dict[str, Any]):
        if not self.webhook_url:
            return

        if self._is_anomaly(date, fgi_result):
            message = self._build_alert_message(date, fgi_result)
            self._send_webhook(message)

    def _is_anomaly(self, date: str, fgi_result: Dict[str, Any]) -> bool:
        try:
            db_path = self.db_path or DB_PATH
            with Database(db_path) as db:
                lookback = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
                df = db.get_scores(lookback, date)
                if not df.empty and len(df) >= 2:
                    fgi_changes = df["FGI_final"].diff().abs().dropna()
                    if not fgi_changes.empty:
                        if len(fgi_changes) == 1:
                            if fgi_changes.iloc[-1] > 15:
                                return True
                        else:
                            threshold = fgi_changes.quantile(self.anomaly_percentile / 100)
                            if fgi_changes.iloc[-1] > threshold:
                                return True

            dimension_scores = fgi_result.get("dimension_scores", {})
            for dim, score in dimension_scores.items():
                if score is not None and (score > 85 or score < 15):
                    return True

            return False
        except Exception:
            return False

    def _build_alert_message(self, date: str, fgi_result: Dict[str, Any]) -> str:
        fgi_final = fgi_result.get("fgi_final", 0)
        health_score = fgi_result.get("health_score", 0)
        dimension_scores = fgi_result.get("dimension_scores", {})

        anomaly_indicators = []
        for dim, score in dimension_scores.items():
            if score > 85 or score < 15:
                anomaly_indicators.append(f"{dim}: {score:.1f}")

        message = f"FGI Alert for {date}\n"
        message += f"FGI Final: {fgi_final:.2f}\n"
        message += f"Health Score: {health_score:.2f}\n"

        if anomaly_indicators:
            message += "Anomaly Indicators:\n"
            for indicator in anomaly_indicators:
                message += f"  - {indicator}\n"

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
