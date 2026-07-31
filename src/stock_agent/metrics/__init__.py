"""Public financial-metric API."""

from .engine import FORMULA_REGISTRY_VERSION, MetricEngine, cagr, growth_rate
from .models import FinancialSnapshot, MetricResult, MetricsReport, MetricStatus

__all__ = [
    "FORMULA_REGISTRY_VERSION",
    "FinancialSnapshot",
    "MetricEngine",
    "MetricResult",
    "MetricsReport",
    "MetricStatus",
    "cagr",
    "growth_rate",
]
