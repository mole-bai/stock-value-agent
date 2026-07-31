"""Contracts for deterministic, evidence-backed investment research views."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from stock_agent.metrics.models import decimal_to_str, to_decimal
from stock_agent.valuation import ScenarioName, ValuationReport


class RecommendationAction(str, Enum):
    BUY_CANDIDATE = "buy_candidate"
    WAIT = "wait"
    HOLD = "hold"
    REDUCE_CANDIDATE = "reduce_candidate"
    RISK_AVOIDANCE = "risk_avoidance"
    NO_RECOMMENDATION = "no_recommendation"

    @property
    def label_zh(self) -> str:
        return {
            self.BUY_CANDIDATE: "买入候选",
            self.WAIT: "等待",
            self.HOLD: "持有",
            self.REDUCE_CANDIDATE: "减持候选",
            self.RISK_AVOIDANCE: "风险回避",
            self.NO_RECOMMENDATION: "无建议",
        }[self]


class Confidence(str, Enum):
    INSUFFICIENT = "insufficient"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def parse(cls, value: "Confidence | str") -> "Confidence":
        if isinstance(value, cls):
            return value
        aliases = {"数据不足": cls.INSUFFICIENT, "低": cls.LOW, "中": cls.MEDIUM, "高": cls.HIGH}
        return aliases.get(str(value), cls(str(value).lower()))

    @property
    def rank(self) -> int:
        return {
            self.INSUFFICIENT: 0,
            self.LOW: 1,
            self.MEDIUM: 2,
            self.HIGH: 3,
        }[self]


class ThesisStatus(str, Enum):
    VALID = "valid"
    UNKNOWN = "unknown"
    INVALID = "invalid"


class RiskSeverity(str, Enum):
    INFORMATION = "information"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    summary: str
    category: str = "fact"
    source_url: str | None = None
    observed_at: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceRef":
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "summary": self.summary,
            "category": self.category,
            "source_url": self.source_url,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class RiskEvent:
    code: str
    description: str
    severity: RiskSeverity
    evidence_ids: tuple[str, ...] = ()
    triggered: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.severity, RiskSeverity):
            object.__setattr__(self, "severity", RiskSeverity(str(self.severity).lower()))
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(self.evidence_ids)))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RiskEvent":
        return cls(
            code=str(payload["code"]),
            description=str(payload["description"]),
            severity=RiskSeverity(str(payload["severity"]).lower()),
            evidence_ids=tuple(payload.get("evidence_ids", ())),
            triggered=bool(payload.get("triggered", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "description": self.description,
            "severity": self.severity.value,
            "evidence_ids": list(self.evidence_ids),
            "triggered": self.triggered,
        }


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QualityIssue":
        return cls(
            code=str(payload["code"]),
            message=str(payload["message"]),
            evidence_ids=tuple(payload.get("evidence_ids", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class DataQualityInput:
    """Fail-closed quality assertions made by upstream normalisers.

    Defaults are intentionally false: omitting a check must not silently allow
    a positive investment view.
    """

    price_fresh: bool = False
    share_count_fresh: bool = False
    cash_fresh: bool = False
    debt_fresh: bool = False
    earnings_fresh: bool = False
    cash_flow_fresh: bool = False
    required_fields_present: bool = False
    source_conflicts_resolved: bool = False
    accounting_identity_valid: bool = False
    currency_consistent: bool = False
    periods_consistent: bool = False
    corporate_actions_resolved: bool = False
    material_event_pending: bool = True
    industry_model_applicable: bool = False
    extraction_confidence: Decimal = Decimal("0")
    minimum_extraction_confidence: Decimal = Decimal("0.80")
    additional_blockers: tuple[QualityIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "extraction_confidence",
            to_decimal(self.extraction_confidence, field_name="extraction_confidence"),
        )
        object.__setattr__(
            self,
            "minimum_extraction_confidence",
            to_decimal(self.minimum_extraction_confidence, field_name="minimum_extraction_confidence"),
        )
        if not Decimal("0") <= self.extraction_confidence <= Decimal("1"):
            raise ValueError("extraction_confidence must be between 0 and 1")
        if not Decimal("0") <= self.minimum_extraction_confidence <= Decimal("1"):
            raise ValueError("minimum_extraction_confidence must be between 0 and 1")
        object.__setattr__(
            self,
            "additional_blockers",
            tuple(
                issue if isinstance(issue, QualityIssue) else QualityIssue.from_dict(issue)
                for issue in self.additional_blockers
            ),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DataQualityInput":
        known = set(cls.__dataclass_fields__)
        unknown = set(payload) - known
        if unknown:
            raise ValueError(f"unknown DataQualityInput fields: {sorted(unknown)}")
        data = dict(payload)
        data["additional_blockers"] = tuple(
            item if isinstance(item, QualityIssue) else QualityIssue.from_dict(item)
            for item in data.get("additional_blockers", ())
        )
        return cls(**data)


@dataclass(frozen=True)
class DataQualityReport:
    passed: bool
    blockers: tuple[QualityIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "blockers": [issue.to_dict() for issue in self.blockers]}


@dataclass(frozen=True)
class RecommendationPolicy:
    version: str = "company_research_policy.v1"
    target_annual_return: Decimal = Decimal("0.12")
    minimum_hold_annual_return: Decimal = Decimal("0.06")
    required_margin_of_safety: Decimal = Decimal("0.25")
    minimum_confidence: Confidence = Confidence.MEDIUM
    require_cross_validation: bool = True

    def __post_init__(self) -> None:
        for name in (
            "target_annual_return",
            "minimum_hold_annual_return",
            "required_margin_of_safety",
        ):
            object.__setattr__(self, name, to_decimal(getattr(self, name), field_name=name))
        object.__setattr__(self, "minimum_confidence", Confidence.parse(self.minimum_confidence))
        if not Decimal("0") <= self.required_margin_of_safety < Decimal("1"):
            raise ValueError("required_margin_of_safety must be in [0, 1)")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecommendationPolicy":
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "target_annual_return": decimal_to_str(self.target_annual_return),
            "minimum_hold_annual_return": decimal_to_str(self.minimum_hold_annual_return),
            "required_margin_of_safety": decimal_to_str(self.required_margin_of_safety),
            "minimum_confidence": self.minimum_confidence.value,
            "require_cross_validation": self.require_cross_validation,
        }


@dataclass(frozen=True)
class RecommendationRequest:
    company_name: str
    valuation: ValuationReport
    data_quality: DataQualityInput
    confidence: Confidence
    thesis_status: ThesisStatus
    existing_position: bool = False
    investment_case_qualified: bool = True
    composite_score: Decimal | None = None
    minimum_buy_score: Decimal = Decimal("70")
    risk_events: tuple[RiskEvent, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    supporting_evidence_ids: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    valid_until: str | None = None
    next_review_date: str | None = None
    policy: RecommendationPolicy = field(default_factory=RecommendationPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.valuation, ValuationReport):
            object.__setattr__(self, "valuation", ValuationReport.from_dict(self.valuation))
        if not isinstance(self.data_quality, DataQualityInput):
            object.__setattr__(self, "data_quality", DataQualityInput.from_dict(self.data_quality))
        object.__setattr__(self, "confidence", Confidence.parse(self.confidence))
        object.__setattr__(
            self,
            "composite_score",
            to_decimal(self.composite_score, field_name="composite_score"),
        )
        object.__setattr__(
            self,
            "minimum_buy_score",
            to_decimal(self.minimum_buy_score, field_name="minimum_buy_score"),
        )
        if self.composite_score is not None and not Decimal("0") <= self.composite_score <= Decimal("100"):
            raise ValueError("composite_score must be between 0 and 100")
        if self.minimum_buy_score is None or not Decimal("0") <= self.minimum_buy_score <= Decimal("100"):
            raise ValueError("minimum_buy_score must be between 0 and 100")
        if not isinstance(self.thesis_status, ThesisStatus):
            object.__setattr__(self, "thesis_status", ThesisStatus(str(self.thesis_status).lower()))
        object.__setattr__(
            self,
            "risk_events",
            tuple(
                item if isinstance(item, RiskEvent) else RiskEvent.from_dict(item)
                for item in self.risk_events
            ),
        )
        object.__setattr__(
            self,
            "evidence",
            tuple(
                item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item)
                for item in self.evidence
            ),
        )
        object.__setattr__(self, "supporting_evidence_ids", tuple(dict.fromkeys(self.supporting_evidence_ids)))
        if not isinstance(self.policy, RecommendationPolicy):
            object.__setattr__(self, "policy", RecommendationPolicy.from_dict(self.policy))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecommendationRequest":
        return cls(
            company_name=str(payload["company_name"]),
            valuation=(
                payload["valuation"]
                if isinstance(payload["valuation"], ValuationReport)
                else ValuationReport.from_dict(payload["valuation"])
            ),
            data_quality=(
                payload["data_quality"]
                if isinstance(payload["data_quality"], DataQualityInput)
                else DataQualityInput.from_dict(payload["data_quality"])
            ),
            confidence=Confidence.parse(payload["confidence"]),
            thesis_status=ThesisStatus(str(payload["thesis_status"]).lower()),
            existing_position=bool(payload.get("existing_position", False)),
            investment_case_qualified=bool(payload.get("investment_case_qualified", True)),
            composite_score=payload.get("composite_score"),
            minimum_buy_score=payload.get("minimum_buy_score", "70"),
            risk_events=tuple(payload.get("risk_events", ())),
            evidence=tuple(payload.get("evidence", ())),
            supporting_evidence_ids=tuple(payload.get("supporting_evidence_ids", ())),
            catalysts=tuple(payload.get("catalysts", ())),
            risks=tuple(payload.get("risks", ())),
            invalidation_conditions=tuple(payload.get("invalidation_conditions", ())),
            valid_until=payload.get("valid_until"),
            next_review_date=payload.get("next_review_date"),
            policy=(
                payload.get("policy", RecommendationPolicy())
                if isinstance(payload.get("policy", RecommendationPolicy()), RecommendationPolicy)
                else RecommendationPolicy.from_dict(payload["policy"])
            ),
        )


@dataclass(frozen=True)
class RuleEvaluation:
    rule_id: str
    passed: bool
    actual: str
    threshold: str | None
    explanation: str
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "passed": self.passed,
            "actual": self.actual,
            "threshold": self.threshold,
            "explanation": self.explanation,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class RecommendationResult:
    security_id: str
    company_name: str
    as_of: str
    scope: str
    action: RecommendationAction
    confidence: Confidence
    currency: str
    current_price: Decimal
    scenario_values: Mapping[ScenarioName, Decimal] | None
    margin_of_safety: Decimal | None
    expected_annual_returns: Mapping[ScenarioName, Decimal | None] | None
    price_bands: Mapping[str, Decimal] | None
    valuation_suppressed_reason: str | None
    data_quality: DataQualityReport
    rationale: tuple[str, ...]
    rule_trace: tuple[RuleEvaluation, ...]
    risk_events: tuple[RiskEvent, ...]
    evidence: tuple[EvidenceRef, ...]
    evidence_ids: tuple[str, ...]
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    valid_until: str | None
    next_review_date: str | None
    policy: RecommendationPolicy
    formula_audit: Mapping[str, str]
    disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "company_name": self.company_name,
            "as_of": self.as_of,
            "scope": self.scope,
            "action": self.action.value,
            "action_label_zh": self.action.label_zh,
            "confidence": self.confidence.value,
            "currency": self.currency,
            "current_price": decimal_to_str(self.current_price),
            "scenario_values": (
                {name.value: decimal_to_str(value) for name, value in self.scenario_values.items()}
                if self.scenario_values is not None
                else None
            ),
            "margin_of_safety": decimal_to_str(self.margin_of_safety),
            "expected_annual_returns": (
                {
                    name.value: decimal_to_str(value)
                    for name, value in self.expected_annual_returns.items()
                }
                if self.expected_annual_returns is not None
                else None
            ),
            "price_bands": (
                {name: decimal_to_str(value) for name, value in self.price_bands.items()}
                if self.price_bands is not None
                else None
            ),
            "valuation_suppressed_reason": self.valuation_suppressed_reason,
            "data_quality": self.data_quality.to_dict(),
            "rationale": list(self.rationale),
            "rule_trace": [rule.to_dict() for rule in self.rule_trace],
            "risk_events": [event.to_dict() for event in self.risk_events],
            "evidence": [item.to_dict() for item in self.evidence],
            "evidence_ids": list(self.evidence_ids),
            "catalysts": list(self.catalysts),
            "risks": list(self.risks),
            "invalidation_conditions": list(self.invalidation_conditions),
            "valid_until": self.valid_until,
            "next_review_date": self.next_review_date,
            "policy": self.policy.to_dict(),
            "formula_audit": dict(self.formula_audit),
            "disclaimer": self.disclaimer,
        }
