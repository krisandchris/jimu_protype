"""WP-A4 rule judgments, evaluation reconciliation and AST summaries."""

from typing import Literal, Self, TypeAlias

from pydantic import StrictStr, model_validator

from .base import AssetVersionRef, NonNegativeInt, StrictModel, require_unique
from .evaluation import RuleTargetEvaluation

RuleJudgmentStatus: TypeAlias = Literal[
    "TRIGGERED", "NON_TRIGGERED", "UNCERTAIN", "FAILED"
]
ASTAggregationStatus: TypeAlias = Literal[
    "ALL_SUCCEEDED", "PARTIAL_FAILED", "ALL_FAILED"
]


class RuleJudgment(StrictModel):
    rule_ref: AssetVersionRef
    category_id: StrictStr
    target_instance_id: StrictStr
    status: RuleJudgmentStatus
    consumed_fact_refs: list[StrictStr]
    unavailable_fact_refs: list[StrictStr]
    rule_trace_ref: StrictStr
    error_code: StrictStr | None
    image_execution_id: StrictStr

    @model_validator(mode="after")
    def validate_error(self) -> Self:
        if self.status == "FAILED" and not self.error_code:
            raise ValueError("a failed judgment requires an error code")
        if self.status != "FAILED" and self.error_code is not None:
            raise ValueError("only a failed judgment can carry an error code")
        return self


class CategoryCoverage(StrictModel):
    active_count: NonNegativeInt
    triggered_count: NonNegativeInt
    non_triggered_count: NonNegativeInt
    uncertain_count: NonNegativeInt
    failed_count: NonNegativeInt

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if not self.active_count:
            raise ValueError("zero evaluations use the Clean Frame path")
        if self.active_count != sum(
            (
                self.triggered_count,
                self.non_triggered_count,
                self.uncertain_count,
                self.failed_count,
            )
        ):
            raise ValueError("active count must equal the sum of the four partitions")
        return self

    @property
    def aggregation_status(self) -> ASTAggregationStatus:
        if not self.failed_count:
            return "ALL_SUCCEEDED"
        if self.failed_count == self.active_count:
            return "ALL_FAILED"
        return "PARTIAL_FAILED"


class TargetASTSummary(StrictModel):
    image_execution_id: StrictStr
    target_instance_id: StrictStr
    aggregation_status: ASTAggregationStatus
    triggered_categories: list[StrictStr]
    non_triggered_categories: list[StrictStr]
    uncertain_categories: list[StrictStr]
    failed_categories: list[StrictStr]
    rule_judgment_refs: list[StrictStr]
    category_coverage: CategoryCoverage

    @property
    def category_statuses(self) -> dict[str, RuleJudgmentStatus]:
        statuses: dict[str, RuleJudgmentStatus] = {}
        for status, categories in self._partitions():
            statuses.update((category, status) for category in categories)
        return statuses

    def _partitions(self) -> list[tuple[RuleJudgmentStatus, list[str]]]:
        return [
            ("TRIGGERED", self.triggered_categories),
            ("NON_TRIGGERED", self.non_triggered_categories),
            ("UNCERTAIN", self.uncertain_categories),
            ("FAILED", self.failed_categories),
        ]

    @model_validator(mode="after")
    def validate_partitions(self) -> Self:
        partitions = [categories for _, categories in self._partitions()]
        require_unique(
            (c for categories in partitions for c in categories), "AST category"
        )
        if any(categories != sorted(categories) for categories in partitions):
            raise ValueError("each category partition must be sorted")
        coverage = self.category_coverage
        if [len(p) for p in partitions] != [
            coverage.triggered_count,
            coverage.non_triggered_count,
            coverage.uncertain_count,
            coverage.failed_count,
        ]:
            raise ValueError("category partitions do not match coverage")
        if self.aggregation_status != coverage.aggregation_status:
            raise ValueError("AST aggregation status does not match failed partition")
        if len(self.rule_judgment_refs) != coverage.active_count:
            raise ValueError("one trace reference is required for each active category")
        require_unique(self.rule_judgment_refs, "rule trace reference")
        return self

    @classmethod
    def from_judgments(
        cls,
        image_execution_id: str,
        target_instance_id: str,
        evaluations: list[RuleTargetEvaluation],
        judgments: list[RuleJudgment],
    ) -> Self:
        require_unique((e.category_id for e in evaluations), "expected category")
        require_unique((e.rule_ref.asset_id for e in evaluations), "expected rule")
        if any(e.target_instance_id != target_instance_id for e in evaluations):
            raise ValueError("expected evaluation target mismatch")
        require_unique((j.category_id for j in judgments), "rule judgment")
        expected = {e.category_id: e.rule_ref for e in evaluations}
        if expected != {j.category_id: j.rule_ref for j in judgments}:
            raise ValueError("judgments do not match active category and rule versions")
        if any(
            (j.image_execution_id, j.target_instance_id)
            != (image_execution_id, target_instance_id)
            for j in judgments
        ):
            raise ValueError("rule judgment image or target identity mismatch")
        ordered = sorted(judgments, key=lambda j: j.category_id)
        partitions: dict[RuleJudgmentStatus, list[str]] = {
            "TRIGGERED": [],
            "NON_TRIGGERED": [],
            "UNCERTAIN": [],
            "FAILED": [],
        }
        for j in ordered:
            partitions[j.status].append(j.category_id)
        coverage = CategoryCoverage(
            active_count=len(ordered),
            triggered_count=len(partitions["TRIGGERED"]),
            non_triggered_count=len(partitions["NON_TRIGGERED"]),
            uncertain_count=len(partitions["UNCERTAIN"]),
            failed_count=len(partitions["FAILED"]),
        )
        return cls(
            image_execution_id=image_execution_id,
            target_instance_id=target_instance_id,
            aggregation_status=coverage.aggregation_status,
            triggered_categories=partitions["TRIGGERED"],
            non_triggered_categories=partitions["NON_TRIGGERED"],
            uncertain_categories=partitions["UNCERTAIN"],
            failed_categories=partitions["FAILED"],
            rule_judgment_refs=[j.rule_trace_ref for j in ordered],
            category_coverage=coverage,
        )
