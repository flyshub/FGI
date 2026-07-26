from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class DataSourceStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class DataSourceResult:
    data: pd.DataFrame | None
    status: DataSourceStatus
    source: str
    error: str | None = None


class DataSource(ABC):
    @abstractmethod
    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        pass

    @abstractmethod
    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        pass

    @abstractmethod
    def health_check(self) -> DataSourceStatus:
        pass
