import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from fgi.storage.database import Database


class BacktestEngine:
    def __init__(self, db: Database):
        self._db = db

    def get_score_series(self, start_date: str, end_date: str) -> pd.DataFrame:
        return self._db.get_scores(start_date, end_date)

    def calculate_ic(self, scores: pd.DataFrame, forward_days: int = 5) -> Dict[str, float]:
        if len(scores) < forward_days + 1:
            return {"ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0}

        scores = scores.copy()
        scores["future_return"] = scores["FGI_final"].shift(-forward_days) / scores["FGI_final"] - 1
        scores = scores.dropna(subset=["future_return"])

        if len(scores) < 2:
            return {"ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0}

        ic = float(scores["FGI_final"].corr(scores["future_return"]))

        ic_values = []
        for i in range(len(scores) - 19):
            window = scores.iloc[i:i+20]
            if len(window) >= 10:
                corr = window["FGI_final"].corr(window["future_return"])
                if not pd.isna(corr):
                    ic_values.append(float(corr))

        if not ic_values:
            return {"ic_mean": ic, "ic_std": 0.0, "icir": 0.0}

        ic_mean = float(np.mean(ic_values))
        ic_std = float(np.std(ic_values))
        icir = ic_mean / ic_std if ic_std > 0 else 0.0

        return {"ic_mean": ic_mean, "ic_std": ic_std, "icir": icir}

    def layer_backtest(self, scores: pd.DataFrame, n_layers: int = 5,
                       holding_days: int = 5) -> Dict[str, List[float]]:
        if len(scores) < holding_days + 1:
            return {"layer_returns": [0.0] * n_layers}

        scores = scores.copy()
        scores["future_return"] = scores["FGI_final"].shift(-holding_days) / scores["FGI_final"] - 1
        scores = scores.dropna(subset=["future_return"])

        if len(scores) < n_layers:
            return {"layer_returns": [0.0] * n_layers}

        scores["layer"] = pd.qcut(scores["FGI_final"], n_layers, labels=False, duplicates="drop")
        layer_returns = []
        for layer in range(n_layers):
            layer_data = scores[scores["layer"] == layer]
            if len(layer_data) > 0:
                layer_returns.append(float(layer_data["future_return"].mean()))
            else:
                layer_returns.append(0.0)

        return {"layer_returns": layer_returns}

    def strategy_simulation(self, scores: pd.DataFrame, holding_days: int = 5,
                           threshold_high: float = 70, threshold_low: float = 30) -> Dict[str, float]:
        if len(scores) < holding_days + 1:
            return {"total_return": 0.0, "win_rate": 0.0, "sharpe": 0.0}

        scores = scores.copy()
        scores["future_return"] = scores["FGI_final"].shift(-holding_days) / scores["FGI_final"] - 1
        scores = scores.dropna(subset=["future_return"])

        if len(scores) == 0:
            return {"total_return": 0.0, "win_rate": 0.0, "sharpe": 0.0}

        signals = []
        for _, row in scores.iterrows():
            if row["FGI_final"] < threshold_low:
                signals.append(1.0)
            elif row["FGI_final"] > threshold_high:
                signals.append(-1.0)
            else:
                signals.append(0.0)

        scores["signal"] = signals
        scores["strategy_return"] = scores["signal"] * scores["future_return"]

        total_return = float((1 + scores["strategy_return"]).prod() - 1)
        win_rate = float((scores["strategy_return"] > 0).sum() / len(scores))
        sharpe = float(scores["strategy_return"].mean() / scores["strategy_return"].std() * np.sqrt(252)) if scores["strategy_return"].std() > 0 else 0.0

        return {"total_return": total_return, "win_rate": win_rate, "sharpe": sharpe}

    def run_full_backtest(self, start_date: str, end_date: str) -> Dict[str, any]:
        scores = self.get_score_series(start_date, end_date)
        if scores.empty:
            return {"error": "No data"}

        ic_result = self.calculate_ic(scores)
        layer_result = self.layer_backtest(scores)
        strategy_result = self.strategy_simulation(scores)

        return {
            "ic_analysis": ic_result,
            "layer_backtest": layer_result,
            "strategy_simulation": strategy_result,
            "data_points": len(scores),
        }
