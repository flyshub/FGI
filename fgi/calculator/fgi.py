import copy
from datetime import datetime, timedelta

import pandas as pd

from fgi.collector.fallback import DataSourceManager, INDICATOR_RAW_KEY
from fgi.storage.database import Database
from fgi.config.settings import MISSING_DAY_LIMIT
from fgi.common.utils import (calculate_fgi, apply_consistency_adjustment,
                              adjust_fgi_with_mad_pct, rolling_percentile,
                              calculate_health_score,
                              extract_indicator_score)
from fgi.calculator.momentum.m1 import M1Calculator
from fgi.calculator.momentum.m2 import M2Calculator
from fgi.calculator.momentum.m3 import M3Calculator
from fgi.calculator.momentum.m4 import M4Calculator
from fgi.calculator.sentiment.s2 import S2Calculator
from fgi.calculator.sentiment.s3 import S3Calculator
from fgi.calculator.valuation.v1 import V1Calculator
from fgi.calculator.valuation.v2 import V2Calculator
from fgi.calculator.funding.f1 import F1Calculator
from fgi.calculator.funding.f2 import F2Calculator
from fgi.calculator.funding.f3 import F3Calculator


INDICATOR_WEIGHTS = {
    "momentum": {"M1": 0.25, "M2": 0.25, "M3": 0.25, "M4": 0.25},
    # #49: M1/S3 长期结构性高相关（同源涨停家数/封单），原 0.85 动态阈值 57% 触发，
    # 退化为抖动开关造成 FGI 因权重切换跳变。改静态 S2=0.75/S3=0.25。
    "sentiment": {"S2": 0.75, "S3": 0.25},
    "valuation": {"V1": 0.50, "V2": 0.50},
    "funding": {"F1": 0.3333, "F2": 0.3333, "F3": 0.3334},
}

DIMENSION_WEIGHTS = {
    "momentum": 0.25,
    "sentiment": 0.25,
    "valuation": 0.25,
    "funding": 0.25,
}


class FGICalculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._calculators = {
            "M1": M1Calculator(data_manager, db),
            "M2": M2Calculator(data_manager, db),
            "M3": M3Calculator(data_manager, db),
            "M4": M4Calculator(data_manager, db),
            "S2": S2Calculator(data_manager, db),
            "S3": S3Calculator(data_manager, db),
            "V1": V1Calculator(data_manager, db),
            "V2": V2Calculator(data_manager, db),
            "F1": F1Calculator(data_manager, db),
            "F2": F2Calculator(data_manager, db),
            "F3": F3Calculator(data_manager, db),
        }

    def run_all_indicators(self, date: str) -> dict:
        results = {}
        for name, calc in self._calculators.items():
            try:
                result = calc.run(date)
                # 正常计算成功时，source_date 就是当天
                if "source_date" not in result:
                    result["source_date"] = date
                results[name] = result
            except Exception as e:
                print(f"[WARN] calculator {name} failed for {date}: {type(e).__name__}: {e}")
                results[name] = {"score": None, "status": "error", "source_date": None, "error": str(e)}
        return results

    @staticmethod
    def _extract_score(result: dict, name: str):
        """Extract an indicator score; only None/NaN count as missing (0.0 is valid)."""
        return extract_indicator_score(result, name)

    def calculate_dimension_score(self, indicator_results: dict, dimension: str,
                                  weights: dict = None):
        weights = (weights or INDICATOR_WEIGHTS)[dimension]
        scores = []
        for ind, weight in weights.items():
            result = indicator_results.get(ind, {})
            score = self._extract_score(result, ind)
            if score is not None:
                scores.append((score, weight))
        if not scores:
            return None
        total_weight = sum(w for _, w in scores)
        weighted_sum = sum(s * w for s, w in scores)
        return weighted_sum / total_weight

    def calculate_health(self, indicator_results: dict, date: str = None) -> float:
        statuses = []
        for name in indicator_results:
            r = indicator_results[name]
            statuses.append({
                "indicator": name,
                "status": r.get("status", "missing"),
            })
        if not statuses:
            return 0
        # #49: correlation_exceed_rate 已废弃 — M1/S3 高相关是同源结构性事实，
        # 已通过 INDICATOR_WEIGHTS 静态降权解决，不再惩罚 health_score。
        return calculate_health_score(pd.DataFrame(statuses), 0.0)

    def _apply_forward_fill(self, indicator_results: dict, date: str):
        """指标当日无得分时，用最近 MISSING_DAY_LIMIT 个交易日内
        最后有效得分填充。elapsed=1 时（T+1 延迟）标记 'normal'（数据尚未发布），
        elapsed>=2 且 <=MISSING_DAY_LIMIT 时标记 'degraded'（数据源故障或长期缺失）。

        注意：填充值只写入内存中的 indicator_results 供当日 FGI 聚合使用，
        不落库 scores_daily —— 否则次日会把填充值误当真实得分，elapsed 永远
        重置为 1，「连续缺失 10 日剔除」永不触发。"""
        start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=MISSING_DAY_LIMIT * 3 + 10)).strftime("%Y-%m-%d")
        history = self._db.get_scores(start, date)
        if history is None or history.empty:
            return
        trade_days = sorted(history["date"].tolist())
        for name, result in indicator_results.items():
            if self._extract_score(result, name) is not None:
                continue
            if name not in history.columns:
                continue
            valid = history[history["date"] < date][["date", name]].dropna()
            if valid.empty:
                continue
            last_date = valid["date"].iloc[-1]
            last_score = float(valid[name].iloc[-1])
            elapsed = sum(1 for d in trade_days if last_date < d <= date)
            if date not in trade_days:
                elapsed += 1
            if not (1 <= elapsed <= MISSING_DAY_LIMIT):
                continue
            result["score"] = last_score
            result["source_date"] = self._resolve_source_date(name, last_date, date)
            result["status"] = "normal" if elapsed == 1 else "degraded"
            self._db.upsert_status(date, name.lower(), result["status"], "forward_fill",
                                   f"filled from {last_date} (elapsed={elapsed})")
        self._db.commit()

    def _resolve_source_date(self, indicator: str, last_score_date: str, target_date: str) -> str:
        """traces one hop back from the last score date to the actual raw-data date."""
        raw_key = INDICATOR_RAW_KEY.get(indicator)
        if raw_key is None:
            return last_score_date
        try:
            d = self._db.get_latest_raw_date(raw_key, last_score_date)
            if d:
                return d
        except Exception as e:
            print(f"[WARN] _resolve_source_date failed for {indicator} ({raw_key}): {e}", flush=True)
        return last_score_date

    def run(self, date: str) -> dict:
        indicator_results = self.run_all_indicators(date)
        self._apply_forward_fill(indicator_results, date)

        # #49: 删除 M1/S3 动态 corr 检查 — 同源结构性高相关，0.85 阈值 57% 触发不稳定
        # INDICATOR_WEIGHTS 已静态化为 S2=0.75/S3=0.25
        weights = copy.deepcopy(INDICATOR_WEIGHTS)

        dimension_scores = {}
        for dim in DIMENSION_WEIGHTS:
            dimension_scores[dim] = self.calculate_dimension_score(
                indicator_results, dim, weights
            )

        raw_fgi = calculate_fgi(dimension_scores)

        all_scores = []
        for name, r in indicator_results.items():
            s = self._extract_score(r, name)
            if s is not None:
                all_scores.append(float(s))

        _, mad = apply_consistency_adjustment(raw_fgi, all_scores)
        self._db.upsert_raw_data(date, "mad", float(mad))
        self._db.commit()

        fgi_final = raw_fgi
        if raw_fgi is not None:
            mad_history = self._db.get_raw_data("mad", "2015-01-01", date)
            if len(mad_history) > 252:
                mad_series = pd.Series(mad_history["value"].values, index=mad_history["date"])
                mad_pct = rolling_percentile(mad_series, window=1260)
                mad_pct_val = mad_pct.iloc[-1]
                if not pd.isna(mad_pct_val):
                    fgi_final = adjust_fgi_with_mad_pct(raw_fgi, mad, float(mad_pct_val))

        health = self.calculate_health(indicator_results, date)

        scores = {
            "FGI_raw": raw_fgi,
            "FGI_final": fgi_final,
            "FGI_legacy": raw_fgi,
            "FGI_current": fgi_final,
            "health_score": health,
        }

        self._db.upsert_score(date, scores)
        self._db.commit()

        return {
            "date": date,
            "dimension_scores": dimension_scores,
            "fgi_raw": raw_fgi,
            "fgi_final": fgi_final,
            "health_score": health,
            "indicator_results": indicator_results,
        }
