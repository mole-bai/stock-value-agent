"""Fail-closed data-quality gate used before recommendation rules."""

from __future__ import annotations

from .models import DataQualityInput, DataQualityReport, QualityIssue


QUALITY_ASSERTIONS: tuple[tuple[str, str], ...] = (
    ("price_fresh", "price is missing or stale"),
    ("share_count_fresh", "diluted share count is missing or stale"),
    ("cash_fresh", "cash balance is missing or stale"),
    ("debt_fresh", "debt balance is missing or stale"),
    ("earnings_fresh", "key earnings data is missing or stale"),
    ("cash_flow_fresh", "cash-flow data is missing or stale"),
    ("required_fields_present", "one or more valuation-critical fields are missing"),
    ("source_conflicts_resolved", "material source conflicts remain unresolved"),
    ("accounting_identity_valid", "accounting identity validation failed"),
    ("currency_consistent", "currency or FX validation failed"),
    ("periods_consistent", "financial periods are inconsistent"),
    ("corporate_actions_resolved", "split, ADS ratio or corporate action is unresolved"),
    ("industry_model_applicable", "the selected valuation model is not applicable"),
)


def evaluate_data_quality(data: DataQualityInput) -> DataQualityReport:
    blockers: list[QualityIssue] = []
    for attribute, message in QUALITY_ASSERTIONS:
        if not getattr(data, attribute):
            blockers.append(QualityIssue(code=attribute, message=message))
    if data.material_event_pending:
        blockers.append(
            QualityIssue(
                code="material_event_pending",
                message="a new filing or material event is awaiting parsing and revaluation",
            )
        )
    if data.extraction_confidence < data.minimum_extraction_confidence:
        blockers.append(
            QualityIssue(
                code="extraction_confidence",
                message=(
                    f"extraction confidence {data.extraction_confidence} is below "
                    f"{data.minimum_extraction_confidence}"
                ),
            )
        )
    blockers.extend(data.additional_blockers)
    return DataQualityReport(passed=not blockers, blockers=tuple(blockers))
