import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "fgi.db"

DATA_DIR.mkdir(exist_ok=True)

AKSHARE_ENABLED = os.getenv("FGI_AKSHARE_ENABLED", "true").lower() == "true"
MOOTDX_ENABLED = os.getenv("FGI_MOOTDX_ENABLED", "true").lower() == "true"
TENCENT_ENABLED = os.getenv("FGI_TENCENT_ENABLED", "true").lower() == "true"

WEBHOOK_URL = os.getenv("FGI_WEBHOOK_URL", "")
WEBHOOK_TYPE = os.getenv("FGI_WEBHOOK_TYPE", "wecom")

LOOKBACK_YEARS = 5
LOOKBACK_START = "2015-01-01"
ROLLING_WINDOW_DAYS = 250
PERCENTILE_WINDOW_YEARS = 5

HEALTHY_THRESHOLD = 60
ANOMALY_PERCENTILE = 99
FGI_EXTREME_HIGH = 85
FGI_EXTREME_LOW = 15
MISSING_DAY_LIMIT = 5

FGI_VERSION = os.getenv("FGI_VERSION", "current")  # "current" or "legacy"
