from fgi.collector.fallback import DataSourceManager
from fgi.storage.database import Database
from fgi.config.settings import (
    HEALTHY_THRESHOLD, ANOMALY_PERCENTILE,
    FGI_EXTREME_HIGH, FGI_EXTREME_LOW, MISSING_DAY_LIMIT
)
from fgi.common.utils import calculate_fgi, apply_consistency_adjustment, adjust_fgi_with_mad_pct, rolling_percentile, calculate_health_score
from fgi.calculator.momentum.m1 import M1Calculator
from fgi.calculator.momentum.m2 import M2Calculator
from fgi.calculator.momentum.m3 import M3Calculator
from fgi.calculator.momentum.m4 import M4Calculator
from fgi.calculator.sentiment.s1 import S1Calculator
from fgi.calculator.sentiment.s2 import S2Calculator
from fgi.calculator.sentiment.s3 import S3Calculator
from fgi.calculator.valuation.v1 import V1Calculator
from fgi.calculator.valuation.v2 import V2Calculator
from fgi.calculator.funding.f1 import F1Calculator
from fgi.calculator.funding.f2 import F2Calculator
from fgi.calculator.funding.f3 import F3Calculator


INDICATOR_WEIGHTS = {
    "momentum": {"M1": 0.25, "M2": 0.25, "M3": 0.25, "M4": 0.25},
    "sentiment": {"S1": 0.3333, "S2": 0.3333, "S3": 0.3334},
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
            "S1": S1Calculator(data_manager, db),
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
                results[name] = result
            except Exception as e:
                results[name] = {"score": None, "status": "error"}
        return results

    def calculate_dimension_score(self, indicator_results: dict, dimension: str) -> float:
        weights = INDICATOR_WEIGHTS[dimension]
        scores = []
        for ind, weight in weights.items():
            result = indicator_results.get(ind, {})
            score = result.get("score") or result.get(ind) or result.get(ind.lower())
            if score is not None:
                scores.append((score, weight))
        if not scores:
            return 50.0
        total_weight = sum(w for _, w in scores)
        weighted_sum = sum(s * w for s, w in scores)
        return weighted_sum / total_weight

    def calculate_health(self, indicator_results: dict) -> float:
        import pandas as pd
        statuses = []
        for name in indicator_results:
            r = indicator_results[name]
            statuses.append({
                "indicator": name,
                "status": r.get("status", "missing"),
            })
        if not statuses:
            return 0
        return calculate_health_score(pd.DataFrame(statuses))

    def run(self, date: str) -> dict:
        indicator_results = self.run_all_indicators(date)

        dimension_scores = {}
        for dim in DIMENSION_WEIGHTS:
            dimension_scores[dim] = self.calculate_dimension_score(
                indicator_results, dim
            )

        raw_fgi = calculate_fgi(dimension_scores)

        all_scores = []
        for name, r in indicator_results.items():
            s = r.get(name.lower()) or r.get("score")
            if s is not None:
                all_scores.append(float(s))

        _, mad = apply_consistency_adjustment(raw_fgi, all_scores)
        self._db.upsert_raw_data(date, "mad", float(mad))
        self._db.commit()

        mad_history = self._db.get_raw_data("mad", "2015-01-01", date)
        if len(mad_history) > 252:
            import pandas as pd
            mad_series = pd.Series(mad_history["value"].values, index=mad_history["date"])
            mad_pct = rolling_percentile(mad_series, window=1260)
            mad_pct_val = mad_pct.iloc[-1]
            if not pd.isna(mad_pct_val):
                fgi_final = adjust_fgi_with_mad_pct(raw_fgi, mad, float(mad_pct_val))
            else:
                fgi_final = raw_fgi
        else:
            fgi_final = raw_fgi

        health = self.calculate_health(indicator_results)

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
