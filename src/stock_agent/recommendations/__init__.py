"""Public investment-research recommendation API."""

from .engine import RECOMMENDATION_FORMULAS, RecommendationEngine
from .gates import evaluate_data_quality
from .models import (
    Confidence,
    DataQualityInput,
    DataQualityReport,
    EvidenceRef,
    QualityIssue,
    RecommendationAction,
    RecommendationPolicy,
    RecommendationRequest,
    RecommendationResult,
    RiskEvent,
    RiskSeverity,
    RuleEvaluation,
    ThesisStatus,
)

__all__ = [
    "Confidence",
    "DataQualityInput",
    "DataQualityReport",
    "EvidenceRef",
    "QualityIssue",
    "RECOMMENDATION_FORMULAS",
    "RecommendationAction",
    "RecommendationEngine",
    "RecommendationPolicy",
    "RecommendationRequest",
    "RecommendationResult",
    "RiskEvent",
    "RiskSeverity",
    "RuleEvaluation",
    "ThesisStatus",
    "evaluate_data_quality",
]
