"""情绪-估值决策矩阵。

基于 FGI 情绪分位与沪深300 PE/PB 5年滚动估值分位，输出 3×3 决策象限和软性建议。

象限布局：
                估值分位
              低估(<25)    合理(25-75)   高估(>75)
情绪  恐惧(<25)    强烈关注     关注         观望
分位  中性(25-75)  关注         中性         谨慎
      贪婪(>75)    观望         谨慎         强烈谨慎

设计原则：
- 软性建议（"建议关注"/"建议谨慎"），不构成投资指令
- 阈值 25/75 与 FGI_LEVELS 保持一致
- PE 与 PB 分位等权平均
- 数据缺失时返回 None，由调用方降级处理
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database

logger = logging.getLogger(__name__)


# 阈值（百分位为 0~1 小数；阈值用 0.25/0.75 与 FGI 25/75 等价概念对应）
SENTIMENT_LOW = 25.0   # FGI 0-100
SENTIMENT_HIGH = 75.0
VALUATION_LOW = 0.25   # 百分位 0-1
VALUATION_HIGH = 0.75
ROLLING_WINDOW_DAYS = 5 * 252  # 5 年


@dataclass(frozen=True)
class DecisionMatrix:
    """决策矩阵输出。"""
    fgi: Optional[float]
    sentiment_tier: str  # "恐惧" / "中性" / "贪婪"
    pe_pct: Optional[float]
    pb_pct: Optional[float]
    valuation_pct: Optional[float]
    valuation_tier: str  # "低估" / "合理" / "高估"
    quadrant: str  # 9 宫格之一，如 "强烈关注"
    advice: str  # 简短建议文本

    def to_dict(self) -> Dict:
        return {
            "fgi": self.fgi,
            "sentiment_tier": self.sentiment_tier,
            "pe_pct": self.pe_pct,
            "pb_pct": self.pb_pct,
            "valuation_pct": self.valuation_pct,
            "valuation_tier": self.valuation_tier,
            "quadrant": self.quadrant,
            "advice": self.advice,
        }


def _classify_sentiment(fgi: Optional[float]) -> Optional[str]:
    if fgi is None or pd.isna(fgi):
        return None
    if fgi < SENTIMENT_LOW:
        return "恐惧"
    if fgi > SENTIMENT_HIGH:
        return "贪婪"
    return "中性"


def _classify_valuation(pct: Optional[float]) -> Optional[str]:
    if pct is None or pd.isna(pct):
        return None
    if pct < VALUATION_LOW:
        return "低估"
    if pct > VALUATION_HIGH:
        return "高估"
    return "合理"


# 9 宫格：行=情绪(恐惧/中性/贪婪)，列=估值(低估/合理/高估)
_QUADRANT_TABLE = {
    ("恐惧", "低估"):   ("强烈关注", "情绪悲观+估值偏低，建议关注左侧机会"),
    ("恐惧", "合理"):   ("关注",     "情绪悲观但估值合理，建议观察"),
    ("恐惧", "高估"):   ("观望",     "情绪悲观且估值偏高，建议观望"),
    ("中性", "低估"):   ("关注",     "估值偏低，建议关注"),
    ("中性", "合理"):   ("中性",     "情绪与估值均居中，无明确信号"),
    ("中性", "高估"):   ("谨慎",     "估值偏高，建议谨慎"),
    ("贪婪", "低估"):   ("观望",     "情绪高涨但估值仍低，建议观望"),
    ("贪婪", "合理"):   ("谨慎",     "情绪高涨估值合理，建议谨慎"),
    ("贪婪", "高估"):   ("强烈谨慎", "情绪高涨+估值偏高，建议警惕右侧风险"),
}


def _lookup_quadrant(sentiment: str, valuation: str) -> tuple:
    return _QUADRANT_TABLE.get(
        (sentiment, valuation),
        ("未知", "数据不足"),
    )


def _compute_valuation_pct(db: Database, date_str: str) -> tuple:
    """读取 PE/PB 5 年滚动分位；优先用 backfill 的 pre-computed percentile。
    Returns: (pe_pct, pb_pct, valuation_pct) — 任一缺失返回 None。
    """
    # 优先用 pre-computed v1_pe_percentile / v1_pb_percentile
    pe_pct_df = db.get_raw_data("v1_pe_percentile", date_str, date_str)
    pb_pct_df = db.get_raw_data("v1_pb_percentile", date_str, date_str)
    pe_pct = float(pe_pct_df["value"].iloc[0]) if not pe_pct_df.empty else None
    pb_pct = float(pb_pct_df["value"].iloc[0]) if not pb_pct_df.empty else None

    if pe_pct is None or pb_pct is None:
        # fallback: 实时计算
        pe_pct, pb_pct = _compute_pct_realtime(db, date_str)

    if pe_pct is None or pb_pct is None:
        return None, None, None
    valuation_pct = (pe_pct + pb_pct) / 2.0
    return pe_pct, pb_pct, valuation_pct


def _compute_pct_realtime(db: Database, date_str: str) -> tuple:
    """从 raw PE/PB 数据实时计算分位（用于 percentile 未 pre-compute 的日期）。"""
    start = (pd.to_datetime(date_str) - pd.Timedelta(days=ROLLING_WINDOW_DAYS + 90)).strftime("%Y-%m-%d")
    pe_data = db.get_raw_data("v1_pe_ttm", start, date_str)
    pb_data = db.get_raw_data("v1_pb", start, date_str)
    if pe_data.empty or pb_data.empty:
        return None, None

    pe_data = pe_data.sort_values("date").reset_index(drop=True)
    pb_data = pb_data.sort_values("date").reset_index(drop=True)
    pe_series = pd.to_numeric(pe_data["value"], errors="coerce")
    pb_series = pd.to_numeric(pb_data["value"], errors="coerce")
    # rolling_percentile 要求至少 252 个非 NaN 值
    if pe_series.dropna().shape[0] < 252 or pb_series.dropna().shape[0] < 252:
        return None, None

    pe_pct_s = rolling_percentile(pe_series, window=ROLLING_WINDOW_DAYS)
    pb_pct_s = rolling_percentile(pb_series, window=ROLLING_WINDOW_DAYS)

    # 取最后一天的值（已排序，最后一天 == date_str）
    pe_pct = float(pe_pct_s.iloc[-1]) if not pd.isna(pe_pct_s.iloc[-1]) else None
    pb_pct = float(pb_pct_s.iloc[-1]) if not pd.isna(pb_pct_s.iloc[-1]) else None
    return pe_pct, pb_pct


def compute_decision_matrix(
    db: Database,
    date_str: str,
    fgi: Optional[float],
) -> Optional[DecisionMatrix]:
    """计算决策矩阵。

    Args:
        db: Database 实例
        date_str: 目标日期 YYYY-MM-DD
        fgi: 当日 FGI 值（None 表示缺失）

    Returns:
        DecisionMatrix 实例；估值或 FGI 任一缺失返回 None
    """
    try:
        pe_pct, pb_pct, val_pct = _compute_valuation_pct(db, date_str)
        sentiment_tier = _classify_sentiment(fgi)
        valuation_tier = _classify_valuation(val_pct)

        if sentiment_tier is None or valuation_tier is None:
            logger.warning(
                "decision_matrix: incomplete inputs (fgi=%s, pe_pct=%s, pb_pct=%s, val_pct=%s) on %s",
                fgi, pe_pct, pb_pct, val_pct, date_str,
            )
            return None

        quadrant, advice = _lookup_quadrant(sentiment_tier, valuation_tier)
        return DecisionMatrix(
            fgi=fgi,
            sentiment_tier=sentiment_tier,
            pe_pct=pe_pct,
            pb_pct=pb_pct,
            valuation_pct=val_pct,
            valuation_tier=valuation_tier,
            quadrant=quadrant,
            advice=advice,
        )
    except Exception as e:
        logger.exception("decision_matrix computation failed for %s: %s", date_str, e)
        return None
