"""End-to-end orchestration for a daily personal-research run."""

from .pipeline import PipelineOutcome, StockMonitoringPipeline, load_static_quote_provider
from .scenario import analyze_price_scenario

__all__ = [
    "PipelineOutcome",
    "StockMonitoringPipeline",
    "analyze_price_scenario",
    "load_static_quote_provider",
]
